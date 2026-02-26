from decimal import Decimal
from django.db import transaction
from django.db.models import Max
from django.core.exceptions import ValidationError

from catalogo.models import StockSucursal
from core.models import Sucursal, AppSetting
from core.fiscal import get_empresa_condicion_fiscal
from .models import Venta
from admin_panel.services import permitir_vender_sin_stock


def _get_app_setting_str(key: str) -> str:
    row = AppSetting.objects.filter(key=key).only("value_str").first()
    return (getattr(row, "value_str", "") or "").strip()


def _snapshot_empresa_y_fiscal_en_venta(venta: Venta, items: list) -> None:
    total_neto = Decimal("0.00")
    total_iva = Decimal("0.00")
    total_otros = Decimal("0.00")

    for item in items:
        total_neto += Decimal(item.subtotal_sin_impuestos_nacionales or 0)
        total_iva += Decimal(item.subtotal_iva_contenido or 0)
        total_otros += Decimal(item.subtotal_otros_impuestos_nacionales_indirectos or 0)

    venta.empresa_nombre_snapshot = _get_app_setting_str("empresa.nombre")
    venta.empresa_razon_social_snapshot = _get_app_setting_str("empresa.razon_social")
    venta.empresa_cuit_snapshot = _get_app_setting_str("empresa.cuit")
    venta.empresa_direccion_snapshot = _get_app_setting_str("empresa.direccion")
    venta.empresa_condicion_fiscal_snapshot = get_empresa_condicion_fiscal()

    venta.fiscal_items_sin_impuestos_nacionales = total_neto.quantize(Decimal("0.01"))
    venta.fiscal_items_iva_contenido = total_iva.quantize(Decimal("0.01"))
    venta.fiscal_items_otros_impuestos_nacionales_indirectos = total_otros.quantize(Decimal("0.01"))


@transaction.atomic
def confirmar_venta(venta: Venta):
    if venta.estado != Venta.Estado.BORRADOR:
        raise ValidationError("Solo se puede confirmar una venta en borrador.")

    items = list(venta.items.select_related("variante").all())

    # Recalcular total
    total = Decimal("0.00")
    for item in items:
        # Fuerza persistencia del snapshot fiscal del item (y subtotal) por si cambió.
        item.save()
        total += item.subtotal

    _snapshot_empresa_y_fiscal_en_venta(venta, items)

    # Descontar stock por item (saltamos si está permitido vender sin stock)
    if not permitir_vender_sin_stock(venta.sucursal):
        for item in items:
            stock, _ = StockSucursal.objects.select_for_update().get_or_create(
                sucursal=venta.sucursal,
                variante=item.variante,
                defaults={"cantidad": 0},
            )

            if stock.cantidad < item.cantidad:
                raise ValidationError(
                    f"Stock insuficiente para {item.variante.sku}. Disponible: {stock.cantidad}, requerido: {item.cantidad}"
                )

            stock.cantidad -= item.cantidad
            stock.save()

    if not venta.numero_sucursal:
        # Serializa la asignación de correlativo por sucursal.
        Sucursal.objects.select_for_update().only("id").get(id=venta.sucursal_id)
        ultimo = (
            Venta.objects
            .filter(sucursal_id=venta.sucursal_id, numero_sucursal__isnull=False)
            .aggregate(max_num=Max("numero_sucursal"))
            .get("max_num")
            or 0
        )
        venta.numero_sucursal = int(ultimo) + 1

    venta.total = total
    venta.estado = Venta.Estado.CONFIRMADA
    venta.save()
