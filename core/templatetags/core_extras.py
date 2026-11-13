from django import template

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
