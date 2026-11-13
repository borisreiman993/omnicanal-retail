"""Respaldo de PostgreSQL con pg_dump (equivalente local a los respaldos administrados)."""
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def buscar_binario(nombre):
    candidatos = []
    if settings.PG_BIN:
        candidatos += [Path(settings.PG_BIN) / f"{nombre}.exe", Path(settings.PG_BIN) / nombre]
    for c in candidatos:
        if c.exists():
            return str(c)
    ruta = shutil.which(nombre)
    if ruta:
        return ruta
    raise CommandError(f"No se encontró {nombre}. Revisa PG_BIN en tu archivo .env (carpeta bin de PostgreSQL).")


def entorno_pg(db):
    env = os.environ.copy()
    env["PGPASSWORD"] = db.get("PASSWORD") or ""
    return env


class Command(BaseCommand):
    help = "Genera un respaldo de la base de datos en la carpeta backups/."

    def handle(self, *args, **o):
        db = settings.DATABASES["default"]
        settings.BACKUPS_DIR.mkdir(exist_ok=True)
        destino = settings.BACKUPS_DIR / f"{db['NAME']}_{datetime.now():%Y%m%d_%H%M%S}.dump"
        cmd = [buscar_binario("pg_dump"), "-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"], "-Fc", "-f", str(destino), db["NAME"]]
        r = subprocess.run(cmd, env=entorno_pg(db), capture_output=True, text=True)
        if r.returncode != 0:
            raise CommandError(f"Falló pg_dump: {r.stderr.strip()}")
        self.stdout.write(self.style.SUCCESS(f"Respaldo creado: {destino}"))
