"""Mecanismo tecnico restringido para crear perfiles internos (Administrador, Vendedor, Bodeguero)."""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import Perfil, Sucursal

User = get_user_model()


class Command(BaseCommand):
    help = "Crea un usuario interno. Ejemplo: python manage.py crear_trabajador maria --rol VENDEDOR --sucursal \"Sucursal Centro\""

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--rol", required=True, choices=[Perfil.ADMINISTRADOR, Perfil.VENDEDOR, Perfil.BODEGUERO])
        parser.add_argument("--sucursal", help="Obligatoria para VENDEDOR y BODEGUERO.")
        parser.add_argument("--email", default="")
        parser.add_argument("--nombre", default="")
        parser.add_argument("--apellido", default="")
        parser.add_argument("--password", help="Si no se indica, se pedirá por teclado.")

    def handle(self, *args, **o):
        if User.objects.filter(username=o["username"]).exists():
            raise CommandError("Ya existe un usuario con ese nombre.")
        sucursal = None
        if o["rol"] in (Perfil.VENDEDOR, Perfil.BODEGUERO):
            if not o["sucursal"]:
                raise CommandError("Vendedores y bodegueros requieren --sucursal.")
            sucursal = Sucursal.objects.filter(nombre=o["sucursal"]).first()
            if not sucursal:
                raise CommandError("La sucursal indicada no existe.")
        clave = o["password"] or getpass("Contraseña: ")
        try:
            validate_password(clave)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages))
        user = User.objects.create_user(o["username"], o["email"], clave, first_name=o["nombre"], last_name=o["apellido"])
        Perfil.objects.create(user=user, rol=o["rol"], sucursal=sucursal)
        self.stdout.write(self.style.SUCCESS(f"Usuario {user.username} creado como {o['rol']}."))
