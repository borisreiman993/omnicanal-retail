"""Procesos programados: reservas vencidas, pagos inciertos, finalizaciones pendientes y notificaciones."""
import time

from django.core.management.base import BaseCommand

from core import services


class Command(BaseCommand):
    help = "Ejecuta los procesos periódicos. Con --cada N se repite cada N segundos (Ctrl+C para detener)."

    def add_arguments(self, parser):
        parser.add_argument("--cada", type=int, default=0, help="Repetir cada N segundos (el informe propone 60).")

    def handle(self, *args, **o):
        while True:
            resumen = services.procesar_pendientes()
            self.stdout.write(f"{time.strftime('%H:%M:%S')} {resumen}")
            if not o["cada"]:
                break
            try:
                time.sleep(o["cada"])
            except KeyboardInterrupt:
                self.stdout.write("Detenido.")
                break
