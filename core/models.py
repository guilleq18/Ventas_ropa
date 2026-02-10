from django.db import models

class AppSetting(models.Model):
    """
    Configuración del sistema (clave/valor).
    """
    key = models.CharField(max_length=80, unique=True)
    value_bool = models.BooleanField(null=True, blank=True)
    value_int = models.IntegerField(null=True, blank=True)
    value_str = models.CharField(max_length=255, null=True, blank=True)

    description = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key

class Sucursal(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    direccion = models.CharField(max_length=150, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    activa = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre
