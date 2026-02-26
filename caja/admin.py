from django.contrib import admin

from .models import CajaSesion


@admin.register(CajaSesion)
class CajaSesionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "sucursal",
        "cajero_apertura",
        "abierta_en",
        "cajero_cierre",
        "cerrada_en",
    )
    list_filter = ("sucursal", "abierta_en", "cerrada_en")
    search_fields = (
        "sucursal__nombre",
        "cajero_apertura__username",
        "cajero_cierre__username",
    )
    date_hierarchy = "abierta_en"
    list_select_related = ("sucursal", "cajero_apertura", "cajero_cierre")
