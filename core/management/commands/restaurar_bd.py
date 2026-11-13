"""Restauracion de un respaldo generado por respaldar_bd."""
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .respaldar_bd import buscar_binario, entorno_pg


class Command(BaseCommand):
    help = "Restaura un respaldo (.dump). ATENCIÓN: reemplaza los datos actuales de la base."

    def add_arguments(self, parser):
        parser.add_argument("archivo")
        parser.add_argument("--si-estoy-seguro", action="store_true", help="Confirma que se reemplazarán los datos actuales.")

    def handle(self, *args, **o):
        if not o["si_estoy_seguro"]:
            raise CommandError("Esto reemplaza los datos actuales. Repite el comando agregando --si-estoy-seguro.")
        db = settings.DATABASES["default"]
        cmd = [buscar_binario("pg_restore"), "-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"],
               "--clean", "--if-exists", "--no-owner", "-d", db["NAME"], o["archivo"]]
        r = subprocess.run(cmd, env=entorno_pg(db), capture_output=True, text=True)
        if r.returncode not in (0, 1):  # 1 = advertencias no criticas de pg_restore
            raise CommandError(f"Falló pg_restore: {r.stderr.strip()}")
        self.stdout.write(self.style.SUCCESS("Restauración finalizada. Revisa que la información sea la esperada y concilia los pagos pendientes."))
