"""
Reglas de negocio del Sistema Omnicanal (informe 5.2, 8.2, 8.3).

Todas las operaciones que tocan existencias usan transaction.atomic() y
select_for_update() con un orden estable (por producto dentro de una sucursal)
para proteger el inventario ante operaciones concurrentes (RNF-INT-01).
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from . import pagos
from .models import (
    AvisoBodega, Carrito, DetalleCarrito, DetalleDevolucion, DetalleOperacion, DetalleVenta,
    Devolucion, Existencia, HistorialEstadoPedido, MovimientoInventario, NotificacionCliente,
    OperacionCompra, OperadorLogistico, Pago, Pedido, Perfil, Producto, RecepcionDevolucion,
    RegistroActividad, ReservaStock, Sucursal, Venta,
)

log = logging.getLogger("core")


# ---------------------------------------------------------------- errores

class ErrorNegocio(Exception):
    """Una regla de negocio impide la operacion. El mensaje se muestra al usuario."""


class StockInsuficiente(ErrorNegocio):
    pass


class ReservaVencida(ErrorNegocio):
    pass


# ---------------------------------------------------------------- utilidades

def registrar_actividad(usuario, accion, detalle="", exito=True):
    try:
        autenticado = bool(usuario and getattr(usuario, "is_authenticated", False))
        RegistroActividad.objects.create(
            usuario=usuario if autenticado else None,
            nombre_usuario=getattr(usuario, "username", "") if usuario else "",
            accion=accion, detalle=detalle[:300], exito=exito,
        )
    except Exception:  # el registro de actividad nunca debe romper una operacion
        log.exception("No se pudo registrar la actividad %s", accion)
    log.info("%s | usuario=%s | %s | exito=%s", accion, getattr(usuario, "username", "-"), detalle, exito)


def _exigir_rol(usuario, rol):
    perfil = getattr(usuario, "perfil", None)
    if perfil is None or perfil.rol != rol:
        raise PermissionDenied
    return perfil


def _consolidar(items):
    """items = [(producto_id, cantidad)] -> {producto_id: cantidad_total}."""
    resultado = {}
    for pid, cantidad in items:
        cantidad = int(cantidad)
        if cantidad <= 0:
            raise ErrorNegocio("Las cantidades deben ser mayores que cero.")
        resultado[int(pid)] = resultado.get(int(pid), 0) + cantidad
    if not resultado:
        raise ErrorNegocio("Debes indicar al menos un producto.")
    return resultado


def _bloquear_existencias(sucursal_id, producto_ids, crear=False):
    """Bloquea las filas de existencia en orden estable (por producto)."""
    ids = sorted(set(producto_ids))
    if crear:
        for pid in ids:
            Existencia.objects.get_or_create(producto_id=pid, sucursal_id=sucursal_id)
    qs = (Existencia.objects.select_for_update(of=("self",))
          .filter(sucursal_id=sucursal_id, producto_id__in=ids)
          .select_related("producto").order_by("producto_id"))
    return {e.producto_id: e for e in qs}


def _movimiento(existencia, tipo, delta, usuario=None, motivo="", venta=None, recepcion=None, referencia=None):
    return MovimientoInventario.objects.create(
        existencia=existencia, tipo=tipo, cantidad=delta, stock_resultante=existencia.stock_registrado,
        usuario=usuario, motivo=motivo[:200], venta=venta, recepcion=recepcion, referencia=referencia,
    )


# ---------------------------------------------------------------- RF-02 / RF-03 / RF-04

def ajustar_inventario(bodeguero, producto_id, nuevo_stock, motivo):
    """RF-02: ajuste con motivo registrado que respeta las reservas vigentes."""
    perfil = _exigir_rol(bodeguero, Perfil.BODEGUERO)
    if not perfil.sucursal:
        raise ErrorNegocio("Tu usuario no tiene una sucursal asignada.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise ErrorNegocio("Debes indicar el motivo del ajuste.")
    nuevo_stock = int(nuevo_stock)
    if nuevo_stock < 0:
        raise ErrorNegocio("El stock no puede ser negativo.")
    with transaction.atomic():
        producto = Producto.objects.get(pk=producto_id)
        existencias = _bloquear_existencias(perfil.sucursal_id, [producto.id], crear=True)
        e = existencias[producto.id]
        if nuevo_stock < e.stock_reservado:
            raise ErrorNegocio(
                f"No se puede dejar el stock en {nuevo_stock}: hay {e.stock_reservado} unidades reservadas por compras digitales. "
                "Se requiere revisión y conciliación.")
        delta = nuevo_stock - e.stock_registrado
        if delta == 0:
            raise ErrorNegocio("El stock indicado es igual al registrado; no hay nada que ajustar.")
        e.stock_registrado = nuevo_stock
        e.save(update_fields=["stock_registrado"])
        _movimiento(e, MovimientoInventario.AJUSTE, delta, usuario=bodeguero, motivo=motivo)
    registrar_actividad(bodeguero, "AJUSTE_INVENTARIO", f"{producto.sku} {delta:+d} en {perfil.sucursal}: {motivo}")
    return e


def existencias_criticas(sucursal=None):
    """RF-04: existencias cuyo disponible alcanza o queda bajo el umbral del producto."""
    qs = (Existencia.objects.select_related("producto", "sucursal")
          .filter(producto__activo=True)
          .annotate(disp=F("stock_registrado") - F("stock_reservado"))
          .filter(disp__lte=F("producto__umbral_stock_critico")))
    if sucursal:
        qs = qs.filter(sucursal=sucursal)
    return qs.order_by("sucursal__nombre", "disp")


# ---------------------------------------------------------------- RF-05 / RF-06

def registrar_venta_presencial(vendedor, items, cliente_nombre="", requiere_bodega=True):
    """RF-05: venta atomica; impide confirmar si no hay disponibilidad suficiente."""
    perfil = _exigir_rol(vendedor, Perfil.VENDEDOR)
    sucursal = perfil.sucursal
    if not sucursal:
        raise ErrorNegocio("Tu usuario no tiene una sucursal asignada.")
    cantidades = _consolidar(items)
    with transaction.atomic():
        productos = {p.id: p for p in Producto.objects.filter(id__in=cantidades, activo=True)}
        if len(productos) != len(cantidades):
            raise ErrorNegocio("Hay productos inexistentes o inactivos en la venta.")
        existencias = _bloquear_existencias(sucursal.id, cantidades.keys())
        faltantes = []
        for pid, cant in cantidades.items():
            e = existencias.get(pid)
            disponible = e.disponible if e else 0
            if disponible < cant:
                faltantes.append(f"{productos[pid].nombre} (disponible: {disponible}, solicitado: {cant})")
        if faltantes:
            raise StockInsuficiente("Stock insuficiente en la sucursal: " + "; ".join(faltantes))
        total = sum(productos[pid].precio * cant for pid, cant in cantidades.items())
        venta = Venta.objects.create(
            canal=Venta.PRESENCIAL, sucursal=sucursal, vendedor=vendedor,
            cliente_nombre=(cliente_nombre or "").strip()[:120], total=total, requiere_bodega=requiere_bodega,
        )
        for pid, cant in sorted(cantidades.items()):
            DetalleVenta.objects.create(venta=venta, producto=productos[pid], cantidad=cant, precio_unitario=productos[pid].precio)
            e = existencias[pid]
            e.stock_registrado -= cant
            e.save(update_fields=["stock_registrado"])
            _movimiento(e, MovimientoInventario.VENTA_PRESENCIAL, -cant, usuario=vendedor,
                        venta=venta, referencia=f"VP-{venta.id}-{pid}")
        if requiere_bodega:
            AvisoBodega.objects.create(sucursal=sucursal, venta=venta)
    registrar_actividad(vendedor, "VENTA_PRESENCIAL", f"Venta #{venta.id} total {venta.total} en {sucursal}")
    return venta


# ---------------------------------------------------------------- RF-07 carrito

def obtener_carrito(cliente):
    carrito, _ = Carrito.objects.get_or_create(cliente=cliente)
    return carrito


def agregar_al_carrito(cliente, producto_id, cantidad):
    """RF-07: agregar al carrito NO reserva inventario."""
    _exigir_rol(cliente, Perfil.CLIENTE)
    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ErrorNegocio("La cantidad debe ser mayor que cero.")
    producto = Producto.objects.filter(pk=producto_id, activo=True).first()
    if not producto:
        raise ErrorNegocio("El producto no existe o no está disponible.")
    carrito = obtener_carrito(cliente)
    detalle, creado = DetalleCarrito.objects.get_or_create(carrito=carrito, producto=producto, defaults={"cantidad": cantidad})
    if not creado:
        detalle.cantidad += cantidad
        detalle.save(update_fields=["cantidad"])
    return detalle


def quitar_del_carrito(cliente, detalle_id):
    _exigir_rol(cliente, Perfil.CLIENTE)
    DetalleCarrito.objects.filter(pk=detalle_id, carrito__cliente=cliente).delete()


# ---------------------------------------------------------------- RF-08 / RF-09 compra digital

def _liberar_reserva(operacion, estado_final):
    """Libera las unidades reservadas. Debe llamarse dentro de una transaccion."""
    detalles = list(operacion.detalles.all())
    existencias = _bloquear_existencias(operacion.sucursal_id, [d.producto_id for d in detalles])
    reserva = operacion.reserva
    if reserva.estado == ReservaStock.ACTIVA:
        for d in detalles:
            e = existencias.get(d.producto_id)
            if e:
                e.stock_reservado = max(0, e.stock_reservado - d.cantidad)
                e.save(update_fields=["stock_reservado"])
        reserva.estado = ReservaStock.LIBERADA
        reserva.save(update_fields=["estado"])
    operacion.estado = estado_final
    operacion.save(update_fields=["estado", "actualizada"])


def iniciar_compra(cliente, sucursal_id, modalidad, direccion=""):
    """
    Etapas 1-3 del informe 8.3: validacion, reserva completa (todo o nada) e inicio del pago.
    La comunicacion con el proveedor se hace FUERA de la transaccion que bloquea el inventario.
    """
    _exigir_rol(cliente, Perfil.CLIENTE)
    if modalidad not in (OperacionCompra.RETIRO, OperacionCompra.DESPACHO):
        raise ErrorNegocio("Selecciona una modalidad de entrega válida.")
    direccion = (direccion or "").strip()
    if modalidad == OperacionCompra.DESPACHO and not direccion:
        raise ErrorNegocio("Para despacho debes indicar la dirección de entrega.")
    sucursal = Sucursal.objects.filter(pk=sucursal_id, activa=True).first()
    if not sucursal:
        raise ErrorNegocio("Selecciona la sucursal que atenderá la compra.")
    carrito = obtener_carrito(cliente)
    lineas = list(carrito.detalles.select_related("producto"))
    if not lineas:
        raise ErrorNegocio("Tu carrito está vacío.")
    if any(not l.producto.activo for l in lineas):
        raise ErrorNegocio("Hay productos del carrito que ya no están disponibles. Quítalos para continuar.")
    cantidades = {l.producto_id: l.cantidad for l in lineas}

    with transaction.atomic():
        if OperacionCompra.objects.filter(
                cliente=cliente,
                estado__in=[OperacionCompra.CAPTURA_EN_PROCESO, OperacionCompra.PAGO_INCIERTO, OperacionCompra.PAGADA_PENDIENTE]).exists():
            raise ErrorNegocio("Tienes un pago en proceso. Espera a que se resuelva antes de iniciar otra compra.")
        # Una compra anterior abandonada (reservada y sin pago) se libera antes de crear la nueva.
        for previa in OperacionCompra.objects.select_for_update().filter(cliente=cliente, estado=OperacionCompra.RESERVADA):
            _liberar_reserva(previa, OperacionCompra.EXPIRADA)

        existencias = _bloquear_existencias(sucursal.id, cantidades.keys())
        faltantes = []
        for l in lineas:
            e = existencias.get(l.producto_id)
            disponible = e.disponible if e else 0
            if disponible < l.cantidad:
                faltantes.append(f"{l.producto.nombre} (disponible: {disponible}, en tu carrito: {l.cantidad})")
        if faltantes:
            raise StockInsuficiente("No hay stock suficiente en la sucursal elegida: " + "; ".join(faltantes)
                                    + ". No se reservó ninguna unidad.")
        total = sum(l.producto.precio * l.cantidad for l in lineas)
        operacion = OperacionCompra.objects.create(
            cliente=cliente, sucursal=sucursal, modalidad=modalidad,
            direccion_despacho=direccion if modalidad == OperacionCompra.DESPACHO else "", total=total)
        for l in lineas:
            DetalleOperacion.objects.create(operacion=operacion, producto=l.producto, cantidad=l.cantidad, precio_unitario=l.producto.precio)
            e = existencias[l.producto_id]
            e.stock_reservado += l.cantidad
            e.save(update_fields=["stock_reservado"])
        ReservaStock.objects.create(operacion=operacion, vence_en=timezone.now() + timedelta(minutes=settings.RESERVA_MINUTOS))
        procesador = pagos.obtener_procesador()
        moneda = settings.PAGO_MONEDA
        Pago.objects.create(operacion=operacion, proveedor=procesador.nombre, importe=total, moneda=moneda)

    try:
        orden = procesador.crear_orden(operacion.referencia, total, moneda)
    except pagos.ErrorProveedorPago as exc:
        with transaction.atomic():
            op = OperacionCompra.objects.select_for_update().get(pk=operacion.pk)
            _liberar_reserva(op, OperacionCompra.RECHAZADA)
        registrar_actividad(cliente, "PAGO_ORDEN_FALLIDA", str(exc)[:250], exito=False)
        raise ErrorNegocio("No se pudo iniciar el pago con el proveedor. No se realizó ningún cobro; intenta nuevamente.")
    Pago.objects.filter(operacion=operacion).update(id_orden_externa=orden.id, url_aprobacion=orden.url_aprobacion)
    registrar_actividad(cliente, "COMPRA_RESERVA", f"Operación {operacion.referencia} total {total}")
    return operacion


def _verificar_captura(pago, resultado):
    """RF-08: el servidor verifica identificador, importe, moneda y cuenta receptora."""
    if resultado.id_orden != pago.id_orden_externa:
        return False
    if resultado.importe != pago.importe or resultado.moneda != pago.moneda:
        return False
    esperada = settings.PAGO_CUENTA_RECEPTORA
    if esperada and resultado.cuenta_receptora and resultado.cuenta_receptora != esperada:
        return False
    return True


def _aplicar_resultado(operacion_id, resultado):
    with transaction.atomic():
        op = OperacionCompra.objects.select_for_update().get(pk=operacion_id)
        if op.estado != OperacionCompra.CAPTURA_EN_PROCESO:
            return op
        pago = op.pago
        if resultado.estado == pagos.COMPLETADA:
            if _verificar_captura(pago, resultado):
                pago.estado = Pago.CAPTURADO
                pago.id_captura = resultado.id_captura
                op.estado = OperacionCompra.PAGADA_PENDIENTE
            else:
                pago.estado = Pago.INCONSISTENTE
                op.estado = OperacionCompra.EN_REVISION  # la reserva sigue protegida
                log.error("Pago inconsistente en operación %s: la captura no coincide con la compra", op.referencia)
        elif resultado.estado == pagos.RECHAZADA:
            pago.estado = Pago.RECHAZADO
            pago.save()
            _liberar_reserva(op, OperacionCompra.RECHAZADA)
            return op
        else:
            pago.estado = Pago.INCIERTO
            op.estado = OperacionCompra.PAGO_INCIERTO  # la reserva NO se libera por el paso del tiempo
        pago.save()
        op.save(update_fields=["estado", "actualizada"])
        return op


def procesar_pago(operacion_id, modo=None):
    """
    Etapas 4-6 del informe 8.3. 'modo' lo usa solo el simulador (aprobar | rechazar | incierto).
    Es seguro repetir la llamada: no duplica capturas, ventas, pedidos ni descuentos.
    """
    procesador = pagos.obtener_procesador()
    accion = None
    with transaction.atomic():
        op = OperacionCompra.objects.select_for_update().get(pk=operacion_id)
        pago = op.pago
        if op.estado == OperacionCompra.FINALIZADA:
            return op
        if op.estado == OperacionCompra.PAGADA_PENDIENTE:
            accion = "finalizar"
        elif op.estado == OperacionCompra.RESERVADA:
            if op.reserva.vence_en <= timezone.now():
                _liberar_reserva(op, OperacionCompra.EXPIRADA)
                accion = "vencida"
            else:
                op.estado = OperacionCompra.CAPTURA_EN_PROCESO
                op.save(update_fields=["estado", "actualizada"])
                pago.estado = Pago.EN_PROCESO
                pago.intentos += 1
                pago.save()
                accion = "capturar"
        elif op.estado == OperacionCompra.PAGO_INCIERTO:
            op.estado = OperacionCompra.CAPTURA_EN_PROCESO
            op.save(update_fields=["estado", "actualizada"])
            accion = "consultar"  # no se inicia un nuevo cobro: se consulta el existente
        else:
            raise ErrorNegocio(f"La operación no admite pago en su estado actual ({op.get_estado_display()}).")
        esperado = {"importe": pago.importe, "moneda": pago.moneda}
        id_orden = pago.id_orden_externa

    if accion == "vencida":
        raise ReservaVencida("La reserva venció antes de completar el pago. Vuelve a iniciar la compra.")
    if accion == "capturar":
        resultado = procesador.capturar(id_orden, esperado, modo=modo)
        _aplicar_resultado(operacion_id, resultado)
    elif accion == "consultar":
        resultado = procesador.consultar(id_orden, esperado)
        _aplicar_resultado(operacion_id, resultado)

    op = OperacionCompra.objects.get(pk=operacion_id)
    if op.estado == OperacionCompra.PAGADA_PENDIENTE:
        try:
            finalizar_operacion(operacion_id)
        except Exception:
            # Captura confirmada pero falla local: NO se vuelve a cobrar; se reintenta la finalizacion.
            log.exception("Falla al finalizar la operación %s; se reintentará", op.referencia)
        op = OperacionCompra.objects.get(pk=operacion_id)
    return op


def finalizar_operacion(operacion_id):
    """Finalizacion local idempotente (informe 8.3, etapa 6)."""
    with transaction.atomic():
        op = OperacionCompra.objects.select_for_update().get(pk=operacion_id)
        if op.estado == OperacionCompra.FINALIZADA:
            return op.venta
        if op.estado != OperacionCompra.PAGADA_PENDIENTE:
            raise ErrorNegocio("La operación no está lista para finalizarse.")
        reserva = op.reserva
        if reserva.estado != ReservaStock.ACTIVA:
            raise ErrorNegocio("La reserva de la operación ya no está activa.")
        detalles = list(op.detalles.all())
        existencias = _bloquear_existencias(op.sucursal_id, [d.producto_id for d in detalles])
        for d in detalles:
            e = existencias.get(d.producto_id)
            if e is None or e.stock_reservado < d.cantidad or e.stock_registrado < d.cantidad:
                raise ErrorNegocio("Inconsistencia de inventario al finalizar la operación.")
        venta = Venta.objects.create(canal=Venta.DIGITAL, sucursal=op.sucursal, cliente=op.cliente,
                                     operacion=op, total=op.total)
        for d in detalles:
            DetalleVenta.objects.create(venta=venta, producto=d.producto, cantidad=d.cantidad, precio_unitario=d.precio_unitario)
            e = existencias[d.producto_id]
            e.stock_registrado -= d.cantidad
            e.stock_reservado -= d.cantidad
            e.save(update_fields=["stock_registrado", "stock_reservado"])
            _movimiento(e, MovimientoInventario.VENTA_DIGITAL, -d.cantidad, usuario=op.cliente, venta=venta,
                        referencia=f"VD-{op.referencia}-{d.producto_id}")
        pedido = Pedido.objects.create(venta=venta, cliente=op.cliente, sucursal=op.sucursal, modalidad=op.modalidad,
                                       direccion_despacho=op.direccion_despacho, estado=Pedido.SOLICITADO)
        HistorialEstadoPedido.objects.create(pedido=pedido, estado_anterior="", estado_nuevo=Pedido.SOLICITADO,
                                             nota="Pedido confirmado tras verificar el pago")
        reserva.estado = ReservaStock.CONSUMIDA
        reserva.save(update_fields=["estado"])
        op.estado = OperacionCompra.FINALIZADA
        op.save(update_fields=["estado", "actualizada"])
        DetalleCarrito.objects.filter(carrito__cliente=op.cliente, producto_id__in=[d.producto_id for d in detalles]).delete()
        _crear_notificacion(pedido, Pedido.SOLICITADO)
        transaction.on_commit(_enviar_best_effort)
    registrar_actividad(op.cliente, "COMPRA_FINALIZADA", f"Venta #{venta.id} / Pedido #{pedido.id}")
    return venta


# ---------------------------------------------------------------- RF-10 / RF-13 pedidos

def transiciones_permitidas(pedido):
    if pedido.estado == Pedido.SOLICITADO:
        return [Pedido.EN_PREPARACION]
    if pedido.estado == Pedido.EN_PREPARACION:
        return [Pedido.PREPARADO]
    if pedido.estado == Pedido.PREPARADO:
        return [Pedido.ENTREGADO] if pedido.modalidad == OperacionCompra.RETIRO else [Pedido.EN_ENVIO]
    if pedido.estado == Pedido.EN_ENVIO:
        return [Pedido.ENTREGADO]
    return []


def cambiar_estado_pedido(usuario, pedido_id, nuevo_estado):
    """RF-10: solo el Bodeguero de la sucursal; sin saltos ni retrocesos. Devuelve (pedido, cambio)."""
    perfil = _exigir_rol(usuario, Perfil.BODEGUERO)
    with transaction.atomic():
        pedido = Pedido.objects.select_for_update().filter(pk=pedido_id, sucursal_id=perfil.sucursal_id).first()
        if pedido is None:
            raise ErrorNegocio("El pedido no existe o no pertenece a tu sucursal.")
        if pedido.estado == nuevo_estado:
            return pedido, False  # repeticion tecnica: no duplica historial ni notificaciones
        if nuevo_estado not in transiciones_permitidas(pedido):
            raise ErrorNegocio(
                f"Transición no permitida: de «{pedido.get_estado_display()}» a «{dict(Pedido.ESTADOS).get(nuevo_estado, nuevo_estado)}» "
                f"para un pedido con {pedido.get_modalidad_display().lower()}.")
        if nuevo_estado == Pedido.EN_ENVIO and not OperadorLogistico.objects.filter(pedido=pedido).exists():
            raise ErrorNegocio("Antes de pasar a «En envío» debes registrar la información del operador logístico.")
        anterior = pedido.estado
        pedido.estado = nuevo_estado
        pedido.save(update_fields=["estado", "actualizado"])
        HistorialEstadoPedido.objects.create(pedido=pedido, estado_anterior=anterior, estado_nuevo=nuevo_estado, usuario=usuario)
        _crear_notificacion(pedido, nuevo_estado)
        transaction.on_commit(_enviar_best_effort)
    registrar_actividad(usuario, "PEDIDO_ESTADO", f"Pedido #{pedido.id}: {anterior} -> {nuevo_estado}")
    return pedido, True


def registrar_operador(usuario, pedido_id, nombre, contacto="", referencia=""):
    """RF-13: informacion del operador logistico para pedidos con despacho."""
    perfil = _exigir_rol(usuario, Perfil.BODEGUERO)
    nombre = (nombre or "").strip()
    if not nombre:
        raise ErrorNegocio("Indica el nombre del operador logístico.")
    with transaction.atomic():
        pedido = Pedido.objects.select_for_update().filter(pk=pedido_id, sucursal_id=perfil.sucursal_id).first()
        if pedido is None:
            raise ErrorNegocio("El pedido no existe o no pertenece a tu sucursal.")
        if pedido.modalidad != OperacionCompra.DESPACHO:
            raise ErrorNegocio("Solo los pedidos con despacho tienen operador logístico.")
        if pedido.estado == Pedido.ENTREGADO:
            raise ErrorNegocio("El pedido ya fue entregado.")
        OperadorLogistico.objects.update_or_create(
            pedido=pedido, defaults={"nombre": nombre[:120], "contacto": (contacto or "")[:120],
                                     "referencia_seguimiento": (referencia or "")[:80], "registrado_por": usuario})
    registrar_actividad(usuario, "OPERADOR_REGISTRADO", f"Pedido #{pedido_id}: {nombre}")


# ---------------------------------------------------------------- RF-12 notificaciones

def _crear_notificacion(pedido, estado):
    etiqueta = dict(Pedido.ESTADOS).get(estado, estado)
    tienda = settings.NOMBRE_TIENDA
    if estado == Pedido.SOLICITADO:
        asunto = f"{tienda}: recibimos tu pedido #{pedido.id}"
        mensaje = f"Hola {pedido.cliente.first_name or pedido.cliente.username}, tu compra fue confirmada y el pedido #{pedido.id} quedó registrado."
    else:
        asunto = f"{tienda}: tu pedido #{pedido.id} está «{etiqueta}»"
        mensaje = f"Hola {pedido.cliente.first_name or pedido.cliente.username}, tu pedido #{pedido.id} cambió su estado a «{etiqueta}»."
    NotificacionCliente.objects.get_or_create(
        clave=f"{pedido.id}-{estado}",
        defaults={"cliente": pedido.cliente, "pedido": pedido, "asunto": asunto, "mensaje": mensaje})


def enviar_notificaciones_pendientes(limite=50):
    """El envio ocurre DESPUES de confirmar la transaccion; una falla no revierte nada."""
    enviadas = fallidas = 0
    pendientes = (NotificacionCliente.objects
                  .filter(estado__in=[NotificacionCliente.PENDIENTE, NotificacionCliente.FALLIDA],
                          intentos__lt=settings.NOTIFICACION_MAX_INTENTOS)
                  .select_related("cliente").order_by("creada")[:limite])
    for n in pendientes:
        n.intentos += 1
        try:
            if not n.cliente.email:
                raise ValueError("El cliente no tiene correo registrado")
            send_mail(n.asunto, n.mensaje, settings.DEFAULT_FROM_EMAIL, [n.cliente.email], fail_silently=False)
            n.estado = NotificacionCliente.ENVIADA  # aceptado por el servicio de correo, no "leido"
            n.enviada_en = timezone.now()
            n.ultimo_error = ""
            enviadas += 1
        except Exception as exc:
            n.estado = NotificacionCliente.FALLIDA
            n.ultimo_error = str(exc)[:250]
            fallidas += 1
            log.warning("Falla al enviar notificación %s: %s", n.pk, exc)
        n.save()
    return enviadas, fallidas


def _enviar_best_effort():
    try:
        enviar_notificaciones_pendientes()
    except Exception:
        log.exception("Fallo no crítico al enviar notificaciones")


# ---------------------------------------------------------------- RF-14 devoluciones

def _cantidad_comprometida(detalle_venta):
    total = (DetalleDevolucion.objects.filter(detalle_venta=detalle_venta,
                                              devolucion__estado__in=[Devolucion.PENDIENTE, Devolucion.APROBADA])
             .aggregate(t=Sum("cantidad"))["t"])
    return total or 0


def crear_devolucion(vendedor, venta_id, canal_solicitud, fundamento, motivo, items, antecedentes="",
                     fecha_recepcion=None, confirmacion_escrita=True):
    """items = {detalle_venta_id: cantidad}. La solicitud puede venir de un canal distinto al de la compra (RN-06)."""
    _exigir_rol(vendedor, Perfil.VENDEDOR)
    venta = Venta.objects.filter(pk=venta_id).first()
    if not venta:
        raise ErrorNegocio("No se encontró la venta original.")
    if not (motivo or "").strip():
        raise ErrorNegocio("Indica el motivo de la solicitud.")
    items = {int(k): int(v) for k, v in items.items() if int(v) > 0}
    if not items:
        raise ErrorNegocio("Indica al menos un producto y cantidad a devolver.")
    with transaction.atomic():
        detalles = {d.id: d for d in venta.detalles.select_for_update()}
        for did, cant in items.items():
            d = detalles.get(did)
            if d is None:
                raise ErrorNegocio("Hay productos que no pertenecen a la venta original.")
            disponible = d.cantidad - _cantidad_comprometida(d)
            if cant > disponible:
                raise ErrorNegocio(f"{d.producto.nombre}: solo se pueden devolver {disponible} unidades (ya hay solicitudes por el resto).")
        dev = Devolucion.objects.create(
            venta=venta, canal_solicitud=canal_solicitud, fundamento=fundamento, motivo=motivo.strip(),
            antecedentes=(antecedentes or "").strip(), fecha_recepcion_cliente=fecha_recepcion,
            confirmacion_escrita=confirmacion_escrita, registrada_por=vendedor)
        for did, cant in items.items():
            DetalleDevolucion.objects.create(devolucion=dev, detalle_venta=detalles[did], cantidad=cant)
    registrar_actividad(vendedor, "DEVOLUCION_SOLICITUD", f"Devolución #{dev.id} de venta #{venta.id} ({fundamento})")
    return dev


def evaluar_plazos(dev, hoy=None):
    """
    Apoyo a la evaluacion (informe 7.6.3). Devuelve (sugerencia, texto):
    CUMPLE | NO_CUMPLE | PENDIENTE (faltan antecedentes). La decision final es del Vendedor.
    """
    hoy = hoy or timezone.localdate()
    base = dev.fecha_recepcion_cliente
    if dev.fundamento == Devolucion.POLITICA and base is None:
        base = timezone.localtime(dev.venta.fecha).date()
    if base is None:
        return "PENDIENTE", "Falta la fecha de recepción del producto: la solicitud debe permanecer pendiente."
    dias = (hoy - base).days
    if dev.fundamento == Devolucion.RETRACTO:
        if dev.venta.canal != Venta.DIGITAL:
            return "NO_CUMPLE", ("El retracto se aplica a contrataciones por medios electrónicos y esta compra fue presencial. "
                                 "Evalúa si corresponde garantía legal o política comercial.")
        plazo = 10 if dev.confirmacion_escrita else 90
        nota = "" if dev.confirmacion_escrita else " (se amplía a 90 días porque no se envió la confirmación escrita)"
        if dias <= plazo:
            return "CUMPLE", f"Dentro del plazo de retracto: {dias} de {plazo} días{nota}. Revisa también las excepciones legales del producto."
        return "NO_CUMPLE", f"Fuera del plazo de retracto: {dias} días (máximo {plazo}){nota}."
    if dev.fundamento == Devolucion.GARANTIA:
        if not dev.antecedentes.strip():
            return "PENDIENTE", "Garantía legal: faltan los antecedentes de la falla invocada; la solicitud debe permanecer pendiente."
        if dias <= 183:
            return "CUMPLE", f"Dentro del plazo de garantía legal de 6 meses ({dias} días desde la recepción)."
        return "NO_CUMPLE", f"Fuera del plazo general de 6 meses de garantía legal ({dias} días)."
    if dias <= 30:
        return "CUMPLE", f"Dentro de la política comercial de 30 días ({dias} días)."
    return "NO_CUMPLE", (f"Fuera de la política comercial de 30 días ({dias} días). Superar este plazo no basta por sí solo "
                         "para rechazar: revisa si corresponde retracto o garantía legal.")


def evaluar_devolucion(vendedor, devolucion_id, decision, justificacion):
    """Aprobar, rechazar o dejar pendiente. Aprobar NO repone inventario ni reembolsa."""
    _exigir_rol(vendedor, Perfil.VENDEDOR)
    justificacion = (justificacion or "").strip()
    with transaction.atomic():
        dev = Devolucion.objects.select_for_update().get(pk=devolucion_id)
        if dev.estado != Devolucion.PENDIENTE:
            raise ErrorNegocio("La solicitud ya fue evaluada.")
        if decision == Devolucion.PENDIENTE:
            dev.justificacion = justificacion
            dev.save(update_fields=["justificacion"])
            return dev
        sugerencia, _ = evaluar_plazos(dev)
        if sugerencia == "PENDIENTE":
            raise ErrorNegocio("Faltan antecedentes: la solicitud debe permanecer pendiente (no es un rechazo).")
        if decision not in (Devolucion.APROBADA, Devolucion.RECHAZADA):
            raise ErrorNegocio("Decisión no válida.")
        if not justificacion:
            raise ErrorNegocio("Debes registrar la justificación de la decisión.")
        dev.estado = decision
        dev.justificacion = justificacion
        dev.evaluada_por = vendedor
        dev.evaluada = timezone.now()
        dev.save()
    registrar_actividad(vendedor, "DEVOLUCION_EVALUADA", f"Devolución #{dev.id}: {decision}")
    return dev


def recibir_devolucion(bodeguero, detalle_devolucion_id, cantidad_recibida, cantidad_apta, observacion=""):
    """
    Recepcion fisica (Bodeguero). Solo las unidades aptas para venta se reincorporan al inventario,
    mediante un movimiento trazable, controlando que no se repongan mas unidades que las aprobadas.
    """
    perfil = _exigir_rol(bodeguero, Perfil.BODEGUERO)
    recibida, apta = int(cantidad_recibida), int(cantidad_apta)
    if recibida <= 0:
        raise ErrorNegocio("La cantidad recibida debe ser mayor que cero.")
    if apta < 0 or apta > recibida:
        raise ErrorNegocio("Las unidades aptas no pueden superar las recibidas.")
    with transaction.atomic():
        detalle = (DetalleDevolucion.objects.select_for_update(of=("self",))
                   .select_related("devolucion", "detalle_venta__producto").get(pk=detalle_devolucion_id))
        if detalle.devolucion.estado != Devolucion.APROBADA:
            raise ErrorNegocio("Solo se pueden recibir productos de devoluciones aprobadas.")
        previas = detalle.recepciones.aggregate(r=Sum("cantidad_recibida"), a=Sum("cantidad_apta"))
        ya_recibida = previas["r"] or 0
        if ya_recibida + recibida > detalle.cantidad:
            raise ErrorNegocio(f"Se aprobó la devolución de {detalle.cantidad} unidades y ya se recibieron {ya_recibida}.")
        recepcion = RecepcionDevolucion.objects.create(
            detalle=detalle, sucursal=perfil.sucursal, bodeguero=bodeguero,
            cantidad_recibida=recibida, cantidad_apta=apta, observacion=(observacion or "")[:200])
        if apta > 0:
            producto = detalle.detalle_venta.producto
            e = _bloquear_existencias(perfil.sucursal_id, [producto.id], crear=True)[producto.id]
            e.stock_registrado += apta
            e.save(update_fields=["stock_registrado"])
            _movimiento(e, MovimientoInventario.DEVOLUCION, apta, usuario=bodeguero, recepcion=recepcion,
                        motivo=f"Devolución #{detalle.devolucion_id}", referencia=f"DEV-{recepcion.id}")
    registrar_actividad(bodeguero, "DEVOLUCION_RECEPCION",
                        f"Devolución #{detalle.devolucion_id}: recibidas {recibida}, reincorporadas {apta}")
    return recepcion


# ---------------------------------------------------------------- procesos programados

def procesar_pendientes():
    """
    Procesos periodicos (informe 8.3): reservas vencidas, pagos inciertos,
    finalizaciones pendientes y notificaciones. Cada paso vuelve a verificar el estado antes de actuar.
    """
    resumen = {"reservas_liberadas": 0, "pagos_revisados": 0, "finalizadas": 0, "notificaciones_enviadas": 0, "notificaciones_fallidas": 0}
    ahora = timezone.now()

    vencidas = list(OperacionCompra.objects.filter(estado=OperacionCompra.RESERVADA, reserva__vence_en__lte=ahora).values_list("pk", flat=True))
    for pk in vencidas:
        with transaction.atomic():
            op = OperacionCompra.objects.select_for_update().get(pk=pk)
            # Se vuelve a verificar: solo se libera si sigue reservada y vencida (sin captura en curso).
            if op.estado == OperacionCompra.RESERVADA and op.reserva.vence_en <= timezone.now():
                _liberar_reserva(op, OperacionCompra.EXPIRADA)
                resumen["reservas_liberadas"] += 1

    for pk in list(OperacionCompra.objects.filter(estado=OperacionCompra.PAGO_INCIERTO).values_list("pk", flat=True)):
        try:
            op = procesar_pago(pk)
            resumen["pagos_revisados"] += 1
            if op.estado == OperacionCompra.FINALIZADA:
                resumen["finalizadas"] += 1
        except ErrorNegocio:
            log.exception("No se pudo revisar el pago incierto %s", pk)

    for pk in list(OperacionCompra.objects.filter(estado=OperacionCompra.PAGADA_PENDIENTE).values_list("pk", flat=True)):
        try:
            finalizar_operacion(pk)
            resumen["finalizadas"] += 1
        except Exception:
            log.exception("Sigue pendiente la finalización de la operación %s", pk)

    enviadas, fallidas = enviar_notificaciones_pendientes()
    resumen["notificaciones_enviadas"], resumen["notificaciones_fallidas"] = enviadas, fallidas
    return resumen


def metricas():
    """Indicadores simples para la pagina de monitoreo (equivalente local a Azure Monitor)."""
    hoy = timezone.now() - timedelta(hours=24)
    return {
        "operaciones_por_estado": {e: OperacionCompra.objects.filter(estado=e).count() for e, _ in OperacionCompra.ESTADOS},
        "pedidos_por_estado": {e: Pedido.objects.filter(estado=e).count() for e, _ in Pedido.ESTADOS},
        "notificaciones_pendientes": NotificacionCliente.objects.filter(estado=NotificacionCliente.PENDIENTE).count(),
        "notificaciones_fallidas": NotificacionCliente.objects.filter(estado=NotificacionCliente.FALLIDA).count(),
        "accesos_denegados_24h": RegistroActividad.objects.filter(accion="ACCESO_DENEGADO", fecha__gte=hoy).count(),
        "logins_fallidos_24h": RegistroActividad.objects.filter(accion="LOGIN_FALLIDO", fecha__gte=hoy).count(),
        "errores_24h": RegistroActividad.objects.filter(exito=False, fecha__gte=hoy).count(),
    }
