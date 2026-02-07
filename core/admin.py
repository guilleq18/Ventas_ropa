from django.contrib import admin
from .models import Sucursal

@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ("nombre", "direccion", "telefono", "activa")
    list_filter = ("activa",)
    search_fields = ("nombre", "direccion")
