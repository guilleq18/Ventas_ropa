from django.db import models
from django.db.models import Sum, Q
from django.utils import timezone


class Cliente(models.Model):
    dni = models.CharField("DNI", max_length=20, unique=True, db_index=True)

    nombre = models.CharField(max_length=80)
    apellido = models.CharField(max_length=80)

    telefono = models.CharField(max_length=40, blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)

    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["dni"]),
            models.Index(fields=["apellido", "nombre"]),
        ]

    def __str__(self):
        return f"{self.apellido}, {self.nombre} ({self.dni})"


class CuentaCorriente(models.Model):
    cliente = models.OneToOneField(
        Cliente,
        on_delete=models.PROTECT,
        related_name="cuenta_corriente",
    )

    activa = models.BooleanField(default=True)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cuenta corriente"
        verbose_name_plural = "Cuentas corrientes"
        indexes = [models.Index(fields=["activa"])]

    def __str__(self):
        return f"CC {self.cliente.dni} - {self.cliente.apellido}, {self.cliente.nombre}"

    def saldo(self):
        """
        Saldo = Débitos - Créditos (saldo global).
        """
        agg = self.movimientos.aggregate(
            debitos=Sum("monto", filter=Q(tipo=MovimientoCuentaCorriente.Tipo.DEBITO)),
            creditos=Sum("monto", filter=Q(tipo=MovimientoCuentaCorriente.Tipo.CREDITO)),
        )
        deb = agg["debitos"] or 0
        cred = agg["creditos"] or 0
        return deb - cred


class MovimientoCuentaCorriente(models.Model):
    class Tipo(models.TextChoices):
        DEBITO = "DEBITO", "Débito (Venta)"
        CREDITO = "CREDITO", "Crédito (Pago)"

    cuenta = models.ForeignKey(
        CuentaCorriente,
        on_delete=models.CASCADE,
        related_name="movimientos"
    )

    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    monto = models.DecimalField(max_digits=12, decimal_places=2)

    fecha = models.DateTimeField(default=timezone.now)

    # Para trazabilidad: si es débito por venta, guardamos la venta
    venta = models.ForeignKey(
        "ventas.Venta",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movimientos_cc"
    )

    # Para pagos o notas internas (transferencia, recibo, etc.)
    referencia = models.CharField(max_length=120, blank=True)
    observacion = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de cuenta corriente"
        verbose_name_plural = "Movimientos de cuenta corriente"
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["cuenta", "tipo", "fecha"]),
            models.Index(fields=["venta"]),
        ]

    def __str__(self):
        signo = "+" if self.tipo == self.Tipo.DEBITO else "-"
        return f"{self.cuenta.cliente.dni} {signo}${self.monto} {self.get_tipo_display()}"

    def clean(self):
        """
        Reglas simples:
        - Si es DEBITO, lo normal es que tenga venta.
        - Si es CREDITO, lo normal es que NO tenga venta.
        No lo hacemos obligatorio a nivel DB para permitir ajustes/manuales,
        pero sí lo validamos para evitar errores comunes.
        """
        from django.core.exceptions import ValidationError

        if self.tipo == self.Tipo.DEBITO and self.venta is None:
            raise ValidationError("Un movimiento DÉBITO debería estar asociado a una Venta.")
        if self.tipo == self.Tipo.CREDITO and self.venta is not None:
            raise ValidationError("Un movimiento CRÉDITO no debería estar asociado a una Venta.")

    def save(self, *args, **kwargs):
        # Ejecuta validaciones del clean() también al guardar desde código/admin
        self.full_clean()
        super().save(*args, **kwargs)
