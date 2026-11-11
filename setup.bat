@echo off
cd /d "%~dp0"
echo Setting up AI Operations Assistant...
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if exist .venv rmdir /s /q .venv
%PY% -m venv .venv || goto :error
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt || goto :error
if not exist .env copy .env.example .env >nul && echo Created .env - open it and add your keys.
echo.
echo Done. Next: run_web.bat (browser) or run_cli.bat (terminal).
pause
exit /b 0
:error
echo Setup failed. Make sure Python 3.10 or newer is installed from python.org.
pause
exit /b 1
