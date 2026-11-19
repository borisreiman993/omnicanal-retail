"""Carga el catálogo de ejemplo de OmniSport (artículos deportivos) con un stock inicial en cada sucursal."""
import csv
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import Existencia, Producto, Sucursal


class Command(BaseCommand):
    help = "Carga los productos deportivos de datos/productos_deportivos.csv y les da stock inicial de demostración."

    def add_arguments(self, parser):
        parser.add_argument("--archivo", default=str(Path(settings.BASE_DIR) / "datos" / "productos_deportivos.csv"))
        parser.add_argument("--sin-stock", action="store_true", help="Solo crea los productos, sin dejar stock en las sucursales.")
        parser.add_argument("--desactivar-ejemplos", action="store_true",
                            help="Oculta del catálogo los 'Producto de ejemplo' genéricos (PROD-xxx); no se borra nada.")

    def handle(self, *args, **o):
        call_command("cargar_productos_csv", o["archivo"], stdout=self.stdout)
        if not o["sin_stock"]:
            sucursales = list(Sucursal.objects.filter(activa=True))
            if not sucursales:
                raise CommandError("No hay sucursales. Ejecuta primero: python manage.py cargar_datos_demo")
            nuevas = 0
            with open(o["archivo"], newline="", encoding="utf-8-sig") as f:
                skus = [fila["sku"].strip() for fila in csv.DictReader(f)]
            for i, p in enumerate(Producto.objects.filter(sku__in=skus).order_by("sku")):
                for j, s in enumerate(sucursales):
                    stock = (i * 5 + j * 9) % 22 + 3          # reparto fijo de demostración
                    if i % 11 == 7 and j == len(sucursales) - 1:
                        stock = 0                               # un caso "sin stock" para ver cómo se muestra
                    _, creada = Existencia.objects.get_or_create(producto=p, sucursal=s, defaults={"stock_registrado": stock})
                    nuevas += creada
            self.stdout.write(f"Existencias nuevas creadas: {nuevas} (las que ya existían no se tocan).")
        if o["desactivar_ejemplos"]:
            n = Producto.objects.filter(sku__startswith="PROD-", activo=True).update(activo=False)
            self.stdout.write(f"Productos de ejemplo ocultados: {n}.")
        self.stdout.write(self.style.SUCCESS("Catálogo deportivo listo. Las fotos se suben desde Productos (perfil Administrador)."))
