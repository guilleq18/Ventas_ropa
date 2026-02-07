from django.urls import path
from . import views

app_name = "caja"

urlpatterns = [
    path("", views.pos, name="pos"),
    path("buscar/", views.buscar_variantes, name="buscar"),
    path("carrito/agregar/<int:variante_id>/", views.carrito_agregar, name="carrito_agregar"),
    path("carrito/qty/<int:variante_id>/", views.carrito_set_qty, name="carrito_set_qty"),
    path("carrito/quitar/<int:variante_id>/", views.carrito_quitar, name="carrito_quitar"),
    path("confirmar/", views.confirmar, name="confirmar"),
    path("scan/", views.scan_add, name="scan_add"),

]
