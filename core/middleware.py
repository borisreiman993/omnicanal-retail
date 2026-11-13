from django.conf import settings
from django.contrib.auth import logout
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

CLAVE_INICIO = "inicio_sesion_ts"


class SesionMaximaMiddleware:
    """Reautenticacion obligatoria al superar la duracion maxima de sesion (8 h)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            inicio = request.session.get(CLAVE_INICIO)
            if inicio is None:
                request.session[CLAVE_INICIO] = timezone.now().timestamp()
            else:
                horas = (timezone.now().timestamp() - inicio) / 3600
                if horas >= settings.SESION_DURACION_MAXIMA_HORAS:
                    logout(request)
                    messages.warning(request, "Tu sesión superó la duración máxima. Inicia sesión nuevamente.")
                    return redirect("login")
        return self.get_response(request)
