"""Carga sucursales, productos NEUTROS de ejemplo, existencias y un usuario por perfil."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import Existencia, Perfil, Producto, Sucursal

User = get_user_model()

SUCURSALES = [("Sucursal Centro", "Av. Principal 100"), ("Sucursal Norte", "Calle Norte 250"), ("Sucursal Sur", "Calle Sur 480")]


class Command(BaseCommand):
    help = "Carga datos de demostración (productos genéricos, sucursales y usuarios de prueba)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo2026!", help="Contraseña de los usuarios de demostración.")
        parser.add_argument("--reiniciar-stock", action="store_true", help="Vuelve a dejar el stock inicial de demostración.")

    def handle(self, *args, **opciones):
        sucursales = []
        for nombre, direccion in SUCURSALES:
            s, _ = Sucursal.objects.get_or_create(nombre=nombre, defaults={"direccion": direccion})
            sucursales.append(s)

        precios = [3990, 5990, 7990, 9990, 12990, 14990, 19990, 24990, 29990, 39990, 49990, 59990]
        for i, precio in enumerate(precios, start=1):
            p, _ = Producto.objects.get_or_create(
                sku=f"PROD-{i:03d}",
                defaults={"nombre": f"Producto de ejemplo {i:02d}", "descripcion": "Producto genérico de demostración.",
                          "precio": precio, "umbral_stock_critico": 5})
            for j, s in enumerate(sucursales):
                stock = (i * 3 + j * 7) % 25 + (2 if i % 5 else 4)
                if i == 12 and j == 2:
                    stock = 0  # un caso sin stock para la demostración
                e, creada = Existencia.objects.get_or_create(producto=p, sucursal=s, defaults={"stock_registrado": stock})
                if not creada and opciones["reiniciar_stock"]:
                    e.stock_registrado = max(stock, e.stock_reservado)
                    e.save(update_fields=["stock_registrado"])

        usuarios = [
            ("admin_demo", Perfil.ADMINISTRADOR, None, "Ana", "Administradora"),
            ("vendedor_centro", Perfil.VENDEDOR, sucursales[0], "Vicente", "Centro"),
            ("vendedor_norte", Perfil.VENDEDOR, sucursales[1], "Valentina", "Norte"),
            ("bodeguero_centro", Perfil.BODEGUERO, sucursales[0], "Bruno", "Centro"),
            ("bodeguero_norte", Perfil.BODEGUERO, sucursales[1], "Beatriz", "Norte"),
            ("cliente_demo", Perfil.CLIENTE, None, "Carla", "Cliente"),
        ]
        for username, rol, sucursal, nombre, apellido in usuarios:
            user, creado = User.objects.get_or_create(username=username, defaults={
                "first_name": nombre, "last_name": apellido, "email": f"{username}@example.com"})
            if creado:
                user.set_password(opciones["password"])
                user.save()
            Perfil.objects.get_or_create(user=user, defaults={"rol": rol, "sucursal": sucursal})

        self.stdout.write(self.style.SUCCESS("Datos de demostración cargados."))
        self.stdout.write("Usuarios: admin_demo, vendedor_centro, vendedor_norte, bodeguero_centro, bodeguero_norte, cliente_demo")
        self.stdout.write(f"Contraseña de demostración: {opciones['password']}  (solo para pruebas locales)")
