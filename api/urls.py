"""
URL configuration for api project.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("core_api.urls")),
    # GET /metrics para o Prometheus. Fica fora de /api/, então o nginx da VM
    # (que só repassa /api/) não publica essa rota na rede.
    path("", include("django_prometheus.urls")),
]
