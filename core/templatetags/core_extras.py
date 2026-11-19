from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def clp(valor):
    """Formatea pesos chilenos: 12345 -> $12.345"""
    try:
        return "$" + f"{int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return valor


@register.filter
def clase_estado(estado):
    return {"SOLICITADO": "info", "EN_PREPARACION": "warn", "PREPARADO": "warn", "EN_ENVIO": "info", "ENTREGADO": "ok",
            "PENDIENTE": "warn", "APROBADA": "ok", "RECHAZADA": "bad", "FINALIZADA": "ok", "ENVIADA": "ok", "FALLIDA": "bad"}.get(estado, "info")


@register.simple_tag(takes_context=True)
def actual(context, *nombres):
    """Devuelve aria-current="page" cuando la página abierta es una de las indicadas (marca el enlace del menú)."""
    request = context.get("request")
    coincidencia = getattr(request, "resolver_match", None)
    return mark_safe('aria-current="page"') if coincidencia and coincidencia.url_name in nombres else ""
