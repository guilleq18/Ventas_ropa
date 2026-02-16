from django.contrib import admin, messages
from django.db.models import Sum, Q
from django.utils.html import format_html

from .models import Cliente, CuentaCorriente, MovimientoCuentaCorriente


class MovimientoInline(admin.TabularInline):
    model = MovimientoCuentaCorriente
    extra = 0
    fields = ("fecha", "tipo", "monto", "venta", "referencia", "observacion")
    readonly_fields = ()
    ordering = ("-fecha", "-id")


@admin.action(description="Crear Cuenta Corriente para clientes seleccionados")
def crear_cuentas_corrientes(modeladmin, request, queryset):
    creadas = 0
    for c in queryset:
        if not hasattr(c, "cuenta_corriente"):
            CuentaCorriente.objects.create(cliente=c)
            creadas += 1
    messages.success(request, f"Listo: creadas {creadas} cuentas corrientes.")


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("dni", "apellido", "nombre", "telefono", "activo", "tiene_cc", "saldo_cc")
    search_fields = ("dni", "apellido", "nombre", "telefono")
    list_filter = ("activo",)
    actions = [crear_cuentas_corrientes]
    ordering = ("apellido", "nombre")

    def tiene_cc(self, obj):
        return hasattr(obj, "cuenta_corriente")
    tiene_cc.boolean = True
    tiene_cc.short_description = "CC"

    def saldo_cc(self, obj):
        if not hasattr(obj, "cuenta_corriente"):
            return "-"
        return obj.cuenta_corriente.saldo()
    saldo_cc.short_description = "Saldo"


@admin.register(CuentaCorriente)
class CuentaCorrienteAdmin(admin.ModelAdmin):
    list_display = ("dni", "cliente", "activa", "saldo_admin", "creada_en")
    list_filter = ("activa",)
    search_fields = ("cliente__dni", "cliente__apellido", "cliente__nombre")
    list_select_related = ("cliente",)
    inlines = [MovimientoInline]
    readonly_fields = ("creada_en",)

    def dni(self, obj):
        return obj.cliente.dni
    dni.short_description = "DNI"

    def saldo_admin(self, obj):
        return obj.saldo()
    saldo_admin.short_description = "Saldo"


@admin.register(MovimientoCuentaCorriente)
class MovimientoCuentaCorrienteAdmin(admin.ModelAdmin):
    list_display = ("dni", "tipo", "monto", "fecha", "venta_link", "referencia")
    list_filter = ("tipo", "fecha")
    search_fields = ("cuenta__cliente__dni", "cuenta__cliente__apellido", "cuenta__cliente__nombre", "referencia")
    date_hierarchy = "fecha"
    list_select_related = ("cuenta", "cuenta__cliente", "venta")

    def dni(self, obj):
        return obj.cuenta.cliente.dni
    dni.short_description = "DNI"

    def venta_link(self, obj):
        if not obj.venta_id:
            return "-"
        # Link al change del admin de ventas.Venta
        return format_html('<a href="/admin/ventas/venta/{}/change/">Venta #{}</a>', obj.venta_id, obj.venta_id)
    venta_link.short_description = "Venta"

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        qs = self.get_queryset(request)
        extra_context["tot_debitos"] = qs.filter(tipo=MovimientoCuentaCorriente.Tipo.DEBITO).aggregate(s=Sum("monto"))["s"] or 0
        extra_context["tot_creditos"] = qs.filter(tipo=MovimientoCuentaCorriente.Tipo.CREDITO).aggregate(s=Sum("monto"))["s"] or 0
        return super().changelist_view(request, extra_context=extra_context)
