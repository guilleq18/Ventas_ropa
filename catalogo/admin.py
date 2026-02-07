from django.contrib import admin
from .models import (
    Categoria, Producto,
    Atributo, AtributoValor,
    Variante, VarianteAtributo,
    StockSucursal
)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activa")
    list_filter = ("activa",)
    search_fields = ("nombre",)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "activo", "precio_base")
    list_filter = ("activo", "categoria")
    search_fields = ("nombre",)


@admin.register(Atributo)
class AtributoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo")
    list_filter = ("activo",)
    search_fields = ("nombre",)


@admin.register(AtributoValor)
class AtributoValorAdmin(admin.ModelAdmin):
    list_display = ("atributo", "valor", "activo")
    list_filter = ("atributo", "activo")
    search_fields = ("valor",)


class VarianteAtributoInline(admin.TabularInline):
    model = VarianteAtributo
    extra = 0


@admin.register(Variante)
class VarianteAdmin(admin.ModelAdmin):
    list_display = ("sku", "producto", "precio", "activo")
    list_filter = ("activo", "producto")
    search_fields = ("sku", "producto__nombre")
    inlines = [VarianteAtributoInline]


@admin.register(StockSucursal)
class StockSucursalAdmin(admin.ModelAdmin):
    list_display = ("sucursal", "variante", "cantidad", "updated_at")
    list_filter = ("sucursal",)
    search_fields = ("variante__sku", "variante__producto__nombre")
