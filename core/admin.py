from django.contrib import admin

from . import models

# Administracion TECNICA (solo superusuarios). No es el perfil "Administrador" comercial.
for modelo in (
    models.Sucursal, models.Perfil, models.Producto, models.Existencia, models.OperacionCompra, models.Pago,
    models.Venta, models.Pedido, models.Devolucion, models.MovimientoInventario, models.NotificacionCliente,
    models.RegistroActividad, models.AvisoBodega, models.OperadorLogistico,
):
    admin.site.register(modelo)
