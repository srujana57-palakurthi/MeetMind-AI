@echo off
rem =========================================================================
rem  run.bat - one click setup + start for Windows
rem  Double-click this file to install everything and start the app.
rem =========================================================================
cd /d "%~dp0"

echo.
echo [1/3] Creating the virtual environment (only the first time)...
if not exist venv (
    py -m venv venv
    if errorlevel 1 (
        echo ERROR: Python was not found. Install it from https://www.python.org/downloads/
        echo        and tick "Add python.exe to PATH" during setup.
        pause
        exit /b 1
    )
) else (
    echo       venv already exists - skipping.
)

echo.
echo [2/3] Installing the Python packages...
call venv\Scripts\activate.bat
python -m pip install --disable-pip-version-check -r requirements.txt

echo.
echo [3/3] Starting the app...
if not exist .env (
    copy .env.example .env >nul
    echo       Created .env from .env.example - check OLLAMA_URL / OLLAMA_MODEL there.
)

python app.py
pause
