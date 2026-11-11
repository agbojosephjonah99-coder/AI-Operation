"""
AI Operations Assistant - web version.

Run on your computer:
    python web.py           then open http://127.0.0.1:5000

Deploy to Vercel: see DEPLOY_VERCEL.md (Vercel loads `app` from app.py).

The server keeps no memory between requests, so it works on Vercel, where
each request may be handled by a different machine:
  - The conversation travels with each request as a signed "state" token
    (questions, the AI's lookups and counts - never record rows). The
    signature stops anyone editing it.
  - Result tables are sent to the browser, which builds CSV downloads itself.
  - The login is a signed cookie.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import zlib
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import config
import reports
from assistant import Session, build_tools, explain_error

MAX_STATE_BYTES = 400_000


# ---------------------------------------------------------------------------
# Signed conversation state
# ---------------------------------------------------------------------------

def _sign(key, data):
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def make_token(key, state):
    raw = zlib.compress(json.dumps(state, separators=(",", ":")).encode("utf-8"))
    data = base64.urlsafe_b64encode(raw)
    return f"{data.decode()}.{_sign(key, data)}"


def read_token(key, token):
    """Return the state in a token, or None if it is missing, too big or was altered."""
    if not token or not isinstance(token, str) or len(token) > MAX_STATE_BYTES or "." not in token:
        return None
    data, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(key, data.encode()), signature):
        return None
    try:
        return json.loads(zlib.decompress(base64.urlsafe_b64decode(data)))
    except (ValueError, zlib.error):
        return None


# ---------------------------------------------------------------------------

def create_app(tools=None, openai_client=None):
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.ON_VERCEL,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=1_000_000,
    )
    state_key = hashlib.sha256(b"state:" + app.secret_key.encode()).digest()
    shared = {"tools": tools}

    def get_tools():
        if shared["tools"] is None:
            shared["tools"] = build_tools()
        return shared["tools"]

    def session_from(body):
        chat = Session(get_tools(), openai_client=openai_client)
        chat.load_state(read_token(state_key, body.get("state")))
        return chat

    def respond(chat, reply):
        return jsonify({**reply.to_dict(), "state": make_token(state_key, chat.export_state())})

    def fail(error, status=500):
        message = explain_error(error) if isinstance(error, Exception) else str(error)
        if isinstance(error, Exception):
            app.logger.exception("Request failed")   # full details in Vercel's logs
        return jsonify({"error": message}), status

    @app.before_request
    def guard():
        if request.endpoint == "static":
            return None
        # Never serve student data from a public deployment without a password.
        if config.ON_VERCEL and (not config.APP_PASSWORD or not config.SECRET_KEY):
            message = ("Setup needed: add APP_PASSWORD and SECRET_KEY in Vercel "
                       "(Project Settings → Environment Variables), then redeploy.")
            if request.path.startswith("/api/"):
                return fail(message, 503)
            return render_template("login.html", error=message, disabled=True), 503
        if not config.APP_PASSWORD:
            return None
        if request.endpoint in ("login", "logout") or session.get("authed"):
            return None
        if request.path.startswith("/api/"):
            return fail("Please sign in again.", 401)
        return redirect(url_for("login"))

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if config.APP_PASSWORD and secrets.compare_digest(
                    request.form.get("password", ""), config.APP_PASSWORD):
                session.clear()
                session.permanent = True
                session["authed"] = True
                return redirect(url_for("index"))
            time.sleep(1)          # slows down password guessing
            error = "Wrong password."
        return render_template("login.html", error=error, disabled=False)

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        shortcuts = [
            ("passport", "Missing passports"),
            ("incomplete", "Incomplete profiles"),
            ("completeness", "Field completeness"),
            ("duplicates", "Duplicate matric numbers"),
            ("overview", "Base overview"),
        ]
        return render_template("index.html", shortcuts=shortcuts,
                               student_table=config.STUDENT_TABLE,
                               has_password=bool(config.APP_PASSWORD))

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        message = (body.get("message") or "").strip()
        if not message:
            return fail("Type a question first.", 400)
        try:
            conversation = session_from(body)
            return respond(conversation, conversation.ask(message))
        except Exception as error:
            return fail(error)

    @app.post("/api/report")
    def report():
        body = request.get_json(silent=True) or {}
        name = body.get("name", "")
        if name not in reports.SHORTCUTS:
            return fail(f"Unknown report '{name}'.", 400)
        report_name, table, field = reports.SHORTCUTS[name]
        field = body.get("field") or field
        table = body.get("table") or table
        try:
            conversation = session_from(body)
            return respond(conversation, conversation.run_report(report_name, table, field))
        except Exception as error:
            return fail(error)

    @app.post("/api/refresh")
    def refresh():
        try:
            get_tools().client.refresh()
            return jsonify({"ok": True})
        except Exception as error:
            return fail(error)

    return app


if __name__ == "__main__":
    app = create_app()
    host = config.WEB_HOST
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{config.WEB_PORT}"
    if host not in ("127.0.0.1", "localhost") and not config.APP_PASSWORD:
        print("WARNING: the app is open to your network without a password. "
              "Set APP_PASSWORD in .env.")
    print(f"AI Operations Assistant running at {url}  (Ctrl+C to stop)")
    try:
        from waitress import serve
        serve(app, host=host, port=config.WEB_PORT, threads=8)
    except ImportError:
        app.run(host=host, port=config.WEB_PORT, threaded=True)
