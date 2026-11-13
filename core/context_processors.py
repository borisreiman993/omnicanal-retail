from django.conf import settings

from .permisos import rol_de


def tienda(request):
    user = getattr(request, "user", None)
    rol = rol_de(user) if user and user.is_authenticated else None
    perfil = getattr(user, "perfil", None) if user and user.is_authenticated else None
    return {
        "NOMBRE_TIENDA": settings.NOMBRE_TIENDA,
        "RESERVA_MINUTOS": settings.RESERVA_MINUTOS,
        "rol": rol,
        "perfil_actual": perfil,
        "es_cliente": rol == "CLIENTE",
        "es_admin": rol == "ADMINISTRADOR",
        "es_vendedor": rol == "VENDEDOR",
        "es_bodeguero": rol == "BODEGUERO",
    }
