from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import Sucursal


class CajaSesion(models.Model):
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.PROTECT,
        related_name="cajas_sesiones",
    )
    cajero_apertura = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cajas_abiertas",
    )
    abierta_en = models.DateTimeField(default=timezone.now)

    cajero_cierre = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cajas_cerradas",
    )
    cerrada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Sesión de caja"
        verbose_name_plural = "Sesiones de caja"
        ordering = ["-abierta_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal"],
                condition=Q(cerrada_en__isnull=True),
                name="caja_unica_abierta_por_sucursal",
            )
        ]
        indexes = [
            models.Index(fields=["sucursal", "abierta_en"]),
            models.Index(fields=["cajero_apertura", "abierta_en"]),
        ]

    @property
    def esta_abierta(self) -> bool:
        return self.cerrada_en is None

    def cerrar(self, user=None):
        if self.cerrada_en is None:
            self.cerrada_en = timezone.now()
        if user is not None:
            self.cajero_cierre = user

    def __str__(self):
        estado = "Abierta" if self.esta_abierta else "Cerrada"
        return f"Caja {self.sucursal} - {self.cajero_apertura} - {estado}"
