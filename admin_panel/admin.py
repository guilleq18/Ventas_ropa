from django.contrib import admin
from .models import SistemaConfig

@admin.register(SistemaConfig)
class SistemaConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "permitir_vender_sin_stock", "permitir_cambiar_precio_venta", "updated_at")
