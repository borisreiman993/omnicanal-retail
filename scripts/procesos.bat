@echo off
REM Procesos programados: reservas vencidas, pagos inciertos y notificaciones (cada 60 segundos)
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python manage.py procesar_pendientes --cada 60
