from django.urls import path

from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("estado/", views.estado_sistema, name="estado_sistema"),
    path("ingresar/", views.login_view, name="login"),
    path("salir/", views.logout_view, name="logout"),
    path("registro/", views.registro, name="registro"),
    path("catalogo/", views.catalogo, name="catalogo"),
    path("producto/<int:producto_id>/", views.producto_detalle, name="producto_detalle"),

    # Cliente
    path("carrito/", views.carrito, name="carrito"),
    path("carrito/agregar/", views.carrito_agregar, name="carrito_agregar"),
    path("carrito/cantidad/<int:detalle_id>/", views.carrito_cantidad, name="carrito_cantidad"),
    path("carrito/quitar/<int:detalle_id>/", views.carrito_quitar, name="carrito_quitar"),
    path("compra/", views.checkout, name="checkout"),
    path("pago/<uuid:referencia>/", views.pago, name="pago"),
    path("mis-pedidos/", views.mis_pedidos, name="mis_pedidos"),
    path("mis-pedidos/<int:pedido_id>/", views.mi_pedido, name="mi_pedido"),
    path("mis-notificaciones/", views.mis_notificaciones, name="mis_notificaciones"),

    # Administrador
    path("productos/", views.productos, name="productos"),
    path("productos/nuevo/", views.producto_form, name="producto_nuevo"),
    path("productos/<int:producto_id>/editar/", views.producto_form, name="producto_editar"),
    path("stock-critico/", views.stock_critico, name="stock_critico"),

    # Vendedor
    path("ventas/nueva/", views.venta_nueva, name="venta_nueva"),
    path("ventas/", views.ventas, name="ventas"),
    path("ventas/<int:venta_id>/", views.venta_detalle, name="venta_detalle"),
    path("devoluciones/", views.devoluciones, name="devoluciones"),
    path("devoluciones/nueva/", views.devolucion_nueva, name="devolucion_nueva"),
    path("devoluciones/<int:devolucion_id>/", views.devolucion_detalle, name="devolucion_detalle"),

    # Bodeguero
    path("bodega/inventario/", views.inventario, name="inventario"),
    path("bodega/inventario/<int:producto_id>/ajustar/", views.inventario_ajustar, name="inventario_ajustar"),
    path("bodega/movimientos/", views.movimientos, name="movimientos"),
    path("bodega/avisos/", views.avisos, name="avisos"),
    path("bodega/avisos/<int:aviso_id>/leido/", views.aviso_leido, name="aviso_leido"),
    path("bodega/pedidos/", views.pedidos_bodega, name="pedidos_bodega"),
    path("bodega/pedidos/<int:pedido_id>/", views.pedido_bodega, name="pedido_bodega"),
    path("bodega/pedidos/<int:pedido_id>/estado/", views.pedido_cambiar_estado, name="pedido_cambiar_estado"),
    path("bodega/pedidos/<int:pedido_id>/operador/", views.pedido_operador, name="pedido_operador"),
    path("bodega/recepciones/", views.recepciones, name="recepciones"),
    path("bodega/recepciones/<int:detalle_id>/", views.recepcion_form, name="recepcion_form"),

    # Tecnico
    path("monitoreo/", views.monitoreo, name="monitoreo"),
]
