from django.db import models
from django.utils import timezone

from core.models import Sucursal
from catalogo.models import Variante


class PlanCuotas(models.Model):
    tarjeta = models.CharField(max_length=30)  # VISA / MASTERCARD / AMEX / etc.
    cuotas = models.PositiveSmallIntegerField()  # 1,3,6,12...
    recargo_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = ("tarjeta", "cuotas")
        indexes = [models.Index(fields=["activo", "tarjeta", "cuotas"])]

    def __str__(self):
        return f"{self.tarjeta} {self.cuotas} cuotas ({self.recargo_pct}%)"


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
    cliente = models.ForeignKey(
    "cuentas_corrientes.Cliente",
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="ventas",
    )


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


class VentaPago(models.Model):
    class Tipo(models.TextChoices):
        CONTADO = "CONTADO", "Contado"
        DEBITO = "DEBITO", "Débito"
        CREDITO = "CREDITO", "Crédito"
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        QR = "QR", "QR"

    venta = models.ForeignKey("ventas.Venta", on_delete=models.CASCADE, related_name="pagos")

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    monto = models.DecimalField(max_digits=12, decimal_places=2)

    # Solo si tipo = CREDITO
    cuotas = models.PositiveSmallIntegerField(default=1)
    coeficiente = models.DecimalField(max_digits=8, decimal_places=4, default=1)   # 1.0000 = sin recargo
    recargo_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)   # ej 28.00
    recargo_monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    plan = models.ForeignKey(PlanCuotas, null=True, blank=True, on_delete=models.PROTECT)

    # Para transferencia / QR
    referencia = models.CharField(max_length=120, blank=True)

    # Datos del POS (opcionales)
    pos_proveedor = models.CharField(max_length=40, blank=True)
    pos_terminal_id = models.CharField(max_length=40, blank=True)
    pos_lote = models.CharField(max_length=40, blank=True)
    pos_cupon = models.CharField(max_length=40, blank=True)
    pos_autorizacion = models.CharField(max_length=40, blank=True)
    pos_marca = models.CharField(max_length=20, blank=True)
    pos_ultimos4 = models.CharField(max_length=4, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["venta", "tipo"]),
        ]

    def __str__(self):
        return f"{self.venta_id} - {self.tipo} ${self.monto}"
