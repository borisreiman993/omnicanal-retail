from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .forms import (
    AgregarCarritoForm, AjusteForm, BuscarVentaForm, CheckoutForm, DevolucionForm, EvaluacionForm,
    ItemVentaFormSet, LoginForm, OperadorForm, ProductoForm, RecepcionForm, RegistroForm, VentaExtraForm,
)
from .middleware import CLAVE_INICIO
from .models import (
    AvisoBodega, Devolucion, DetalleDevolucion, Existencia, MovimientoInventario, NotificacionCliente,
    OperacionCompra, Pedido, Perfil, Producto, RegistroActividad, Sucursal, Venta,
)
from .permisos import rol_requerido, solo_superusuario
from .services import ErrorNegocio, registrar_actividad

User = get_user_model()
CLIENTE, ADMIN, VENDEDOR, BODEGUERO = Perfil.CLIENTE, Perfil.ADMINISTRADOR, Perfil.VENDEDOR, Perfil.BODEGUERO


def error_403(request, exception=None):
    return render(request, "core/403.html", status=403)


def estado_sistema(request):
    """Comprobacion simple de disponibilidad (conexion a la base de datos)."""
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        return JsonResponse({"estado": "ok"})
    except Exception:
        return JsonResponse({"estado": "error"}, status=503)


# ------------------------------------------------------------------ acceso (RF-17)

def login_view(request):
    if request.user.is_authenticated:
        return redirect("inicio")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        nombre = form.cleaned_data["username"].strip()
        clave = form.cleaned_data["password"]
        usuario = User.objects.filter(username__iexact=nombre).first()
        perfil = getattr(usuario, "perfil", None)
        if perfil and perfil.bloqueado:
            registrar_actividad(usuario, "LOGIN_BLOQUEADO", "Intento con la cuenta bloqueada", exito=False)
            messages.error(request, f"Por seguridad, la cuenta está bloqueada temporalmente. Intenta nuevamente en {settings.LOGIN_BLOQUEO_MINUTOS} minutos.")
        else:
            autenticado = authenticate(request, username=usuario.username if usuario else nombre, password=clave)
            if autenticado is not None and autenticado.is_active:
                if perfil:
                    perfil.intentos_fallidos = 0
                    perfil.bloqueado_hasta = None
                    perfil.save(update_fields=["intentos_fallidos", "bloqueado_hasta"])
                login(request, autenticado)
                request.session[CLAVE_INICIO] = timezone.now().timestamp()
                registrar_actividad(autenticado, "LOGIN_OK", "Inicio de sesión")
                destino = request.GET.get("next", "")
                if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
                    return redirect(destino)
                return redirect("inicio")
            if perfil:
                perfil.intentos_fallidos += 1
                if perfil.intentos_fallidos >= settings.LOGIN_MAX_INTENTOS:
                    perfil.bloqueado_hasta = timezone.now() + timedelta(minutes=settings.LOGIN_BLOQUEO_MINUTOS)
                    perfil.intentos_fallidos = 0
                perfil.save(update_fields=["intentos_fallidos", "bloqueado_hasta"])
            registrar_actividad(usuario, "LOGIN_FALLIDO", f"Usuario ingresado: {nombre[:40]}", exito=False)
            messages.error(request, "Usuario o contraseña incorrectos.")  # mensaje generico
    return render(request, "core/login.html", {"form": form})


@require_POST
def logout_view(request):
    registrar_actividad(request.user, "LOGOUT", "Cierre de sesión")
    logout(request)
    messages.info(request, "Cerraste sesión.")
    return redirect("login")


def registro(request):
    if request.user.is_authenticated:
        return redirect("inicio")
    form = RegistroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        registrar_actividad(user, "REGISTRO_CLIENTE", "Nueva cuenta de cliente")
        messages.success(request, "Tu cuenta fue creada. Ya puedes iniciar sesión.")
        return redirect("login")
    return render(request, "core/registro.html", {"form": form})


# ------------------------------------------------------------------ inicio

def inicio(request):
    if not request.user.is_authenticated:
        return redirect("catalogo")
    perfil = getattr(request.user, "perfil", None)
    ctx = {}
    if perfil and perfil.rol == CLIENTE:
        ctx["pedidos"] = Pedido.objects.filter(cliente=request.user)[:5]
    elif perfil and perfil.rol == ADMIN:
        ctx["n_criticos"] = services.existencias_criticas().count()
        ctx["n_productos"] = Producto.objects.filter(activo=True).count()
    elif perfil and perfil.rol == VENDEDOR:
        hoy = timezone.localdate()
        ctx["ventas_hoy"] = Venta.objects.filter(sucursal=perfil.sucursal, fecha__date=hoy).count()
        ctx["devoluciones_pendientes"] = Devolucion.objects.filter(estado=Devolucion.PENDIENTE).count()
    elif perfil and perfil.rol == BODEGUERO:
        ctx["avisos_nuevos"] = AvisoBodega.objects.filter(sucursal=perfil.sucursal, leido=False).count()
        ctx["pedidos_abiertos"] = Pedido.objects.filter(sucursal=perfil.sucursal).exclude(estado=Pedido.ENTREGADO).count()
        ctx["n_criticos"] = services.existencias_criticas(perfil.sucursal).count()
    return render(request, "core/inicio.html", ctx)


# ------------------------------------------------------------------ catalogo (RF-03)

def catalogo(request):
    q = request.GET.get("q", "").strip()
    sucursales = list(Sucursal.objects.filter(activa=True))
    productos = Producto.objects.filter(activo=True)
    if q:
        productos = productos.filter(Q(nombre__icontains=q) | Q(sku__icontains=q))
    existencias = {(e.producto_id, e.sucursal_id): e for e in Existencia.objects.filter(producto__in=productos)}
    filas = []
    for p in productos:
        celdas = []
        for s in sucursales:
            e = existencias.get((p.id, s.id))
            celdas.append(e.disponible if e else 0)
        filas.append({"producto": p, "celdas": celdas})
    return render(request, "core/catalogo.html", {"filas": filas, "sucursales": sucursales, "q": q, "form": AgregarCarritoForm()})


# ------------------------------------------------------------------ carrito y compra (RF-07/08/09)

@rol_requerido(CLIENTE)
def carrito(request):
    c = services.obtener_carrito(request.user)
    lineas = list(c.detalles.select_related("producto"))
    total = sum(l.subtotal for l in lineas)
    return render(request, "core/carrito.html", {"lineas": lineas, "total": total})


@rol_requerido(CLIENTE)
@require_POST
def carrito_agregar(request):
    form = AgregarCarritoForm(request.POST)
    if form.is_valid():
        try:
            services.agregar_al_carrito(request.user, form.cleaned_data["producto_id"], form.cleaned_data["cantidad"])
            messages.success(request, "Producto agregado al carrito. Recuerda: agregar al carrito no reserva stock.")
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Indica una cantidad válida.")
    return redirect(request.POST.get("volver") or "catalogo") if url_has_allowed_host_and_scheme(request.POST.get("volver") or "/", allowed_hosts={request.get_host()}) else redirect("catalogo")


@rol_requerido(CLIENTE)
@require_POST
def carrito_quitar(request, detalle_id):
    services.quitar_del_carrito(request.user, detalle_id)
    messages.info(request, "Producto quitado del carrito.")
    return redirect("carrito")


@rol_requerido(CLIENTE)
def checkout(request):
    c = services.obtener_carrito(request.user)
    lineas = list(c.detalles.select_related("producto"))
    if not lineas:
        messages.warning(request, "Tu carrito está vacío.")
        return redirect("catalogo")
    form = CheckoutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            op = services.iniciar_compra(request.user, form.cleaned_data["sucursal"].id,
                                         form.cleaned_data["modalidad"], form.cleaned_data["direccion"])
            return redirect("pago", referencia=op.referencia)
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    # Orientacion: en que sucursales hay stock para todo el carrito
    existencias = {(e.producto_id, e.sucursal_id): e.disponible for e in Existencia.objects.filter(producto__in=[l.producto for l in lineas])}
    resumen = []
    for s in Sucursal.objects.filter(activa=True):
        ok = all(existencias.get((l.producto_id, s.id), 0) >= l.cantidad for l in lineas)
        resumen.append((s, ok))
    return render(request, "core/checkout.html", {"form": form, "lineas": lineas, "total": sum(l.subtotal for l in lineas), "resumen": resumen})


@rol_requerido(CLIENTE)
def pago(request, referencia):
    op = get_object_or_404(OperacionCompra, referencia=referencia, cliente=request.user)
    if request.method == "POST":
        modo = request.POST.get("modo")
        if settings.PAGO_PROVEEDOR != "simulado":
            modo = None
        elif modo not in ("aprobar", "rechazar", "incierto"):
            raise Http404
        try:
            services.procesar_pago(op.pk, modo=modo)
        except services.ReservaVencida as exc:
            messages.error(request, str(exc))
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
        return redirect("pago", referencia=referencia)
    op.refresh_from_db()
    segundos = 0
    if op.estado == OperacionCompra.RESERVADA:
        segundos = max(0, int((op.reserva.vence_en - timezone.now()).total_seconds()))
    return render(request, "core/pago.html", {
        "op": op, "pago": op.pago, "segundos": segundos, "simulado": settings.PAGO_PROVEEDOR == "simulado",
        "pedido": getattr(getattr(op, "venta", None), "pedido", None) if op.estado == OperacionCompra.FINALIZADA else None,
    })


# ------------------------------------------------------------------ pedidos del cliente (RF-11) y notificaciones

@rol_requerido(CLIENTE)
def mis_pedidos(request):
    return render(request, "core/mis_pedidos.html", {"pedidos": Pedido.objects.filter(cliente=request.user).select_related("sucursal")})


@rol_requerido(CLIENTE)
def mi_pedido(request, pedido_id):
    pedido = get_object_or_404(Pedido.objects.select_related("sucursal", "venta"), pk=pedido_id, cliente=request.user)  # 404 si es ajeno
    return render(request, "core/pedido_cliente.html", {"pedido": pedido, "detalles": pedido.venta.detalles.select_related("producto"),
                                                         "historial": pedido.historial.all(), "operador": getattr(pedido, "operador", None)})


@rol_requerido(CLIENTE)
def mis_notificaciones(request):
    return render(request, "core/mis_notificaciones.html", {"notificaciones": NotificacionCliente.objects.filter(cliente=request.user)[:50]})


# ------------------------------------------------------------------ administrador (RF-01 / RF-04)

@rol_requerido(ADMIN)
def productos(request):
    q = request.GET.get("q", "").strip()
    qs = Producto.objects.all()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(sku__icontains=q))
    return render(request, "core/productos.html", {"productos": qs, "q": q})


@rol_requerido(ADMIN)
def producto_form(request, producto_id=None):
    producto = get_object_or_404(Producto, pk=producto_id) if producto_id else None
    form = ProductoForm(request.POST or None, instance=producto)
    if request.method == "POST" and form.is_valid():
        p = form.save()
        registrar_actividad(request.user, "PRODUCTO_EDITADO" if producto else "PRODUCTO_CREADO", f"{p.sku} - {p.nombre}")
        messages.success(request, "Producto guardado. Los cambios ya están disponibles para todos los canales de venta.")
        return redirect("productos")
    return render(request, "core/producto_form.html", {"form": form, "producto": producto})


@rol_requerido(ADMIN, BODEGUERO)
def stock_critico(request):
    perfil = request.user.perfil
    sucursal = perfil.sucursal if perfil.rol == BODEGUERO else None
    return render(request, "core/stock_critico.html", {"existencias": services.existencias_criticas(sucursal), "sucursal": sucursal})


# ------------------------------------------------------------------ vendedor (RF-05 / RF-14)

@rol_requerido(VENDEDOR)
def venta_nueva(request):
    perfil = request.user.perfil
    formset = ItemVentaFormSet(request.POST or None, prefix="items")
    extra = VentaExtraForm(request.POST or None)
    if request.method == "POST" and formset.is_valid() and extra.is_valid():
        items = [(f.cleaned_data["producto"].id, f.cleaned_data["cantidad"]) for f in formset
                 if f.cleaned_data.get("producto") and f.cleaned_data.get("cantidad")]
        try:
            venta = services.registrar_venta_presencial(request.user, items, extra.cleaned_data["cliente_nombre"],
                                                        extra.cleaned_data["requiere_bodega"])
            messages.success(request, f"Venta #{venta.id} registrada por ${venta.total:,}".replace(",", "."))
            return redirect("venta_detalle", venta_id=venta.id)
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    return render(request, "core/venta_nueva.html", {"formset": formset, "extra": extra, "sucursal": perfil.sucursal})


@rol_requerido(VENDEDOR)
def ventas(request):
    qs = Venta.objects.filter(sucursal=request.user.perfil.sucursal).select_related("vendedor")
    return render(request, "core/ventas.html", {"page": Paginator(qs, 25).get_page(request.GET.get("page"))})


@rol_requerido(VENDEDOR)
def venta_detalle(request, venta_id):
    venta = get_object_or_404(Venta, pk=venta_id, sucursal=request.user.perfil.sucursal)
    return render(request, "core/venta_detalle.html", {"venta": venta, "detalles": venta.detalles.select_related("producto")})


@rol_requerido(VENDEDOR)
def devoluciones(request):
    qs = Devolucion.objects.select_related("venta")
    estado = request.GET.get("estado")
    if estado in dict(Devolucion.ESTADOS):
        qs = qs.filter(estado=estado)
    return render(request, "core/devoluciones.html", {"devoluciones": qs[:100], "estado": estado})


@rol_requerido(VENDEDOR)
def devolucion_nueva(request):
    """Paso 1: ubicar la compra original (consulta restringida). Paso 2: registrar la solicitud."""
    buscar = BuscarVentaForm(request.GET or None)
    venta = None
    if request.GET.get("venta_id") and buscar.is_valid():
        venta = Venta.objects.filter(pk=buscar.cleaned_data["venta_id"]).first()
        if venta is None:
            messages.error(request, "No se encontró una venta con ese número.")
    form = DevolucionForm(request.POST or None)
    if venta and request.method == "POST" and form.is_valid():
        items = {}
        for d in venta.detalles.all():
            try:
                items[d.id] = int(request.POST.get(f"cant_{d.id}") or 0)
            except ValueError:
                items[d.id] = 0
        try:
            dev = services.crear_devolucion(
                request.user, venta.id, form.cleaned_data["canal_solicitud"], form.cleaned_data["fundamento"],
                form.cleaned_data["motivo"], items, form.cleaned_data["antecedentes"],
                form.cleaned_data["fecha_recepcion_cliente"], form.cleaned_data["confirmacion_escrita"])
            messages.success(request, f"Solicitud de devolución #{dev.id} registrada como Pendiente.")
            return redirect("devolucion_detalle", devolucion_id=dev.id)
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    detalles = []
    if venta:
        for d in venta.detalles.select_related("producto"):
            detalles.append({"d": d, "disponible": d.cantidad - services._cantidad_comprometida(d)})
    return render(request, "core/devolucion_nueva.html", {"buscar": buscar, "venta": venta, "form": form, "detalles": detalles})


@rol_requerido(VENDEDOR)
def devolucion_detalle(request, devolucion_id):
    dev = get_object_or_404(Devolucion.objects.select_related("venta"), pk=devolucion_id)
    form = EvaluacionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.evaluar_devolucion(request.user, dev.id, form.cleaned_data["decision"], form.cleaned_data["justificacion"])
            messages.success(request, "Evaluación registrada.")
            return redirect("devolucion_detalle", devolucion_id=dev.id)
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    dev.refresh_from_db()
    sugerencia, texto = services.evaluar_plazos(dev)
    detalles = dev.detalles.select_related("detalle_venta__producto")
    return render(request, "core/devolucion_detalle.html", {"dev": dev, "form": form, "sugerencia": sugerencia, "texto": texto, "detalles": detalles})


# ------------------------------------------------------------------ bodeguero (RF-02 / RF-06 / RF-10 / RF-13)

def _suc(request):
    return request.user.perfil.sucursal


@rol_requerido(BODEGUERO)
def inventario(request):
    suc = _suc(request)
    existencias = {e.producto_id: e for e in Existencia.objects.filter(sucursal=suc)}
    filas = [{"producto": p, "e": existencias.get(p.id)} for p in Producto.objects.filter(activo=True)]
    return render(request, "core/inventario.html", {"filas": filas, "sucursal": suc})


@rol_requerido(BODEGUERO)
def inventario_ajustar(request, producto_id):
    suc = _suc(request)
    producto = get_object_or_404(Producto, pk=producto_id)
    e = Existencia.objects.filter(producto=producto, sucursal=suc).first()
    form = AjusteForm(request.POST or None, initial={"nuevo_stock": e.stock_registrado if e else 0})
    if request.method == "POST" and form.is_valid():
        try:
            services.ajustar_inventario(request.user, producto.id, form.cleaned_data["nuevo_stock"], form.cleaned_data["motivo"])
            messages.success(request, "Ajuste registrado con su movimiento trazable.")
            return redirect("inventario")
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    return render(request, "core/inventario_ajustar.html", {"form": form, "producto": producto, "e": e, "sucursal": suc})


@rol_requerido(BODEGUERO)
def movimientos(request):
    qs = MovimientoInventario.objects.filter(existencia__sucursal=_suc(request)).select_related("existencia__producto", "usuario")
    return render(request, "core/movimientos.html", {"page": Paginator(qs, 30).get_page(request.GET.get("page")), "sucursal": _suc(request)})


@rol_requerido(BODEGUERO)
def avisos(request):
    qs = AvisoBodega.objects.filter(sucursal=_suc(request)).select_related("venta")
    return render(request, "core/avisos.html", {"avisos": qs[:100]})


@rol_requerido(BODEGUERO)
@require_POST
def aviso_leido(request, aviso_id):
    AvisoBodega.objects.filter(pk=aviso_id, sucursal=_suc(request)).update(leido=True)
    return redirect("avisos")


@rol_requerido(BODEGUERO)
def pedidos_bodega(request):
    qs = Pedido.objects.filter(sucursal=_suc(request)).select_related("cliente")
    estado = request.GET.get("estado")
    if estado in dict(Pedido.ESTADOS):
        qs = qs.filter(estado=estado)
    return render(request, "core/pedidos_bodega.html", {"pedidos": qs[:100], "estado": estado, "estados": Pedido.ESTADOS})


@rol_requerido(BODEGUERO)
def pedido_bodega(request, pedido_id):
    pedido = get_object_or_404(Pedido.objects.select_related("venta", "cliente"), pk=pedido_id, sucursal=_suc(request))
    operador_form = OperadorForm(initial={"nombre": getattr(getattr(pedido, "operador", None), "nombre", "")})
    siguientes = [(e, dict(Pedido.ESTADOS)[e]) for e in services.transiciones_permitidas(pedido)]
    return render(request, "core/pedido_bodega.html", {
        "pedido": pedido, "detalles": pedido.venta.detalles.select_related("producto"), "historial": pedido.historial.all(),
        "operador": getattr(pedido, "operador", None), "siguientes": siguientes, "operador_form": operador_form})


@rol_requerido(BODEGUERO)
@require_POST
def pedido_cambiar_estado(request, pedido_id):
    get_object_or_404(Pedido, pk=pedido_id, sucursal=_suc(request))
    try:
        pedido, cambio = services.cambiar_estado_pedido(request.user, pedido_id, request.POST.get("estado", ""))
        messages.success(request, "Estado actualizado." if cambio else "El pedido ya estaba en ese estado.")
    except ErrorNegocio as exc:
        messages.error(request, str(exc))
    return redirect("pedido_bodega", pedido_id=pedido_id)


@rol_requerido(BODEGUERO)
@require_POST
def pedido_operador(request, pedido_id):
    get_object_or_404(Pedido, pk=pedido_id, sucursal=_suc(request))
    form = OperadorForm(request.POST)
    if form.is_valid():
        try:
            services.registrar_operador(request.user, pedido_id, form.cleaned_data["nombre"], form.cleaned_data["contacto"], form.cleaned_data["referencia"])
            messages.success(request, "Información del operador logístico registrada.")
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Indica el nombre del operador logístico.")
    return redirect("pedido_bodega", pedido_id=pedido_id)


@rol_requerido(BODEGUERO)
def recepciones(request):
    detalles = (DetalleDevolucion.objects.filter(devolucion__estado=Devolucion.APROBADA)
                .select_related("devolucion", "detalle_venta__producto").prefetch_related("recepciones"))
    filas = []
    for d in detalles:
        recibida = sum(r.cantidad_recibida for r in d.recepciones.all())
        if recibida < d.cantidad:
            filas.append({"d": d, "recibida": recibida, "faltan": d.cantidad - recibida})
    return render(request, "core/recepciones.html", {"filas": filas})


@rol_requerido(BODEGUERO)
def recepcion_form(request, detalle_id):
    detalle = get_object_or_404(DetalleDevolucion.objects.select_related("devolucion", "detalle_venta__producto"),
                                pk=detalle_id, devolucion__estado=Devolucion.APROBADA)
    form = RecepcionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.recibir_devolucion(request.user, detalle.id, form.cleaned_data["cantidad_recibida"],
                                        form.cleaned_data["cantidad_apta"], form.cleaned_data["observacion"])
            messages.success(request, "Recepción registrada. Solo las unidades aptas se reincorporaron al inventario.")
            return redirect("recepciones")
        except ErrorNegocio as exc:
            messages.error(request, str(exc))
    return render(request, "core/recepcion_form.html", {"form": form, "d": detalle})


# ------------------------------------------------------------------ monitoreo (solo superusuario tecnico)

@solo_superusuario
def monitoreo(request):
    log_path = settings.LOGS_DIR / "omnicanal.log"
    lineas = []
    if log_path.exists():
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lineas = f.readlines()[-40:]
    return render(request, "core/monitoreo.html", {
        "m": services.metricas(), "actividad": RegistroActividad.objects.all()[:30], "log": "".join(lineas)})
