"""
Patron Adapter para la pasarela de pagos (informe 6.4 / 8.3).

El resto del sistema solo conoce la interfaz ProcesadorPago. Hay dos adaptadores:
- AdaptadorPayPalSimulado: no sale a internet; sirve para demostrar y probar los flujos.
- AdaptadorPayPalSandbox: usa la API REST de PayPal Sandbox (requiere credenciales).
  NOTA: este adaptador esta escrito segun la documentacion de PayPal pero NO fue
  probado contra PayPal real en el desarrollo del proyecto.
"""
import base64
import json
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings


class ErrorProveedorPago(Exception):
    """El proveedor no pudo crear la orden (error cierto, no incierto)."""


@dataclass
class OrdenExterna:
    id: str
    url_aprobacion: str = ""


@dataclass
class ResultadoCaptura:
    estado: str  # COMPLETADA | RECHAZADA | INCIERTA
    id_orden: str = ""
    id_captura: str = ""
    importe: int = 0
    moneda: str = ""
    cuenta_receptora: str = ""


COMPLETADA = "COMPLETADA"
RECHAZADA = "RECHAZADA"
INCIERTA = "INCIERTA"


class ProcesadorPago(ABC):
    nombre = "base"
    requiere_redireccion = False

    @abstractmethod
    def crear_orden(self, referencia, importe, moneda):
        ...

    @abstractmethod
    def capturar(self, id_orden, esperado, modo=None):
        """esperado = {"importe": int, "moneda": str}. 'modo' solo lo usa el simulador."""

    @abstractmethod
    def consultar(self, id_orden, esperado):
        ...


class AdaptadorPayPalSimulado(ProcesadorPago):
    nombre = "simulado"

    def crear_orden(self, referencia, importe, moneda):
        return OrdenExterna(id=f"SIM-{uuid.uuid4().hex[:12].upper()}")

    def capturar(self, id_orden, esperado, modo=None):
        cuenta = settings.PAGO_CUENTA_RECEPTORA
        if modo == "aprobar":
            return ResultadoCaptura(COMPLETADA, id_orden, f"CAP-{id_orden}", esperado["importe"], esperado["moneda"], cuenta)
        if modo == "monto_distinto":  # solo para pruebas de la verificacion del servidor
            return ResultadoCaptura(COMPLETADA, id_orden, f"CAP-{id_orden}", esperado["importe"] + 1, esperado["moneda"], cuenta)
        if modo == "rechazar":
            return ResultadoCaptura(RECHAZADA, id_orden)
        return ResultadoCaptura(INCIERTA, id_orden)

    def consultar(self, id_orden, esperado):
        resolucion = settings.SIMULACION_INCIERTO
        cuenta = settings.PAGO_CUENTA_RECEPTORA
        if resolucion == "completada":
            return ResultadoCaptura(COMPLETADA, id_orden, f"CAP-{id_orden}", esperado["importe"], esperado["moneda"], cuenta)
        if resolucion == "rechazada":
            return ResultadoCaptura(RECHAZADA, id_orden)
        return ResultadoCaptura(INCIERTA, id_orden)


class AdaptadorPayPalSandbox(ProcesadorPago):
    nombre = "paypal_sandbox"
    requiere_redireccion = True
    BASE = "https://api-m.sandbox.paypal.com"

    def _token(self):
        credenciales = f"{settings.PAYPAL_CLIENT_ID}:{settings.PAYPAL_CLIENT_SECRET}".encode()
        req = urllib.request.Request(
            f"{self.BASE}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": "Basic " + base64.b64encode(credenciales).decode(),
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["access_token"]

    def _llamar(self, metodo, ruta, cuerpo=None, request_id=None):
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # idempotencia
        req = urllib.request.Request(f"{self.BASE}{ruta}", data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                                     headers=headers, method=metodo)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def _a_moneda_paypal(self, importe_clp):
        valor = importe_clp / settings.PAYPAL_CLP_POR_UNIDAD
        return f"{valor:.2f}"

    def crear_orden(self, referencia, importe, moneda):
        try:
            datos = self._llamar("POST", "/v2/checkout/orders", {
                "intent": "CAPTURE",
                "purchase_units": [{
                    "reference_id": str(referencia),
                    "amount": {"currency_code": settings.PAYPAL_MONEDA, "value": self._a_moneda_paypal(importe)},
                }],
            }, request_id=f"orden-{referencia}")
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            raise ErrorProveedorPago(str(exc)) from exc
        aprobar = next((l["href"] for l in datos.get("links", []) if l.get("rel") in ("approve", "payer-action")), "")
        return OrdenExterna(id=datos["id"], url_aprobacion=aprobar)

    def _interpretar(self, datos, id_orden, esperado):
        estado = datos.get("status")
        unidad = (datos.get("purchase_units") or [{}])[0]
        captura = ((unidad.get("payments") or {}).get("captures") or [{}])[0]
        if estado == "COMPLETED" and captura.get("status") == "COMPLETED":
            valor = float(captura["amount"]["value"])
            importe_clp = round(valor * settings.PAYPAL_CLP_POR_UNIDAD)
            return ResultadoCaptura(COMPLETADA, id_orden, captura.get("id", ""), importe_clp,
                                    esperado["moneda"] if captura["amount"]["currency_code"] == settings.PAYPAL_MONEDA else captura["amount"]["currency_code"],
                                    (unidad.get("payee") or {}).get("email_address", settings.PAGO_CUENTA_RECEPTORA))
        if estado in ("VOIDED", "DECLINED"):
            return ResultadoCaptura(RECHAZADA, id_orden)
        return ResultadoCaptura(INCIERTA, id_orden)

    def capturar(self, id_orden, esperado, modo=None):
        try:
            datos = self._llamar("POST", f"/v2/checkout/orders/{id_orden}/capture", {}, request_id=f"captura-{id_orden}")
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                return ResultadoCaptura(RECHAZADA, id_orden)
            return ResultadoCaptura(INCIERTA, id_orden)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return ResultadoCaptura(INCIERTA, id_orden)  # timeout: NO se interpreta como rechazo
        return self._interpretar(datos, id_orden, esperado)

    def consultar(self, id_orden, esperado):
        try:
            datos = self._llamar("GET", f"/v2/checkout/orders/{id_orden}")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return ResultadoCaptura(INCIERTA, id_orden)
        return self._interpretar(datos, id_orden, esperado)


def obtener_procesador():
    if settings.PAGO_PROVEEDOR == "paypal_sandbox":
        return AdaptadorPayPalSandbox()
    return AdaptadorPayPalSimulado()
