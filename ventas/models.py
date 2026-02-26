from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Sucursal
from core.fiscal import desglosar_monto_final_gravado_con_iva
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
    CODIGO_DIGITS = 11

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
    numero_sucursal = models.PositiveBigIntegerField(null=True, blank=True)
    caja_sesion = models.ForeignKey(
        "caja.CajaSesion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ventas",
    )
    cajero = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ventas_realizadas",
    )
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

    # Snapshot fiscal/empresa para reimpresiones consistentes del ticket.
    empresa_nombre_snapshot = models.CharField(max_length=80, blank=True, default="")
    empresa_razon_social_snapshot = models.CharField(max_length=120, blank=True, default="")
    empresa_cuit_snapshot = models.CharField(max_length=20, blank=True, default="")
    empresa_direccion_snapshot = models.CharField(max_length=255, blank=True, default="")
    empresa_condicion_fiscal_snapshot = models.CharField(max_length=40, blank=True, default="")

    fiscal_items_sin_impuestos_nacionales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    fiscal_items_iva_contenido = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    fiscal_items_otros_impuestos_nacionales_indirectos = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "numero_sucursal"],
                name="ventas_numero_sucursal_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["sucursal", "numero_sucursal"]),
        ]

    @property
    def codigo_sucursal(self) -> str:
        if self.numero_sucursal:
            return f"V{int(self.numero_sucursal):0{self.CODIGO_DIGITS}d}"
        if self.id:
            return f"#{self.id}"
        return "s/n"

    def __str__(self):
        return f"Venta {self.codigo_sucursal} - {self.sucursal.nombre} - {self.fecha:%Y-%m-%d %H:%M}"


class VentaItem(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="items")
    variante = models.ForeignKey(Variante, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    iva_alicuota_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("21.00"))

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    precio_unitario_sin_impuestos_nacionales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    precio_unitario_iva_contenido = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    subtotal_sin_impuestos_nacionales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    subtotal_iva_contenido = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    subtotal_otros_impuestos_nacionales_indirectos = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    def _aplicar_snapshot_fiscal(self):
        alicuota = self.iva_alicuota_pct if self.iva_alicuota_pct is not None else Decimal("21.00")
        unitario = desglosar_monto_final_gravado_con_iva(
            self.precio_unitario or Decimal("0.00"),
            iva_alicuota_pct=alicuota,
        )
        subtotal = desglosar_monto_final_gravado_con_iva(
            self.subtotal or Decimal("0.00"),
            iva_alicuota_pct=alicuota,
        )

        self.precio_unitario_sin_impuestos_nacionales = unitario.monto_sin_impuestos_nacionales
        self.precio_unitario_iva_contenido = unitario.iva_contenido
        self.subtotal_sin_impuestos_nacionales = subtotal.monto_sin_impuestos_nacionales
        self.subtotal_iva_contenido = subtotal.iva_contenido
        self.subtotal_otros_impuestos_nacionales_indirectos = (
            subtotal.otros_impuestos_nacionales_indirectos
        )

    def save(self, *args, **kwargs):
        self.subtotal = self.cantidad * self.precio_unitario
        self._aplicar_snapshot_fiscal()

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {
                "subtotal",
                "iva_alicuota_pct",
                "precio_unitario_sin_impuestos_nacionales",
                "precio_unitario_iva_contenido",
                "subtotal_sin_impuestos_nacionales",
                "subtotal_iva_contenido",
                "subtotal_otros_impuestos_nacionales_indirectos",
            }
        super().save(*args, **kwargs)


class VentaPago(models.Model):
    class Tipo(models.TextChoices):
        CONTADO = "CONTADO", "Contado"
        DEBITO = "DEBITO", "Débito"
        CREDITO = "CREDITO", "Crédito"
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        QR = "QR", "QR"
        CUENTA_CORRIENTE = "CUENTA_CORRIENTE", "Cuenta corriente"  

    venta = models.ForeignKey("ventas.Venta", on_delete=models.CASCADE, related_name="pagos")

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    monto = models.DecimalField(max_digits=12, decimal_places=2)

    # Solo si tipo = CREDITO
    cuotas = models.PositiveSmallIntegerField(default=1)
    coeficiente = models.DecimalField(max_digits=8, decimal_places=4, default=1)   # 1.0000 = sin recargo
    recargo_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)   # ej 28.00
    recargo_monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    plan = models.ForeignKey(
        PlanCuotas,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        db_constraint=False,
        db_index=False,
    )

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
