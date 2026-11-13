@echo off
REM Crea las tablas, carga los datos de demostracion (si faltan) y levanta el servidor
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python manage.py migrate
python manage.py cargar_datos_demo
python manage.py runserver
