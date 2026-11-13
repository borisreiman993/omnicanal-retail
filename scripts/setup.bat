@echo off
REM Prepara el proyecto: entorno virtual, dependencias y archivo .env
cd /d "%~dp0.."
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if not exist .env ( copy .env.example .env )
echo.
echo Listo. Ahora abre el archivo .env y escribe tu contrasena de PostgreSQL en DB_PASSWORD.
echo Luego ejecuta: scripts\iniciar.bat
pause
