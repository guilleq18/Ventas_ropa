from django.db import models, transaction
from django.utils import timezone

from core.models import Sucursal
from catalogo.models import Variante, StockSucursal


class Venta(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", "Borrador"
        CONFIRMADA = "CONFIRMADA", "Confirmada"
        ANULADA = "ANULADA", "Anulada"

    class MedioPago(models.TextChoices):
        EFECTIVO = "EFECTIVO", "Efectivo"
        DEBITO = "DEBITO", "Débito"
        CREDITO = "CREDITO", "Crédito"
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        CUENTA_CORRIENTE = "CUENTA_CORRIENTE", "Cuenta corriente"

    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT)
    fecha = models.DateTimeField(default=timezone.now)

    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.BORRADOR)
    medio_pago = models.CharField(max_length=30, choices=MedioPago.choices, default=MedioPago.EFECTIVO)

    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"Venta #{self.id} - {self.sucursal.nombre} - {self.fecha:%Y-%m-%d %H:%M}"


class VentaItem(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="items")
    variante = models.ForeignKey(Variante, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.subtotal = self.cantidad * self.precio_unitario
        super().save(*args, **kwargs)
