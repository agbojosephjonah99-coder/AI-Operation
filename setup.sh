#!/usr/bin/env bash
# Setup for macOS / Linux.  Usage: ./setup.sh
set -e
cd "$(dirname "$0")"
rm -rf .venv
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env - open it and add your keys."; }
echo "Done. Run:  .venv/bin/python web.py   (or agent.py for the terminal)"
