from django.conf import settings
from django.db import models

from core.models import Sucursal

class SistemaConfig(models.Model):
    permitir_vender_sin_stock = models.BooleanField(default=False)
    permitir_cambiar_precio_venta = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración del sistema"
        verbose_name_plural = "Configuración del sistema"

    def __str__(self):
        return "Configuración del sistema"


class UsuarioPerfil(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="panel_profile",
    )
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios_asignados",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"

    def __str__(self):
        suc = self.sucursal.nombre if self.sucursal_id else "Sin sucursal"
        return f"{self.user.username} - {suc}"
