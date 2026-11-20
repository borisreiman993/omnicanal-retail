import io
import shutil
import tempfile
import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import (
    AvisoBodega, Existencia, HistorialEstadoPedido, MovimientoInventario, NotificacionCliente,
    OperacionCompra, Pedido, Perfil, Producto, ReservaStock, Sucursal, Venta,
)
from .services import ErrorNegocio, StockInsuficiente

User = get_user_model()
CLAVE = "Clave-Segura-2026"


def crear_usuario(username, rol, sucursal=None, email=None):
    u = User.objects.create_user(username, email or f"{username}@example.com", CLAVE, first_name=username.capitalize())
    Perfil.objects.create(user=u, rol=rol, sucursal=sucursal)
    return u


class BaseDatos(TestCase):
    """Dos sucursales, dos productos y un usuario por perfil."""

    def setUp(self):
        self.centro = Sucursal.objects.create(nombre="Centro")
        self.norte = Sucursal.objects.create(nombre="Norte")
        self.p1 = Producto.objects.create(sku="A-1", nombre="Producto A", precio=10000, umbral_stock_critico=3)
        self.p2 = Producto.objects.create(sku="B-1", nombre="Producto B", precio=5000, umbral_stock_critico=3)
        self.e1 = Existencia.objects.create(producto=self.p1, sucursal=self.centro, stock_registrado=10)
        self.e2 = Existencia.objects.create(producto=self.p2, sucursal=self.centro, stock_registrado=2)
        Existencia.objects.create(producto=self.p1, sucursal=self.norte, stock_registrado=5)
        self.admin = crear_usuario("admin1", Perfil.ADMINISTRADOR)
        self.vendedor = crear_usuario("vend1", Perfil.VENDEDOR, self.centro)
        self.bodeguero = crear_usuario("bod1", Perfil.BODEGUERO, self.centro)
        self.bodeguero_norte = crear_usuario("bod2", Perfil.BODEGUERO, self.norte)
        self.cliente = crear_usuario("cli1", Perfil.CLIENTE)
        self.cliente2 = crear_usuario("cli2", Perfil.CLIENTE)

    def refrescar(self, *existencias):
        for e in existencias:
            e.refresh_from_db()

    def comprar(self, cliente=None, items=None, modalidad="RETIRO", sucursal=None, direccion=""):
        cliente = cliente or self.cliente
        for pid, cant in (items or [(self.p1.id, 2)]):
            services.agregar_al_carrito(cliente, pid, cant)
        return services.iniciar_compra(cliente, (sucursal or self.centro).id, modalidad, direccion)


class TestVentaPresencial(BaseDatos):
    def test_venta_descuenta_stock_registra_movimiento_y_aviso(self):
        v = services.registrar_venta_presencial(self.vendedor, [(self.p1.id, 3), (self.p2.id, 1)])
        self.refrescar(self.e1, self.e2)
        self.assertEqual((self.e1.stock_registrado, self.e2.stock_registrado), (7, 1))
        self.assertEqual(v.total, 3 * 10000 + 5000)
        self.assertEqual(MovimientoInventario.objects.filter(venta=v).count(), 2)
        self.assertTrue(AvisoBodega.objects.filter(venta=v, sucursal=self.centro).exists())
        self.assertFalse(hasattr(v, "pedido"), "La venta presencial no genera un Pedido digital")

    def test_sin_stock_suficiente_se_rechaza_y_no_cambia_nada(self):
        with self.assertRaises(StockInsuficiente):
            services.registrar_venta_presencial(self.vendedor, [(self.p1.id, 2), (self.p2.id, 3)])
        self.refrescar(self.e1, self.e2)
        self.assertEqual((self.e1.stock_registrado, self.e2.stock_registrado), (10, 2))
        self.assertEqual(Venta.objects.count(), 0)

    def test_respeta_reservas_de_compras_digitales(self):
        services.agregar_al_carrito(self.cliente, self.p2.id, 2)
        services.iniciar_compra(self.cliente, self.centro.id, "RETIRO")  # reserva las 2 unidades de B
        with self.assertRaises(StockInsuficiente):
            services.registrar_venta_presencial(self.vendedor, [(self.p2.id, 1)])

    def test_solo_el_vendedor_puede_vender(self):
        with self.assertRaises(PermissionDenied):
            services.registrar_venta_presencial(self.bodeguero, [(self.p1.id, 1)])


class TestCarritoYCompraDigital(BaseDatos):
    def test_agregar_al_carrito_no_reserva(self):
        services.agregar_al_carrito(self.cliente, self.p1.id, 4)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_reservado, 0)

    def test_reserva_todo_o_nada(self):
        services.agregar_al_carrito(self.cliente, self.p1.id, 2)
        services.agregar_al_carrito(self.cliente, self.p2.id, 5)  # solo hay 2
        with self.assertRaises(StockInsuficiente):
            services.iniciar_compra(self.cliente, self.centro.id, "RETIRO")
        self.refrescar(self.e1, self.e2)
        self.assertEqual((self.e1.stock_reservado, self.e2.stock_reservado), (0, 0))
        self.assertEqual(OperacionCompra.objects.count(), 0)

    def test_despacho_exige_direccion(self):
        services.agregar_al_carrito(self.cliente, self.p1.id, 1)
        with self.assertRaises(ErrorNegocio):
            services.iniciar_compra(self.cliente, self.centro.id, "DESPACHO", "")

    def test_pago_aprobado_finaliza_la_compra(self):
        op = self.comprar(items=[(self.p1.id, 2), (self.p2.id, 1)], modalidad="DESPACHO", direccion="Calle 123")
        self.refrescar(self.e1, self.e2)
        self.assertEqual((self.e1.stock_reservado, self.e2.stock_reservado), (2, 1))
        op = services.procesar_pago(op.pk, modo="aprobar")
        self.assertEqual(op.estado, OperacionCompra.FINALIZADA)
        self.refrescar(self.e1, self.e2)
        self.assertEqual((self.e1.stock_registrado, self.e1.stock_reservado), (8, 0))
        self.assertEqual((self.e2.stock_registrado, self.e2.stock_reservado), (1, 0))
        pedido = op.venta.pedido
        self.assertEqual((pedido.estado, pedido.modalidad, pedido.direccion_despacho), ("SOLICITADO", "DESPACHO", "Calle 123"))
        self.assertEqual(op.venta.canal, Venta.DIGITAL)
        self.assertEqual(op.reserva.estado, ReservaStock.CONSUMIDA)
        self.assertTrue(NotificacionCliente.objects.filter(pedido=pedido, estado="PENDIENTE").exists())
        self.assertFalse(self.cliente.carrito.detalles.exists())

    def test_finalizacion_es_idempotente(self):
        op = self.comprar()
        services.procesar_pago(op.pk, modo="aprobar")
        services.procesar_pago(op.pk, modo="aprobar")  # repeticion tecnica
        services.finalizar_operacion(op.pk)
        self.assertEqual(Venta.objects.filter(operacion=op).count(), 1)
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(MovimientoInventario.objects.filter(tipo="VENTA_DIGITAL").count(), 1)
        self.assertEqual(NotificacionCliente.objects.count(), 1)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, 8)

    def test_pago_rechazado_libera_la_reserva(self):
        op = self.comprar()
        op = services.procesar_pago(op.pk, modo="rechazar")
        self.assertEqual(op.estado, OperacionCompra.RECHAZADA)
        self.refrescar(self.e1)
        self.assertEqual((self.e1.stock_registrado, self.e1.stock_reservado), (10, 0))
        self.assertEqual(Venta.objects.count(), 0)

    def test_pago_incierto_mantiene_reserva_aunque_pase_el_tiempo(self):
        op = self.comprar()
        op = services.procesar_pago(op.pk, modo="incierto")
        self.assertEqual(op.estado, OperacionCompra.PAGO_INCIERTO)
        ReservaStock.objects.filter(operacion=op).update(vence_en=timezone.now() - timedelta(minutes=30))
        with override_settings(SIMULACION_INCIERTO="incierta"):
            services.procesar_pendientes()
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionCompra.PAGO_INCIERTO)  # NO se libera por el paso del tiempo
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_reservado, 2)
        # el proveedor confirma luego la captura: se finaliza sin un nuevo cobro
        with override_settings(SIMULACION_INCIERTO="completada"):
            resumen = services.procesar_pendientes()
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionCompra.FINALIZADA)
        self.assertEqual(resumen["finalizadas"], 1)
        self.assertEqual(op.pago.intentos, 1, "No se inició una nueva captura")

    def test_reserva_vencida_sin_captura_se_libera_por_proceso_programado(self):
        op = self.comprar()
        ReservaStock.objects.filter(operacion=op).update(vence_en=timezone.now() - timedelta(minutes=1))
        resumen = services.procesar_pendientes()
        self.assertEqual(resumen["reservas_liberadas"], 1)
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionCompra.EXPIRADA)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_reservado, 0)
        with self.assertRaises(ErrorNegocio):
            services.procesar_pago(op.pk, modo="aprobar")  # no se puede pagar una reserva liberada

    def test_pagar_con_reserva_vencida_la_libera_y_avisa(self):
        op = self.comprar()
        ReservaStock.objects.filter(operacion=op).update(vence_en=timezone.now() - timedelta(seconds=5))
        with self.assertRaises(services.ReservaVencida):
            services.procesar_pago(op.pk, modo="aprobar")
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_reservado, 0)

    def test_captura_que_no_coincide_con_la_compra_queda_en_revision(self):
        op = self.comprar()
        op = services.procesar_pago(op.pk, modo="monto_distinto")
        self.assertEqual(op.estado, OperacionCompra.EN_REVISION)
        self.assertEqual(Venta.objects.count(), 0)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_reservado, 2)  # unidades protegidas

    def test_falla_local_tras_captura_no_recobra_y_se_reintenta(self):
        op = self.comprar()
        with mock.patch("core.services.finalizar_operacion", side_effect=RuntimeError("falla")):
            op = services.procesar_pago(op.pk, modo="aprobar")
        self.assertEqual(op.estado, OperacionCompra.PAGADA_PENDIENTE)
        self.assertEqual(op.pago.intentos, 1)
        services.procesar_pendientes()
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionCompra.FINALIZADA)
        self.assertEqual(op.pago.intentos, 1, "No se ejecutó una segunda captura")

    def test_nueva_compra_libera_la_reserva_abandonada(self):
        op1 = self.comprar(items=[(self.p1.id, 2)])
        self.comprar(items=[(self.p1.id, 1)])
        op1.refresh_from_db()
        self.assertEqual(op1.estado, OperacionCompra.EXPIRADA)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_reservado, 3)  # 2 del carrito acumulado + 1 nuevo; solo una reserva vigente
        self.assertEqual(OperacionCompra.objects.filter(estado="RESERVADA").count(), 1)


class TestPedidos(BaseDatos):
    def pedido(self, modalidad="RETIRO"):
        op = self.comprar(modalidad=modalidad, direccion="Calle 1" if modalidad == "DESPACHO" else "")
        return services.procesar_pago(op.pk, modo="aprobar").venta.pedido

    def test_retiro_no_pasa_por_envio(self):
        p = self.pedido("RETIRO")
        services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_PREPARACION")
        services.cambiar_estado_pedido(self.bodeguero, p.id, "PREPARADO")
        with self.assertRaises(ErrorNegocio):
            services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_ENVIO")
        p, cambio = services.cambiar_estado_pedido(self.bodeguero, p.id, "ENTREGADO")
        self.assertTrue(cambio)
        self.assertEqual(p.estado, "ENTREGADO")

    def test_despacho_exige_operador_antes_de_en_envio(self):
        p = self.pedido("DESPACHO")
        services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_PREPARACION")
        services.cambiar_estado_pedido(self.bodeguero, p.id, "PREPARADO")
        with self.assertRaises(ErrorNegocio):
            services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_ENVIO")
        services.registrar_operador(self.bodeguero, p.id, "Operador X", "555", "TRK1")
        services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_ENVIO")
        with self.assertRaises(ErrorNegocio):
            services.cambiar_estado_pedido(self.bodeguero, p.id, "PREPARADO")  # sin retrocesos
        services.cambiar_estado_pedido(self.bodeguero, p.id, "ENTREGADO")

    def test_no_se_permiten_saltos(self):
        p = self.pedido()
        with self.assertRaises(ErrorNegocio):
            services.cambiar_estado_pedido(self.bodeguero, p.id, "ENTREGADO")

    def test_repetir_la_misma_transicion_no_duplica_historial_ni_notificacion(self):
        p = self.pedido()
        services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_PREPARACION")
        _, cambio = services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_PREPARACION")
        self.assertFalse(cambio)
        self.assertEqual(HistorialEstadoPedido.objects.filter(pedido=p, estado_nuevo="EN_PREPARACION").count(), 1)
        self.assertEqual(NotificacionCliente.objects.filter(pedido=p).count(), 2)  # Solicitado + En preparacion

    def test_el_estado_no_modifica_inventario(self):
        p = self.pedido()
        self.refrescar(self.e1)
        antes = self.e1.stock_registrado
        services.cambiar_estado_pedido(self.bodeguero, p.id, "EN_PREPARACION")
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, antes)

    def test_bodeguero_de_otra_sucursal_no_puede_cambiar_estado(self):
        p = self.pedido()
        with self.assertRaises(ErrorNegocio):
            services.cambiar_estado_pedido(self.bodeguero_norte, p.id, "EN_PREPARACION")

    def test_cliente_y_vendedor_no_pueden_cambiar_estado(self):
        p = self.pedido()
        for u in (self.cliente, self.vendedor, self.admin):
            with self.assertRaises(PermissionDenied):
                services.cambiar_estado_pedido(u, p.id, "EN_PREPARACION")

    def test_falla_del_correo_no_revierte_el_pedido(self):
        p = self.pedido()
        with mock.patch("core.services.send_mail", side_effect=OSError("SMTP caido")):
            enviadas, fallidas = services.enviar_notificaciones_pendientes()
        self.assertEqual((enviadas, fallidas), (0, 1))
        p.refresh_from_db()
        self.assertEqual(p.estado, "SOLICITADO")
        n = NotificacionCliente.objects.get(pedido=p)
        self.assertEqual(n.estado, "FALLIDA")
        enviadas, _ = services.enviar_notificaciones_pendientes()  # reintento sobre la misma notificacion
        self.assertEqual(enviadas, 1)
        self.assertEqual(NotificacionCliente.objects.filter(pedido=p).count(), 1)
        self.assertEqual(len(mail.outbox), 1)


class TestInventario(BaseDatos):
    def test_ajuste_con_motivo_genera_movimiento(self):
        services.ajustar_inventario(self.bodeguero, self.p1.id, 12, "Conteo físico")
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, 12)
        m = MovimientoInventario.objects.get(tipo="AJUSTE")
        self.assertEqual((m.cantidad, m.stock_resultante, m.usuario), (2, 12, self.bodeguero))

    def test_ajuste_exige_motivo_y_no_puede_bajar_de_lo_reservado(self):
        with self.assertRaises(ErrorNegocio):
            services.ajustar_inventario(self.bodeguero, self.p1.id, 12, "  ")
        services.agregar_al_carrito(self.cliente, self.p1.id, 6)
        services.iniciar_compra(self.cliente, self.centro.id, "RETIRO")
        with self.assertRaises(ErrorNegocio):
            services.ajustar_inventario(self.bodeguero, self.p1.id, 5, "Diferencia")
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, 10)

    def test_solo_el_bodeguero_ajusta_y_solo_su_sucursal(self):
        for u in (self.vendedor, self.admin, self.cliente):
            with self.assertRaises(PermissionDenied):
                services.ajustar_inventario(u, self.p1.id, 1, "x")
        services.ajustar_inventario(self.bodeguero_norte, self.p1.id, 9, "Conteo")
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, 10)  # no toca Centro

    def test_restricciones_de_integridad_en_la_base(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Existencia.objects.filter(pk=self.e1.pk).update(stock_reservado=11)

    def test_stock_critico_usa_umbral_por_producto(self):
        criticos = {(e.producto_id, e.sucursal_id) for e in services.existencias_criticas()}
        self.assertIn((self.p2.id, self.centro.id), criticos)  # 2 <= 3
        self.assertNotIn((self.p1.id, self.centro.id), criticos)  # 10 > 3
        services.agregar_al_carrito(self.cliente, self.p1.id, 8)
        services.iniciar_compra(self.cliente, self.centro.id, "RETIRO")  # disponible baja a 2
        criticos = {(e.producto_id, e.sucursal_id) for e in services.existencias_criticas(self.centro)}
        self.assertIn((self.p1.id, self.centro.id), criticos)


class TestDevoluciones(BaseDatos):
    def venta(self, canal="DIGITAL"):
        if canal == "DIGITAL":
            op = self.comprar(items=[(self.p1.id, 3)])
            return services.procesar_pago(op.pk, modo="aprobar").venta
        return services.registrar_venta_presencial(self.vendedor, [(self.p1.id, 3)])

    def solicitud(self, venta, **kw):
        d = venta.detalles.first()
        datos = dict(canal_solicitud="PRESENCIAL", fundamento="RETRACTO", motivo="Cambio de opinión", items={d.id: 2},
                     fecha_recepcion=timezone.localdate() - timedelta(days=3))
        datos.update(kw)
        return services.crear_devolucion(self.vendedor, venta.id, datos["canal_solicitud"], datos["fundamento"], datos["motivo"],
                                         datos["items"], datos.get("antecedentes", ""), datos["fecha_recepcion"], datos.get("confirmacion", True))

    def test_se_acepta_solicitud_por_un_canal_distinto_al_de_la_compra(self):
        v = self.venta("DIGITAL")
        dev = self.solicitud(v, canal_solicitud="PRESENCIAL")
        self.assertEqual((dev.estado, v.canal, dev.canal_solicitud), ("PENDIENTE", "DIGITAL", "PRESENCIAL"))
        sug, _ = services.evaluar_plazos(dev)
        self.assertEqual(sug, "CUMPLE")

    def test_retracto_fuera_de_plazo_y_extension_a_90_dias(self):
        v = self.venta("DIGITAL")
        dev = self.solicitud(v, fecha_recepcion=timezone.localdate() - timedelta(days=20))
        self.assertEqual(services.evaluar_plazos(dev)[0], "NO_CUMPLE")
        dev2 = self.solicitud(v, fecha_recepcion=timezone.localdate() - timedelta(days=20), confirmacion=False,
                              items={v.detalles.first().id: 1})
        self.assertEqual(services.evaluar_plazos(dev2)[0], "CUMPLE")

    def test_retracto_no_aplica_a_compra_presencial(self):
        v = self.venta("PRESENCIAL")
        dev = self.solicitud(v)
        self.assertEqual(services.evaluar_plazos(dev)[0], "NO_CUMPLE")

    def test_politica_comercial_fuera_de_30_dias_no_es_rechazo_automatico(self):
        v = self.venta("PRESENCIAL")
        dev = self.solicitud(v, fundamento="POLITICA", fecha_recepcion=timezone.localdate() - timedelta(days=45))
        sug, texto = services.evaluar_plazos(dev)
        self.assertEqual(sug, "NO_CUMPLE")
        self.assertIn("no basta", texto)
        # el vendedor conserva la decision, que debe justificar
        dev = services.evaluar_devolucion(self.vendedor, dev.id, "APROBADA", "Corresponde garantía legal por falla")
        self.assertEqual(dev.estado, "APROBADA")

    def test_sin_antecedentes_permanece_pendiente_y_no_se_puede_rechazar(self):
        v = self.venta("DIGITAL")
        dev = self.solicitud(v, fecha_recepcion=None)
        self.assertEqual(services.evaluar_plazos(dev)[0], "PENDIENTE")
        with self.assertRaises(ErrorNegocio):
            services.evaluar_devolucion(self.vendedor, dev.id, "RECHAZADA", "No hay datos")
        services.evaluar_devolucion(self.vendedor, dev.id, "PENDIENTE", "Falta la fecha de recepción")
        dev.refresh_from_db()
        self.assertEqual(dev.estado, "PENDIENTE")

    def test_la_decision_exige_justificacion(self):
        dev = self.solicitud(self.venta("DIGITAL"))
        with self.assertRaises(ErrorNegocio):
            services.evaluar_devolucion(self.vendedor, dev.id, "APROBADA", "")

    def test_aprobar_no_repone_inventario_y_la_recepcion_solo_repone_las_aptas_sin_duplicar(self):
        v = self.venta("DIGITAL")
        dev = self.solicitud(v)  # 2 unidades
        services.evaluar_devolucion(self.vendedor, dev.id, "APROBADA", "Dentro del plazo de retracto")
        self.refrescar(self.e1)
        antes = self.e1.stock_registrado
        det = dev.detalles.first()
        with self.assertRaises(PermissionDenied):
            services.recibir_devolucion(self.vendedor, det.id, 1, 1)
        services.recibir_devolucion(self.bodeguero, det.id, 1, 1, "Sellado")
        services.recibir_devolucion(self.bodeguero, det.id, 1, 0, "Dañado")
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, antes + 1)  # solo la unidad apta
        with self.assertRaises(ErrorNegocio):
            services.recibir_devolucion(self.bodeguero, det.id, 1, 1)  # ya se recibieron las 2 aprobadas
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, antes + 1)
        self.assertEqual(MovimientoInventario.objects.filter(tipo="DEVOLUCION").count(), 1)

    def test_no_se_recibe_una_devolucion_no_aprobada(self):
        dev = self.solicitud(self.venta("DIGITAL"))
        with self.assertRaises(ErrorNegocio):
            services.recibir_devolucion(self.bodeguero, dev.detalles.first().id, 1, 1)

    def test_no_se_puede_devolver_mas_de_lo_comprado(self):
        v = self.venta("DIGITAL")
        self.solicitud(v)
        with self.assertRaises(ErrorNegocio):
            self.solicitud(v, items={v.detalles.first().id: 2})  # quedaba 1


class TestPermisosHTTP(BaseDatos):
    def entrar(self, usuario):
        c = Client()
        self.assertTrue(c.login(username=usuario.username, password=CLAVE))
        return c

    def test_usuario_anonimo_es_redirigido_al_login(self):
        for nombre in ("carrito", "productos", "venta_nueva", "inventario", "mis_pedidos", "monitoreo"):
            r = Client().get(reverse(nombre))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertIn("/ingresar/", r["Location"])

    def test_cada_perfil_solo_accede_a_sus_funciones(self):
        permitido = {
            "cliente": {"carrito", "mis_pedidos", "mis_notificaciones"},
            "admin": {"productos", "stock_critico"},
            "vendedor": {"venta_nueva", "ventas", "devoluciones"},
            "bodeguero": {"inventario", "pedidos_bodega", "avisos", "recepciones", "movimientos", "stock_critico"},
        }
        todas = {"carrito", "mis_pedidos", "mis_notificaciones", "productos", "stock_critico", "venta_nueva", "ventas",
                 "devoluciones", "inventario", "pedidos_bodega", "avisos", "recepciones", "movimientos", "monitoreo"}
        usuarios = {"cliente": self.cliente, "admin": self.admin, "vendedor": self.vendedor, "bodeguero": self.bodeguero}
        for perfil, user in usuarios.items():
            c = self.entrar(user)
            for nombre in todas:
                r = c.get(reverse(nombre))
                if nombre in permitido[perfil]:
                    self.assertEqual(r.status_code, 200, f"{perfil} deberia acceder a {nombre}")
                else:
                    self.assertEqual(r.status_code, 403, f"{perfil} NO deberia acceder a {nombre}")

    def test_el_servidor_rechaza_operaciones_directas_de_otro_perfil(self):
        c = self.entrar(self.cliente)
        self.assertEqual(c.post(reverse("inventario_ajustar", args=[self.p1.id]), {"nuevo_stock": 999, "motivo": "x"}).status_code, 403)
        self.assertEqual(c.post(reverse("producto_nuevo"), {"sku": "X", "nombre": "X", "precio": 1}).status_code, 403)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, 10)
        v = self.entrar(self.vendedor)
        self.assertEqual(v.post(reverse("inventario_ajustar", args=[self.p1.id]), {"nuevo_stock": 999, "motivo": "x"}).status_code, 403)

    def test_cliente_no_ve_pedidos_ajenos(self):
        op = self.comprar(cliente=self.cliente)
        pedido = services.procesar_pago(op.pk, modo="aprobar").venta.pedido
        self.assertEqual(self.entrar(self.cliente).get(reverse("mi_pedido", args=[pedido.id])).status_code, 200)
        self.assertEqual(self.entrar(self.cliente2).get(reverse("mi_pedido", args=[pedido.id])).status_code, 404)
        self.assertEqual(self.entrar(self.cliente2).get(reverse("pago", args=[op.referencia])).status_code, 404)

    def test_bodeguero_no_gestiona_pedidos_de_otra_sucursal_por_url(self):
        op = self.comprar()
        pedido = services.procesar_pago(op.pk, modo="aprobar").venta.pedido
        c = self.entrar(self.bodeguero_norte)
        self.assertEqual(c.get(reverse("pedido_bodega", args=[pedido.id])).status_code, 404)
        self.assertEqual(c.post(reverse("pedido_cambiar_estado", args=[pedido.id]), {"estado": "EN_PREPARACION"}).status_code, 404)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, "SOLICITADO")

    def test_logout_solo_por_post(self):
        c = self.entrar(self.cliente)
        self.assertEqual(c.get(reverse("logout")).status_code, 405)
        self.assertEqual(c.post(reverse("logout")).status_code, 302)

    def test_pantalla_de_inicio_de_cada_perfil(self):
        for u in (self.cliente, self.admin, self.vendedor, self.bodeguero):
            self.assertEqual(self.entrar(u).get(reverse("inicio")).status_code, 200, u.username)

    def test_catalogo_es_publico(self):
        r = Client().get(reverse("catalogo"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Producto A")


class TestLogin(BaseDatos):
    def test_credenciales_validas_e_invalidas(self):
        c = Client()
        r = c.post(reverse("login"), {"username": "cli1", "password": "mala"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Usuario o contraseña incorrectos")
        self.assertEqual(c.get(reverse("carrito")).status_code, 302)  # sigue sin acceso
        r = c.post(reverse("login"), {"username": "cli1", "password": CLAVE})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get(reverse("carrito")).status_code, 200)

    def test_bloqueo_tras_cinco_intentos_fallidos(self):
        c = Client()
        for _ in range(5):
            c.post(reverse("login"), {"username": "cli1", "password": "mala"})
        perfil = Perfil.objects.get(user=self.cliente)
        self.assertTrue(perfil.bloqueado)
        r = c.post(reverse("login"), {"username": "cli1", "password": CLAVE})  # aun con la clave correcta
        self.assertContains(r, "bloqueada temporalmente")
        self.assertEqual(c.get(reverse("carrito")).status_code, 302)
        Perfil.objects.filter(pk=perfil.pk).update(bloqueado_hasta=timezone.now() - timedelta(minutes=1))
        self.assertEqual(c.post(reverse("login"), {"username": "cli1", "password": CLAVE}).status_code, 302)

    def test_registro_crea_siempre_un_cliente(self):
        c = Client()
        r = c.post(reverse("registro"), {"username": "nuevo", "first_name": "N", "last_name": "N", "email": "n@example.com",
                                         "password1": "Clave-Nueva-2026", "password2": "Clave-Nueva-2026", "rol": "ADMINISTRADOR"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(User.objects.get(username="nuevo").perfil.rol, Perfil.CLIENTE)


class TestFlujoCompletoHTTP(BaseDatos):
    def test_compra_digital_de_extremo_a_extremo(self):
        c = Client()
        c.login(username="cli1", password=CLAVE)
        c.post(reverse("carrito_agregar"), {"producto_id": self.p1.id, "cantidad": 2, "volver": "/catalogo/"})
        r = c.post(reverse("checkout"), {"sucursal": self.centro.id, "modalidad": "DESPACHO", "direccion": "Calle 99"})
        self.assertEqual(r.status_code, 302)
        op = OperacionCompra.objects.get()
        self.assertContains(c.get(r["Location"]), "Pasarela de pago")
        # el navegador por si solo no completa la compra: solo la verificacion del servidor lo hace
        self.assertEqual(op.estado, OperacionCompra.RESERVADA)
        r = c.post(reverse("pago", args=[op.referencia]), {"modo": "aprobar"})
        op.refresh_from_db()
        self.assertEqual(op.estado, OperacionCompra.FINALIZADA)
        self.assertContains(c.get(reverse("pago", args=[op.referencia])), "Compra confirmada")
        pedido = op.venta.pedido
        b = Client()
        b.login(username="bod1", password=CLAVE)
        b.post(reverse("pedido_cambiar_estado", args=[pedido.id]), {"estado": "EN_PREPARACION"})
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, "EN_PREPARACION")
        self.assertContains(c.get(reverse("mi_pedido", args=[pedido.id])), "En preparación")

    def test_venta_presencial_por_la_interfaz(self):
        v = Client()
        v.login(username="vend1", password=CLAVE)
        datos = {"items-TOTAL_FORMS": 6, "items-INITIAL_FORMS": 0, "items-MIN_NUM_FORMS": 0, "items-MAX_NUM_FORMS": 1000,
                 "items-0-producto": self.p1.id, "items-0-cantidad": 2, "requiere_bodega": "on"}
        for i in range(1, 6):
            datos[f"items-{i}-producto"] = ""
            datos[f"items-{i}-cantidad"] = ""
        r = v.post(reverse("venta_nueva"), datos)
        self.assertEqual(r.status_code, 302)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_registrado, 8)
        datos["items-0-cantidad"] = 50
        r = v.post(reverse("venta_nueva"), datos)
        self.assertContains(r, "Stock insuficiente")


class TestConcurrencia(TransactionTestCase):
    """Verificacion real con PostgreSQL: bloqueo de filas sobre las ultimas unidades (RNF-INT-01)."""

    def setUp(self):
        self.suc = Sucursal.objects.create(nombre="Unica")
        self.prod = Producto.objects.create(sku="Z-1", nombre="Ultima unidad", precio=1000)
        self.exi = Existencia.objects.create(producto=self.prod, sucursal=self.suc, stock_registrado=1)
        self.vendedores = [crear_usuario(f"v{i}", Perfil.VENDEDOR, self.suc) for i in range(4)]
        self.clientes = [crear_usuario(f"c{i}", Perfil.CLIENTE) for i in range(4)]

    def _correr(self, tareas):
        resultados, barrera = [], threading.Barrier(len(tareas))

        def envoltorio(f):
            try:
                barrera.wait()
                resultados.append(("ok", f()))
            except (ErrorNegocio, PermissionDenied) as exc:
                resultados.append(("rechazo", str(exc)))
            except Exception as exc:  # cualquier otro error haria fallar la prueba
                resultados.append(("error", repr(exc)))
            finally:
                connection.close()

        hilos = [threading.Thread(target=envoltorio, args=(f,)) for f in tareas]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        return resultados

    def test_cuatro_ventas_simultaneas_de_la_ultima_unidad(self):
        res = self._correr([lambda v=v: services.registrar_venta_presencial(v, [(self.prod.id, 1)]) for v in self.vendedores])
        self.assertEqual([r[0] for r in res].count("ok"), 1, res)
        self.assertEqual([r[0] for r in res].count("error"), 0, res)
        self.exi.refresh_from_db()
        self.assertEqual(self.exi.stock_registrado, 0)
        self.assertEqual(Venta.objects.count(), 1)

    def test_venta_presencial_compite_con_reserva_digital(self):
        for c in self.clientes[:2]:
            services.agregar_al_carrito(c, self.prod.id, 1)
        tareas = [lambda: services.registrar_venta_presencial(self.vendedores[0], [(self.prod.id, 1)]),
                  lambda: services.iniciar_compra(self.clientes[0], self.suc.id, "RETIRO"),
                  lambda: services.iniciar_compra(self.clientes[1], self.suc.id, "RETIRO")]
        res = self._correr(tareas)
        self.assertEqual([r[0] for r in res].count("ok"), 1, res)
        self.assertEqual([r[0] for r in res].count("error"), 0, res)
        self.exi.refresh_from_db()
        self.assertLessEqual(self.exi.stock_reservado, self.exi.stock_registrado)
        self.assertEqual(self.exi.disponible, 0)

    def test_dos_pagos_simultaneos_de_la_misma_operacion_no_duplican_nada(self):
        services.agregar_al_carrito(self.clientes[0], self.prod.id, 1)
        op = services.iniciar_compra(self.clientes[0], self.suc.id, "RETIRO")
        res = self._correr([lambda: services.procesar_pago(op.pk, modo="aprobar") for _ in range(4)])
        self.assertEqual([r[0] for r in res].count("error"), 0, res)
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(MovimientoInventario.objects.filter(tipo="VENTA_DIGITAL").count(), 1)
        self.exi.refresh_from_db()
        self.assertEqual((self.exi.stock_registrado, self.exi.stock_reservado), (0, 0))
        op.refresh_from_db()
        self.assertEqual(op.pago.intentos, 1)


# --------------------------------------------------------------------------- rediseño OmniSport

def png_de_prueba(lado=20):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (lado, lado), "red").save(buf, "PNG")
    return buf.getvalue()


class TestCatalogoFiltros(BaseDatos):
    def setUp(self):
        super().setUp()
        self.p3 = Producto.objects.create(sku="C-1", nombre="Zapatillas Cumbre", precio=60000)
        Existencia.objects.create(producto=self.p3, sucursal=self.norte, stock_registrado=4)

    def nombres(self, **params):
        r = self.client.get(reverse("catalogo"), params)
        self.assertEqual(r.status_code, 200)
        return [f["producto"].nombre for f in r.context["filas"]]

    def test_sin_filtros_muestra_solo_productos_activos_ordenados_por_nombre(self):
        Producto.objects.create(sku="X-1", nombre="Oculto", precio=1000, activo=False)
        self.assertEqual(self.nombres(), ["Producto A", "Producto B", "Zapatillas Cumbre"])

    def test_buscar_por_nombre_o_codigo(self):
        self.assertEqual(self.nombres(q="zapat"), ["Zapatillas Cumbre"])
        self.assertEqual(self.nombres(q="b-1"), ["Producto B"])

    def test_rango_de_precio(self):
        self.assertEqual(self.nombres(precio_min=6000, precio_max=20000), ["Producto A"])

    def test_orden_por_precio(self):
        self.assertEqual(self.nombres(orden="precio_asc"), ["Producto B", "Producto A", "Zapatillas Cumbre"])
        self.assertEqual(self.nombres(orden="precio_desc"), ["Zapatillas Cumbre", "Producto A", "Producto B"])

    def test_solo_con_stock_usa_disponible_y_no_solo_el_registrado(self):
        # B tiene 2 en Centro; se reservan las 2 -> disponible 0 -> no aparece
        Existencia.objects.filter(pk=self.e2.pk).update(stock_reservado=2)
        self.assertEqual(self.nombres(disponibles="on"), ["Producto A", "Zapatillas Cumbre"])

    def test_solo_con_stock_respeta_la_sucursal_elegida(self):
        self.assertEqual(self.nombres(disponibles="on", sucursal=self.norte.id), ["Producto A", "Zapatillas Cumbre"])
        self.assertEqual(self.nombres(disponibles="on", sucursal=self.centro.id), ["Producto A", "Producto B"])

    def test_la_sucursal_elegida_limita_las_columnas_de_disponibilidad(self):
        r = self.client.get(reverse("catalogo"), {"sucursal": self.norte.id})
        self.assertEqual([d["sucursal"] for d in r.context["filas"][0]["disponibilidad"]], [self.norte])

    def test_filtro_invalido_no_rompe_la_pagina(self):
        r = self.client.get(reverse("catalogo"), {"precio_min": "-5", "orden": "inventado"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["filas"]), 3)

    def test_paginacion_conserva_los_filtros(self):
        for i in range(30):
            Producto.objects.create(sku=f"M-{i:02d}", nombre=f"Masivo {i:02d}", precio=1000 + i)
        r = self.client.get(reverse("catalogo"), {"q": "Masivo"})
        self.assertEqual(len(r.context["filas"]), 24)
        self.assertContains(r, "q=Masivo&amp;page=2")
        r2 = self.client.get(reverse("catalogo"), {"q": "Masivo", "page": 2})
        self.assertEqual(len(r2.context["filas"]), 6)

    def test_el_boton_agregar_solo_aparece_para_clientes_y_con_stock(self):
        r = self.client.get(reverse("catalogo"))
        self.assertNotContains(r, "Agregar</button>")
        self.assertContains(r, "para comprar")
        self.client.force_login(self.cliente)
        self.assertContains(self.client.get(reverse("catalogo")), "Agregar</button>")
        self.client.force_login(self.vendedor)
        self.assertNotContains(self.client.get(reverse("catalogo")), "Agregar</button>")


class TestDetalleProducto(BaseDatos):
    def test_detalle_muestra_disponibilidad_por_sucursal(self):
        r = self.client.get(reverse("producto_detalle", args=[self.p1.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "10 unidades")
        self.assertContains(r, "5 unidades")

    def test_producto_inactivo_da_404(self):
        self.p1.activo = False
        self.p1.save()
        self.assertEqual(self.client.get(reverse("producto_detalle", args=[self.p1.id])).status_code, 404)


class TestCarritoCantidad(BaseDatos):
    def setUp(self):
        super().setUp()
        self.det = services.agregar_al_carrito(self.cliente, self.p1.id, 2)
        self.client.force_login(self.cliente)

    def poner(self, cantidad, detalle=None):
        return self.client.post(reverse("carrito_cantidad", args=[(detalle or self.det).id]), {"cantidad": cantidad}, follow=True)

    def test_cambiar_cantidad_no_reserva_stock(self):
        self.poner(5)
        self.det.refresh_from_db()
        self.assertEqual(self.det.cantidad, 5)
        self.refrescar(self.e1)
        self.assertEqual(self.e1.stock_reservado, 0)

    def test_cantidad_cero_quita_la_linea(self):
        self.poner(0)
        self.assertFalse(type(self.det).objects.filter(pk=self.det.pk).exists())

    def test_cantidad_sobre_el_maximo_se_rechaza(self):
        r = self.poner(services.CANTIDAD_MAXIMA_CARRITO + 1)
        self.det.refresh_from_db()
        self.assertEqual(self.det.cantidad, 2)
        self.assertContains(r, "cantidad máxima")

    def test_cantidad_no_numerica_se_rechaza(self):
        r = self.poner("abc")
        self.det.refresh_from_db()
        self.assertEqual(self.det.cantidad, 2)
        self.assertContains(r, "cantidad válida")

    def test_no_se_puede_modificar_el_carrito_de_otro_cliente(self):
        otro = services.agregar_al_carrito(self.cliente2, self.p2.id, 1)
        self.poner(9, detalle=otro)
        otro.refresh_from_db()
        self.assertEqual(otro.cantidad, 1)

    def test_solo_los_clientes_y_solo_por_post(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.post(reverse("carrito_cantidad", args=[self.det.id]), {"cantidad": 3}).status_code, 403)
        self.client.force_login(self.cliente)
        self.assertEqual(self.client.get(reverse("carrito_cantidad", args=[self.det.id])).status_code, 405)

    def test_el_icono_del_carrito_cuenta_las_unidades(self):
        r = self.client.get(reverse("catalogo"))
        self.assertContains(r, 'class="badge" aria-hidden="true">2<')


class TestFotosDeProductos(BaseDatos):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.client.force_login(self.admin)

    def datos(self, **extra):
        base = {"sku": "FOTO-1", "nombre": "Zapatilla con foto", "descripcion": "", "precio": 30000, "umbral_stock_critico": 5, "activo": "on"}
        base.update(extra)
        return base

    def test_el_administrador_sube_una_foto_y_se_muestra(self):
        with override_settings(MEDIA_ROOT=self.media):
            foto = SimpleUploadedFile("zapatilla.png", png_de_prueba(), content_type="image/png")
            r = self.client.post(reverse("producto_nuevo"), self.datos(imagen=foto))
            self.assertEqual(r.status_code, 302)
            p = Producto.objects.get(sku="FOTO-1")
            self.assertTrue(p.imagen.name.startswith("productos/"))
            html = self.client.get(reverse("catalogo")).content.decode()
            self.assertIn(f'alt="{p.nombre}"', html)
            self.assertIn("/media/productos/", html)

    def test_sin_foto_se_muestra_un_marcador_con_texto_alternativo(self):
        html = self.client.get(reverse("catalogo")).content.decode()
        self.assertIn("Producto A, sin foto", html)

    def test_un_archivo_que_no_es_imagen_se_rechaza(self):
        with override_settings(MEDIA_ROOT=self.media):
            falso = SimpleUploadedFile("virus.png", b"esto no es una imagen", content_type="image/png")
            r = self.client.post(reverse("producto_nuevo"), self.datos(imagen=falso))
            self.assertEqual(r.status_code, 200)
            self.assertFalse(Producto.objects.filter(sku="FOTO-1").exists())

    def test_una_foto_demasiado_pesada_se_rechaza(self):
        with override_settings(MEDIA_ROOT=self.media, IMAGEN_PRODUCTO_MAX_MB=0):
            foto = SimpleUploadedFile("grande.png", png_de_prueba(), content_type="image/png")
            r = self.client.post(reverse("producto_nuevo"), self.datos(imagen=foto))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "pesa demasiado")

    def test_editar_sin_cambiar_la_foto_la_conserva(self):
        with override_settings(MEDIA_ROOT=self.media):
            foto = SimpleUploadedFile("z.png", png_de_prueba(), content_type="image/png")
            self.client.post(reverse("producto_nuevo"), self.datos(imagen=foto))
            p = Producto.objects.get(sku="FOTO-1")
            nombre = p.imagen.name
            self.client.post(reverse("producto_editar", args=[p.id]), self.datos(nombre="Nombre nuevo"))
            p.refresh_from_db()
            self.assertEqual((p.nombre, p.imagen.name), ("Nombre nuevo", nombre))

    def test_otros_perfiles_no_pueden_subir_fotos(self):
        self.client.force_login(self.bodeguero)
        self.assertEqual(self.client.post(reverse("producto_nuevo"), self.datos()).status_code, 403)


class TestCatalogoDeportivo(BaseDatos):
    def test_carga_productos_y_stock_sin_duplicar(self):
        call_command("cargar_catalogo_deportivo", stdout=io.StringIO())
        n = Producto.objects.filter(sku__startswith="ZAP-").count()
        self.assertGreater(n, 0)
        self.assertTrue(Existencia.objects.filter(producto__sku__startswith="ZAP-", sucursal=self.centro, stock_registrado__gt=0).exists())
        total = (Producto.objects.count(), Existencia.objects.count())
        call_command("cargar_catalogo_deportivo", stdout=io.StringIO())
        self.assertEqual((Producto.objects.count(), Existencia.objects.count()), total)

    def test_no_pisa_el_stock_que_ya_existe(self):
        call_command("cargar_catalogo_deportivo", stdout=io.StringIO())
        e = Existencia.objects.get(producto__sku="ZAP-001", sucursal=self.centro)
        Existencia.objects.filter(pk=e.pk).update(stock_registrado=77)
        call_command("cargar_catalogo_deportivo", stdout=io.StringIO())
        e.refresh_from_db()
        self.assertEqual(e.stock_registrado, 77)

    def test_desactivar_ejemplos_oculta_los_productos_genericos(self):
        Producto.objects.create(sku="PROD-001", nombre="Producto de ejemplo 01", precio=1000)
        call_command("cargar_catalogo_deportivo", "--desactivar-ejemplos", stdout=io.StringIO())
        self.assertFalse(Producto.objects.get(sku="PROD-001").activo)
        self.assertTrue(Producto.objects.get(sku="ZAP-001").activo)


class TestFotosPorSku(BaseDatos):
    PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
           b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.fotos = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        self.addCleanup(shutil.rmtree, self.fotos, True)

    def cargar(self, *extra):
        salida = io.StringIO()
        with override_settings(MEDIA_ROOT=self.media):
            call_command("cargar_catalogo_deportivo", "--carpeta-fotos", self.fotos, *extra, stdout=salida)
        return salida.getvalue()

    def foto(self, nombre, contenido=None):
        with open(f"{self.fotos}/{nombre}", "wb") as f:
            f.write(contenido or self.PNG)

    def test_asigna_la_foto_cuyo_nombre_es_el_sku(self):
        self.foto("zap-001.PNG")
        texto = self.cargar()
        p = Producto.objects.get(sku="ZAP-001")
        self.assertTrue(p.imagen.name.startswith("productos/ZAP-001"))
        self.assertFalse(Producto.objects.get(sku="ZAP-002").imagen)
        self.assertIn("Fotos asignadas: 1", texto)

    def test_no_reemplaza_una_foto_existente_salvo_que_se_pida(self):
        self.foto("ZAP-001.png")
        self.cargar()
        antes = Producto.objects.get(sku="ZAP-001").imagen.name
        self.foto("ZAP-001.png", self.PNG + b"x")
        self.assertIn("Fotos asignadas: 0", self.cargar())
        self.assertEqual(Producto.objects.get(sku="ZAP-001").imagen.name, antes)
        self.assertIn("Fotos asignadas: 1", self.cargar("--reemplazar-fotos"))

    def test_avisa_de_fotos_con_nombre_que_no_coincide_y_de_las_muy_pesadas(self):
        self.foto("ZAPATO-XX.png")
        self.foto("ZAP-002.png")
        with override_settings(IMAGEN_PRODUCTO_MAX_MB=0):
            texto = self.cargar()
        self.assertIn("ZAPATO-XX", texto)
        self.assertIn("ZAP-002.png pesa más", texto)
        self.assertFalse(Producto.objects.get(sku="ZAP-002").imagen)

    def test_sin_carpeta_de_fotos_no_falla(self):
        shutil.rmtree(self.fotos)
        self.assertIn("no hay carpeta de fotos", self.cargar())
