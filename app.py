"""
Entry point for Vercel, which looks for a Flask app named `app` in app.py.
To run locally use:  python web.py
"""

from web import create_app

app = create_app()
