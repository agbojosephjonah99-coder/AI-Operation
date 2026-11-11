"""
Settings for the AI Operations Assistant.

Secrets and settings are read from the .env file that sits next to this
file. Variable names from the original version still work
(Airtable_Personal_Access_Token, Airtable_Base_ID, OPENAI_API_KEY,
OPENAI_MODEL).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _int(name, default):
    try:
        return int(_env(name, default=str(default)))
    except ValueError:
        return default


# --- Connections ---------------------------------------------------------
AIRTABLE_TOKEN = _env("Airtable_Personal_Access_Token", "AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = _env("Airtable_Base_ID", "AIRTABLE_BASE_ID")
OPENAI_MODEL = _env("OPENAI_MODEL", default="gpt-4.1-mini")
# Stripped, because a space or line break pasted after the key makes every
# OpenAI request fail with a misleading "Connection error."
OPENAI_API_KEY = _env("OPENAI_API_KEY")

# --- Speed ---------------------------------------------------------------
# How long downloaded records are reused before Airtable is asked again.
CACHE_SECONDS = _int("CACHE_SECONDS", 300)
# How long the list of tables and fields is reused.
SCHEMA_CACHE_SECONDS = _int("SCHEMA_CACHE_SECONDS", 900)

# --- Student reports -----------------------------------------------------
STUDENT_TABLE = _env("STUDENT_TABLE", default="Student Profile/Biodata")
STUDENT_NAME_FIELD = _env("STUDENT_NAME_FIELD", default="Name")
STUDENT_ID_FIELD = _env("STUDENT_ID_FIELD", default="Matric_Number")
MISSING_FLAGS_FIELD = _env("MISSING_FLAGS_FIELD", default="Missing Fields")

# --- Conversation memory -------------------------------------------------
# Number of earlier questions (with their answers) the assistant remembers.
MAX_HISTORY_TURNS = _int("MAX_HISTORY_TURNS", 20)

# --- Privacy -------------------------------------------------------------
PRIVACY_FILE = BASE_DIR / "privacy.json"
# Alternative to the file (handy on Vercel): the whole privacy.json as text.
PRIVACY_JSON = _env("PRIVACY_JSON")

# --- Web interface -------------------------------------------------------
# Vercel sets VERCEL=1. When deployed there, a password and secret key are required.
ON_VERCEL = bool(os.getenv("VERCEL"))
WEB_HOST = _env("WEB_HOST", default="127.0.0.1")
WEB_PORT = _int("WEB_PORT", 5000)
# If set, the web page asks for this password before it can be used.
APP_PASSWORD = _env("APP_PASSWORD")
SECRET_KEY = _env("SECRET_KEY")
