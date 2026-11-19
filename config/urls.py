from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Administracion tecnica de Django: solo superusuarios (no es el perfil Administrador comercial).
    path("admin-tecnico/", admin.site.urls),
    path("", include("core.urls")),
]

# En desarrollo local, Django sirve las fotos de los productos desde la carpeta media/.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = "core.views.error_403"
