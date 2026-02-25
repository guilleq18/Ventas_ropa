from django.contrib import admin
from .models import SistemaConfig, UsuarioPerfil

@admin.register(SistemaConfig)
class SistemaConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "permitir_vender_sin_stock", "permitir_cambiar_precio_venta", "updated_at")


@admin.register(UsuarioPerfil)
class UsuarioPerfilAdmin(admin.ModelAdmin):
    list_display = ("user", "sucursal", "updated_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "sucursal__nombre")
    list_filter = ("sucursal",)
