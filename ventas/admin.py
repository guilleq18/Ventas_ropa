from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import Venta, VentaItem, VentaPago, PlanCuotas
from .services import confirmar_venta


@admin.register(PlanCuotas)
class PlanCuotasAdmin(admin.ModelAdmin):
    list_display = ("tarjeta", "cuotas", "recargo_pct", "activo")
    list_filter = ("tarjeta", "activo")
    search_fields = ("tarjeta",)
    ordering = ("tarjeta", "cuotas")


class VentaItemInline(admin.TabularInline):
    model = VentaItem
    extra = 0


class VentaPagoInline(admin.TabularInline):
    model = VentaPago
    extra = 0


@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "sucursal",
        "fecha",
        "estado",
        "medio_pago",
        "cliente_dni",
        "cliente_nombre",
        "total",
    )
    list_filter = ("estado", "sucursal", "medio_pago")
    date_hierarchy = "fecha"
    search_fields = (
        "id",
        "cliente__dni",
        "cliente__apellido",
        "cliente__nombre",
    )
    list_select_related = ("sucursal", "cliente")

    inlines = [VentaItemInline, VentaPagoInline]
    actions = ["accion_confirmar"]

    def cliente_dni(self, obj):
        return obj.cliente.dni if obj.cliente else "-"
    cliente_dni.short_description = "DNI"

    def cliente_nombre(self, obj):
        if not obj.cliente:
            return "-"
        return f"{obj.cliente.apellido}, {obj.cliente.nombre}"
    cliente_nombre.short_description = "Cliente"

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
