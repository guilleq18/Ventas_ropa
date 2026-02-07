from django.db import models
from core.models import Sucursal


class Categoria(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    activa = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    # Producto “base” (sin talle/color). Las variantes cuelgan de acá.
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, null=True, blank=True)
    activo = models.BooleanField(default=True)

    # Opcionales (si querés precio por variante, lo ponemos en Variante)
    precio_base = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    costo_base = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["nombre"])]

    def __str__(self):
        return self.nombre


class Atributo(models.Model):
    # Ej: "Talle", "Color", "Material"
    nombre = models.CharField(max_length=60, unique=True)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre


class AtributoValor(models.Model):
    # Ej: atributo="Talle" valor="M"
    atributo = models.ForeignKey(Atributo, on_delete=models.CASCADE, related_name="valores")
    valor = models.CharField(max_length=60)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = ("atributo", "valor")
        indexes = [models.Index(fields=["atributo", "valor"])]

    def __str__(self):
        return f"{self.atributo.nombre}: {self.valor}"


class Variante(models.Model):
    # Variante concreta: Producto + combinación de valores
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="variantes")

    # SKU (código interno) / código de barras opcional
    sku = models.CharField(max_length=64, unique=True)
    codigo_barras = models.CharField(max_length=64, blank=True, db_index=True)


    # Permite precio/costo por variante (ropa suele necesitarlo)
    precio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    costo = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    activo = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["producto", "activo"]),
            models.Index(fields=["sku"]),
        ]

    def __str__(self):
        return f"{self.producto.nombre} ({self.sku})"


class VarianteAtributo(models.Model):
    # Tabla puente: cada Variante tiene N valores (talle, color, etc.)
    variante = models.ForeignKey(Variante, on_delete=models.CASCADE, related_name="atributos")
    atributo = models.ForeignKey(Atributo, on_delete=models.PROTECT)
    valor = models.ForeignKey(AtributoValor, on_delete=models.PROTECT)

    class Meta:
        unique_together = ("variante", "atributo")
        indexes = [models.Index(fields=["variante", "atributo"])]

    def __str__(self):
        return f"{self.variante.sku} - {self.atributo.nombre}={self.valor.valor}"


class StockSucursal(models.Model):
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE)
    variante = models.ForeignKey(Variante, on_delete=models.CASCADE)

    cantidad = models.IntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("sucursal", "variante")
        indexes = [
            models.Index(fields=["sucursal", "variante"]),
            models.Index(fields=["variante"]),
        ]

    def __str__(self):
        return f"{self.sucursal.nombre} - {self.variante.sku}: {self.cantidad}"
