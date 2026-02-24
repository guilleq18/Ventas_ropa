from django.db import models

class SistemaConfig(models.Model):
    permitir_vender_sin_stock = models.BooleanField(default=False)
    permitir_cambiar_precio_venta = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración del sistema"
        verbose_name_plural = "Configuración del sistema"

    def __str__(self):
        return "Configuración del sistema"
