from django.urls import path
from . import views

app_name = "catalogo"

urlpatterns = [
    path("", views.productos, name="productos"),
    path("buscar/", views.productos_buscar, name="productos_buscar"),

    path("producto/nuevo/", views.producto_nuevo, name="producto_nuevo"),
    path("producto/<int:pk>/editar/", views.producto_editar, name="producto_editar"),
    path("producto/<int:pk>/toggle/", views.producto_toggle, name="producto_toggle"),

    path("producto/<int:producto_id>/variantes/", views.variantes_panel, name="variantes_panel"),
    path("producto/<int:producto_id>/variante/nueva/", views.variante_nueva, name="variante_nueva"),
    path("variante/<int:pk>/editar/", views.variante_editar, name="variante_editar"),
    path("variante/<int:pk>/eliminar/", views.variante_eliminar, name="variante_eliminar"),

    path("producto/<int:producto_id>/variantes/generador/", views.variantes_generador, name="variantes_generador"),

    path("variante/<int:variante_id>/stock/", views.stock_modal, name="stock_modal"),
    path("producto/<int:producto_id>/stock/planilla/", views.stock_planilla, name="stock_planilla"),
    path("stock/set/", views.stock_set, name="stock_set"),
    path("variante/<int:variante_id>/stock/detalle/", views.variante_stock_detalle, name="variante_stock_detalle"),
    path("variante/stock/set/", views.variante_stock_set, name="variante_stock_set"),
    path("variante/<int:variante_id>/stock/detalle/", views.variante_stock_detalle, name="variante_stock_detalle"),
    path("variante/stock/set/", views.variante_stock_set, name="variante_stock_set"),




]
