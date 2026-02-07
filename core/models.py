from django.db import models

class Sucursal(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    direccion = models.CharField(max_length=150, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    activa = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre
