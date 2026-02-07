from django.contrib import admin
from django.core.exceptions import ValidationError
from django.contrib import messages

from .models import Venta, VentaItem
from .services import confirmar_venta


class VentaItemInline(admin.TabularInline):
    model = VentaItem
    extra = 0


@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    list_display = ("id", "sucursal", "fecha", "estado", "medio_pago", "total")
    list_filter = ("estado", "sucursal", "medio_pago")
    date_hierarchy = "fecha"
    inlines = [VentaItemInline]
    actions = ["accion_confirmar"]

    @admin.action(description="Confirmar ventas seleccionadas (descuenta stock)")
    def accion_confirmar(self, request, queryset):
        ok = 0
        for venta in queryset:
            try:
                confirmar_venta(venta)
                ok += 1
            except ValidationError as e:
                messages.error(request, f"Venta #{venta.id}: {e}")
        if ok:
            messages.success(request, f"Confirmadas: {ok}")
