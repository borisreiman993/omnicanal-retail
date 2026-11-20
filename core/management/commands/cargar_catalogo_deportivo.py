"""Carga el catálogo de ejemplo de OmniSport (artículos deportivos) con un stock inicial en cada sucursal."""
import csv
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import Existencia, Producto, Sucursal


class Command(BaseCommand):
    help = "Carga los productos deportivos de datos/productos_deportivos.csv y les da stock inicial de demostración."

    def add_arguments(self, parser):
        parser.add_argument("--archivo", default=str(Path(settings.BASE_DIR) / "datos" / "productos_deportivos.csv"))
        parser.add_argument("--sin-stock", action="store_true", help="Solo crea los productos, sin dejar stock en las sucursales.")
        parser.add_argument("--carpeta-fotos", default=str(Path(settings.BASE_DIR) / "datos" / "fotos"),
                            help="Carpeta con fotos nombradas por SKU (ej. ZAP-001.jpg). Por defecto datos/fotos.")
        parser.add_argument("--reemplazar-fotos", action="store_true",
                            help="Vuelve a asignar la foto aunque el producto ya tenga una.")
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
        self._asignar_fotos(Path(o["carpeta_fotos"]), o["archivo"], o["reemplazar_fotos"])
        if o["desactivar_ejemplos"]:
            n = Producto.objects.filter(sku__startswith="PROD-", activo=True).update(activo=False)
            self.stdout.write(f"Productos de ejemplo ocultados: {n}.")
        self.stdout.write(self.style.SUCCESS("Catálogo deportivo listo."))

    def _asignar_fotos(self, carpeta, archivo_csv, reemplazar):
        """Asigna a cada producto la foto datos/fotos/<SKU>.jpg|.jpeg|.png|.webp si existe."""
        if not carpeta.is_dir():
            self.stdout.write("Fotos: no hay carpeta de fotos, se omite.")
            return
        extensiones = (".jpg", ".jpeg", ".png", ".webp")
        por_sku = {}
        for ruta in carpeta.iterdir():
            if ruta.is_file() and ruta.suffix.lower() in extensiones:
                por_sku.setdefault(ruta.stem.strip().upper(), ruta)
        with open(archivo_csv, newline="", encoding="utf-8-sig") as f:
            skus = [fila["sku"].strip() for fila in csv.DictReader(f)]
        limite = settings.IMAGEN_PRODUCTO_MAX_MB * 1024 * 1024
        asignadas = omitidas = 0
        grandes = []
        for p in Producto.objects.filter(sku__in=skus):
            ruta = por_sku.get(p.sku.upper())
            if not ruta:
                continue
            if p.imagen and not reemplazar:
                omitidas += 1
                continue
            if ruta.stat().st_size > limite:
                grandes.append(ruta.name)
                continue
            with open(ruta, "rb") as f:
                p.imagen.save(f"{p.sku}{ruta.suffix.lower()}", File(f), save=True)
            asignadas += 1
        sin_foto = len(skus) - len([s for s in skus if s.upper() in por_sku])
        self.stdout.write(f"Fotos asignadas: {asignadas}. Ya tenían foto (no se tocan): {omitidas}. Productos sin archivo de foto: {sin_foto}.")
        for nombre in grandes:
            self.stdout.write(self.style.WARNING(f"  {nombre} pesa más de {settings.IMAGEN_PRODUCTO_MAX_MB} MB y se omitió."))
        sobrantes = sorted(k for k in por_sku if k not in {s.upper() for s in skus})
        if sobrantes:
            self.stdout.write(self.style.WARNING(f"  Fotos cuyo nombre no coincide con ningún SKU: {', '.join(sobrantes)}"))
