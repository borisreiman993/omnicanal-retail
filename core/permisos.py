"""
RF-17 / RF-18: autorizacion verificada en el SERVIDOR para cada vista.
Ocultar botones en la interfaz no es un mecanismo de autorizacion: cada vista
restringida usa el decorador rol_requerido.
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def rol_de(user):
    perfil = getattr(user, "perfil", None)
    return perfil.rol if perfil else None


def rol_requerido(*roles):
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if rol_de(request.user) not in roles:
                from .services import registrar_actividad
                registrar_actividad(request.user, "ACCESO_DENEGADO", request.path, exito=False)
                raise PermissionDenied
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador


def solo_superusuario(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_superuser:
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return envoltura
