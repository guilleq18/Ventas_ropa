from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError

from catalogo.models import StockSucursal
from .models import Venta
from admin_panel.services import permitir_vender_sin_stock


@transaction.atomic
def confirmar_venta(venta: Venta):
    if venta.estado != Venta.Estado.BORRADOR:
        raise ValidationError("Solo se puede confirmar una venta en borrador.")

    # Recalcular total
    total = Decimal("0")
    for item in venta.items.select_related("variante").all():
        total += item.subtotal

    # Descontar stock por item (saltamos si está permitido vender sin stock)
    if not permitir_vender_sin_stock():
        for item in venta.items.select_related("variante").all():
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

    venta.total = total
    venta.estado = Venta.Estado.CONFIRMADA
    venta.save()
