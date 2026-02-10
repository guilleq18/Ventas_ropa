from django.urls import path
from . import views

app_name = "caja"


urlpatterns = [
    path("", views.pos, name="pos"),
    path("buscar/", views.buscar_variantes, name="buscar"),
    path("scan/", views.scan_add, name="scan_add"),

    path("carrito/agregar/<int:variante_id>/", views.carrito_agregar, name="carrito_agregar"),
    path("carrito/qty/<int:variante_id>/", views.carrito_set_qty, name="carrito_set_qty"),
    path("carrito/quitar/<int:variante_id>/", views.carrito_quitar, name="carrito_quitar"),
    path("carrito/vaciar/", views.carrito_vaciar, name="carrito_vaciar"),

    path("pagos/add/", views.pagos_add, name="pagos_add"),
    path("pagos/del/<int:idx>/", views.pagos_del, name="pagos_del"),
    path("pagos/set/<int:idx>/", views.pagos_set, name="pagos_set"),

    path("confirmar/", views.confirmar, name="confirmar"),
    path("pagos/cuotas/<int:idx>/", views.pagos_cuotas, name="pagos_cuotas"),
    path("", views.pos, name="pos"),
    path("ticket/<int:venta_id>/", views.ticket, name="ticket"),


]