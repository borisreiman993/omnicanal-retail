"""Carga o actualiza productos desde un CSV (para cuando decidas que vender)."""
import csv

from django.core.management.base import BaseCommand, CommandError

from core.models import Producto


class Command(BaseCommand):
    help = "CSV con columnas: sku,nombre,descripcion,precio,umbral_stock_critico (la última y la descripción son opcionales)."

    def add_arguments(self, parser):
        parser.add_argument("archivo")

    def handle(self, *args, **o):
        creados = actualizados = 0
        try:
            with open(o["archivo"], newline="", encoding="utf-8-sig") as f:
                for n, fila in enumerate(csv.DictReader(f), start=2):
                    try:
                        sku, nombre, precio = fila["sku"].strip(), fila["nombre"].strip(), int(fila["precio"])
                    except (KeyError, ValueError, AttributeError):
                        raise CommandError(f"Fila {n}: revisa las columnas sku, nombre y precio (número entero).")
                    if not sku or not nombre or precio <= 0:
                        raise CommandError(f"Fila {n}: sku, nombre y precio son obligatorios y el precio debe ser mayor que cero.")
                    _, creado = Producto.objects.update_or_create(sku=sku, defaults={
                        "nombre": nombre, "descripcion": (fila.get("descripcion") or "").strip(), "precio": precio,
                        "umbral_stock_critico": int(fila.get("umbral_stock_critico") or 5)})
                    creados += creado
                    actualizados += (not creado)
        except FileNotFoundError:
            raise CommandError("No se encontró el archivo CSV.")
        self.stdout.write(self.style.SUCCESS(f"Productos creados: {creados}, actualizados: {actualizados}. El stock lo ingresa el Bodeguero."))
