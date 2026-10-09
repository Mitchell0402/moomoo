@echo off
rem Start the dashboard by hand (normally it starts by itself at logon).
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" -m dashboard
timeout /t 3 /nobreak >nul
start "" http://localhost:8080/
