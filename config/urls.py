from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Administracion tecnica de Django: solo superusuarios (no es el perfil Administrador comercial).
    path("admin-tecnico/", admin.site.urls),
    path("", include("core.urls")),
]

handler403 = "core.views.error_403"
