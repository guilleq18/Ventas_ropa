from django import forms
from .models import Producto, Variante, StockSucursal, Categoria


class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["nombre", "descripcion", "categoria", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"autocomplete": "off"}),
            
            "categoria": forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Mostrar primero categorías activas (tu campo es 'activa')
        self.fields["categoria"].queryset = Categoria.objects.order_by("-activa", "nombre")


class VarianteForm(forms.ModelForm):
    # Solo para UI: se guarda en VarianteAtributo
    talle = forms.CharField(required=False)
    color = forms.CharField(required=False)

    class Meta:
        model = Variante
        fields = ["sku", "codigo_barras", "precio", "costo", "activo"]


class GeneradorVariantesForm(forms.Form):
    # Combinaciones
    talles = forms.CharField(
        required=True,
        help_text="Separados por coma. Ej: S,M,L,XL",
        widget=forms.TextInput(attrs={"placeholder": "S,M,L"})
    )
    colores = forms.CharField(
        required=True,
        help_text="Separados por coma. Ej: Negro,Blanco",
        widget=forms.TextInput(attrs={"placeholder": "Negro,Blanco"})
    )

    # ✅ Nuevo: código de barras base (opcional)
    # Si lo cargás, se asigna el MISMO EAN a todas las variantes generadas.

    codigo_barras_base = forms.CharField(
        required=False,
        max_length=64,
        label="Código de barras (EAN)",
        widget=forms.TextInput(attrs={"placeholder": "Ej: 7791234567890"})
    )

    # Datos por variante
    precio = forms.DecimalField(required=True, max_digits=12, decimal_places=2)
    costo = forms.DecimalField(required=True, max_digits=12, decimal_places=2)
    activo = forms.BooleanField(required=False, initial=True)


class StockSucursalForm(forms.ModelForm):
    class Meta:
        model = StockSucursal
        fields = ["sucursal", "cantidad"]
