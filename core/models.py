"""
Modelo de datos (informe 10.1). Usuario + Perfil (sin herencia), inventario por
Sucursal con stock registrado / reservado, compra digital con reserva, Venta,
Pedido con historial, Devolucion con recepcion fisica y movimientos trazables.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Sucursal(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    direccion = models.CharField(max_length=200, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "sucursales"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Perfil(models.Model):
    CLIENTE = "CLIENTE"
    ADMINISTRADOR = "ADMINISTRADOR"
    VENDEDOR = "VENDEDOR"
    BODEGUERO = "BODEGUERO"
    ROLES = [
        (CLIENTE, "Cliente"),
        (ADMINISTRADOR, "Administrador"),
        (VENDEDOR, "Vendedor"),
        (BODEGUERO, "Bodeguero"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    rol = models.CharField(max_length=15, choices=ROLES, default=CLIENTE)
    sucursal = models.ForeignKey(Sucursal, null=True, blank=True, on_delete=models.PROTECT, related_name="trabajadores")
    telefono = models.CharField(max_length=30, blank=True)
    intentos_fallidos = models.PositiveSmallIntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_rol_display()})"

    @property
    def bloqueado(self):
        return bool(self.bloqueado_hasta and self.bloqueado_hasta > timezone.now())


class Producto(models.Model):
    sku = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    precio = models.PositiveIntegerField(help_text="Precio en pesos chilenos (CLP).")
    activo = models.BooleanField(default=True)
    umbral_stock_critico = models.PositiveIntegerField(default=5, help_text="RF-04: stock disponible desde el cual se marca como critico.")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.sku} - {self.nombre}"


class Existencia(models.Model):
    """Stock de un Producto en una Sucursal (RN-01)."""

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="existencias")
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="existencias")
    stock_registrado = models.PositiveIntegerField(default=0)
    stock_reservado = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["producto", "sucursal"], name="uq_existencia_producto_sucursal"),
            models.CheckConstraint(condition=Q(stock_registrado__gte=0), name="ck_stock_registrado_no_negativo"),
            models.CheckConstraint(condition=Q(stock_reservado__gte=0), name="ck_stock_reservado_no_negativo"),
            models.CheckConstraint(condition=Q(stock_reservado__lte=F("stock_registrado")), name="ck_reservado_no_supera_registrado"),
        ]
        ordering = ["sucursal__nombre", "producto__nombre"]

    @property
    def disponible(self):
        # Stock disponible = stock registrado - stock reservado
        return self.stock_registrado - self.stock_reservado

    @property
    def es_critico(self):
        return self.disponible <= self.producto.umbral_stock_critico

    def __str__(self):
        return f"{self.producto.sku} @ {self.sucursal}"


class Carrito(models.Model):
    cliente = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="carrito")
    actualizado = models.DateTimeField(auto_now=True)


class DetalleCarrito(models.Model):
    carrito = models.ForeignKey(Carrito, on_delete=models.CASCADE, related_name="detalles")
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["carrito", "producto"], name="uq_detalle_carrito"),
            models.CheckConstraint(condition=Q(cantidad__gte=1), name="ck_detalle_carrito_cantidad"),
        ]

    @property
    def subtotal(self):
        return self.producto.precio * self.cantidad


class OperacionCompra(models.Model):
    """Operacion de compra digital: reserva + pago + finalizacion (informe 8.3)."""

    RETIRO = "RETIRO"
    DESPACHO = "DESPACHO"
    MODALIDADES = [(RETIRO, "Retiro en tienda"), (DESPACHO, "Despacho")]

    RESERVADA = "RESERVADA"
    CAPTURA_EN_PROCESO = "CAPTURA_EN_PROCESO"
    PAGO_INCIERTO = "PAGO_INCIERTO"
    PAGADA_PENDIENTE = "PAGADA_PENDIENTE_FINALIZAR"
    FINALIZADA = "FINALIZADA"
    RECHAZADA = "RECHAZADA"
    EXPIRADA = "EXPIRADA"
    EN_REVISION = "EN_REVISION"
    ESTADOS = [
        (RESERVADA, "Reservada, pago pendiente"),
        (CAPTURA_EN_PROCESO, "Captura de pago en proceso"),
        (PAGO_INCIERTO, "Pago con resultado incierto"),
        (PAGADA_PENDIENTE, "Pagada, finalizacion pendiente"),
        (FINALIZADA, "Finalizada"),
        (RECHAZADA, "Pago rechazado"),
        (EXPIRADA, "Reserva vencida"),
        (EN_REVISION, "En revision (inconsistencia)"),
    ]

    referencia = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    cliente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="operaciones")
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT)
    modalidad = models.CharField(max_length=10, choices=MODALIDADES)
    direccion_despacho = models.CharField(max_length=250, blank=True)
    total = models.PositiveIntegerField()
    estado = models.CharField(max_length=30, choices=ESTADOS, default=RESERVADA)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creada"]

    def __str__(self):
        return f"Operacion {str(self.referencia)[:8]} ({self.estado})"


class DetalleOperacion(models.Model):
    operacion = models.ForeignKey(OperacionCompra, on_delete=models.CASCADE, related_name="detalles")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.PositiveIntegerField()

    @property
    def subtotal(self):
        return self.precio_unitario * self.cantidad


class ReservaStock(models.Model):
    ACTIVA = "ACTIVA"
    CONSUMIDA = "CONSUMIDA"
    LIBERADA = "LIBERADA"
    ESTADOS = [(ACTIVA, "Activa"), (CONSUMIDA, "Consumida"), (LIBERADA, "Liberada")]

    operacion = models.OneToOneField(OperacionCompra, on_delete=models.CASCADE, related_name="reserva")
    estado = models.CharField(max_length=10, choices=ESTADOS, default=ACTIVA)
    vence_en = models.DateTimeField()
    creada = models.DateTimeField(auto_now_add=True)


class Pago(models.Model):
    CREADO = "CREADO"
    EN_PROCESO = "EN_PROCESO"
    CAPTURADO = "CAPTURADO"
    RECHAZADO = "RECHAZADO"
    INCIERTO = "INCIERTO"
    INCONSISTENTE = "INCONSISTENTE"
    ESTADOS = [(CREADO, "Creado"), (EN_PROCESO, "En proceso"), (CAPTURADO, "Capturado"),
               (RECHAZADO, "Rechazado"), (INCIERTO, "Incierto"), (INCONSISTENTE, "Inconsistente")]

    operacion = models.OneToOneField(OperacionCompra, on_delete=models.CASCADE, related_name="pago")
    proveedor = models.CharField(max_length=30)
    id_orden_externa = models.CharField(max_length=80, blank=True)
    id_captura = models.CharField(max_length=80, blank=True)
    url_aprobacion = models.URLField(max_length=500, blank=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default=CREADO)
    importe = models.PositiveIntegerField()
    moneda = models.CharField(max_length=5, default="CLP")
    intentos = models.PositiveSmallIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)


class Venta(models.Model):
    PRESENCIAL = "PRESENCIAL"
    DIGITAL = "DIGITAL"
    CANALES = [(PRESENCIAL, "Presencial"), (DIGITAL, "Digital")]

    canal = models.CharField(max_length=10, choices=CANALES)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="ventas")
    vendedor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="ventas_realizadas")
    cliente = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="compras")
    cliente_nombre = models.CharField(max_length=120, blank=True)
    operacion = models.OneToOneField(OperacionCompra, null=True, blank=True, on_delete=models.PROTECT, related_name="venta")
    total = models.PositiveIntegerField()
    requiere_bodega = models.BooleanField(default=False)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-fecha"]

    def __str__(self):
        return f"Venta #{self.pk} ({self.get_canal_display()})"


class DetalleVenta(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="detalles")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.PositiveIntegerField()

    @property
    def subtotal(self):
        return self.precio_unitario * self.cantidad


class Pedido(models.Model):
    SOLICITADO = "SOLICITADO"
    EN_PREPARACION = "EN_PREPARACION"
    PREPARADO = "PREPARADO"
    EN_ENVIO = "EN_ENVIO"
    ENTREGADO = "ENTREGADO"
    ESTADOS = [
        (SOLICITADO, "Solicitado"),
        (EN_PREPARACION, "En preparación"),
        (PREPARADO, "Preparado"),
        (EN_ENVIO, "En envío"),
        (ENTREGADO, "Entregado"),
    ]

    venta = models.OneToOneField(Venta, on_delete=models.PROTECT, related_name="pedido")
    cliente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pedidos")
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="pedidos")
    modalidad = models.CharField(max_length=10, choices=OperacionCompra.MODALIDADES)
    direccion_despacho = models.CharField(max_length=250, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default=SOLICITADO)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado"]

    def __str__(self):
        return f"Pedido #{self.pk} ({self.get_estado_display()})"


class OperadorLogistico(models.Model):
    """RF-13: informacion del operador encargado de un despacho."""

    pedido = models.OneToOneField(Pedido, on_delete=models.CASCADE, related_name="operador")
    nombre = models.CharField(max_length=120)
    contacto = models.CharField(max_length=120, blank=True)
    referencia_seguimiento = models.CharField(max_length=80, blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    registrado_en = models.DateTimeField(auto_now=True)


class HistorialEstadoPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="historial")
    estado_anterior = models.CharField(max_length=20, blank=True)
    estado_nuevo = models.CharField(max_length=20)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    fecha = models.DateTimeField(default=timezone.now)
    nota = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["fecha", "id"]


class Devolucion(models.Model):
    RETRACTO = "RETRACTO"
    GARANTIA = "GARANTIA"
    POLITICA = "POLITICA"
    FUNDAMENTOS = [
        (RETRACTO, "Derecho a retracto"),
        (GARANTIA, "Garantía legal"),
        (POLITICA, "Política comercial"),
    ]
    PENDIENTE = "PENDIENTE"
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"
    ESTADOS = [(PENDIENTE, "Pendiente"), (APROBADA, "Aprobada"), (RECHAZADA, "Rechazada")]

    venta = models.ForeignKey(Venta, on_delete=models.PROTECT, related_name="devoluciones")
    canal_solicitud = models.CharField(max_length=10, choices=Venta.CANALES)
    fundamento = models.CharField(max_length=10, choices=FUNDAMENTOS)
    motivo = models.TextField()
    antecedentes = models.TextField(blank=True)
    fecha_recepcion_cliente = models.DateField(null=True, blank=True)
    confirmacion_escrita = models.BooleanField(default=True, help_text="Solo retracto: se envio la confirmacion escrita de la contratacion.")
    estado = models.CharField(max_length=10, choices=ESTADOS, default=PENDIENTE)
    justificacion = models.TextField(blank=True)
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="devoluciones_registradas")
    evaluada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="devoluciones_evaluadas")
    solicitada = models.DateTimeField(default=timezone.now)
    evaluada = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-solicitada"]
        verbose_name_plural = "devoluciones"


class DetalleDevolucion(models.Model):
    devolucion = models.ForeignKey(Devolucion, on_delete=models.CASCADE, related_name="detalles")
    detalle_venta = models.ForeignKey(DetalleVenta, on_delete=models.PROTECT, related_name="devoluciones")
    cantidad = models.PositiveIntegerField()


class RecepcionDevolucion(models.Model):
    """Recepcion fisica registrada por el Bodeguero (informe 8.3)."""

    detalle = models.ForeignKey(DetalleDevolucion, on_delete=models.PROTECT, related_name="recepciones")
    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT)
    bodeguero = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cantidad_recibida = models.PositiveIntegerField()
    cantidad_apta = models.PositiveIntegerField(help_text="Unidades en condicion de volver a venderse.")
    observacion = models.CharField(max_length=200, blank=True)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(cantidad_apta__lte=F("cantidad_recibida")), name="ck_recepcion_apta_le_recibida"),
        ]


class MovimientoInventario(models.Model):
    AJUSTE = "AJUSTE"
    VENTA_PRESENCIAL = "VENTA_PRESENCIAL"
    VENTA_DIGITAL = "VENTA_DIGITAL"
    DEVOLUCION = "DEVOLUCION"
    TIPOS = [
        (AJUSTE, "Ajuste del Bodeguero"),
        (VENTA_PRESENCIAL, "Venta presencial"),
        (VENTA_DIGITAL, "Venta digital"),
        (DEVOLUCION, "Reincorporación por devolución"),
    ]

    existencia = models.ForeignKey(Existencia, on_delete=models.PROTECT, related_name="movimientos")
    tipo = models.CharField(max_length=20, choices=TIPOS)
    cantidad = models.IntegerField(help_text="Variacion del stock registrado (positiva o negativa).")
    stock_resultante = models.PositiveIntegerField()
    motivo = models.CharField(max_length=200, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    venta = models.ForeignKey(Venta, null=True, blank=True, on_delete=models.PROTECT)
    recepcion = models.OneToOneField(RecepcionDevolucion, null=True, blank=True, on_delete=models.PROTECT)
    # Referencia unica: impide registrar dos veces el mismo movimiento (idempotencia).
    referencia = models.CharField(max_length=120, unique=True, null=True, blank=True)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-fecha", "-id"]


class AvisoBodega(models.Model):
    """RF-06: aviso interno persistente para Bodega."""

    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name="avisos")
    venta = models.OneToOneField(Venta, on_delete=models.CASCADE, related_name="aviso")
    leido = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["leido", "-creado"]


class NotificacionCliente(models.Model):
    """RF-12: notificacion por correo al Cliente, registrada junto con la operacion."""

    PENDIENTE = "PENDIENTE"
    ENVIADA = "ENVIADA"
    FALLIDA = "FALLIDA"
    ESTADOS = [(PENDIENTE, "Pendiente"), (ENVIADA, "Enviada al servicio de correo"), (FALLIDA, "Fallida")]

    cliente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notificaciones")
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="notificaciones")
    clave = models.CharField(max_length=60, unique=True)  # "<pedido>-<estado>": evita duplicados
    asunto = models.CharField(max_length=200)
    mensaje = models.TextField()
    estado = models.CharField(max_length=10, choices=ESTADOS, default=PENDIENTE)
    intentos = models.PositiveSmallIntegerField(default=0)
    ultimo_error = models.CharField(max_length=250, blank=True)
    creada = models.DateTimeField(auto_now_add=True)
    enviada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creada"]


class RegistroActividad(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    nombre_usuario = models.CharField(max_length=150, blank=True)
    accion = models.CharField(max_length=60)
    detalle = models.CharField(max_length=300, blank=True)
    exito = models.BooleanField(default=True)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-fecha", "-id"]
