from django.urls import path
from . import views

app_name = "caja"

urlpatterns = [
    # =========================
    # POS
    # =========================
    path("", views.pos, name="pos"),

    # =========================
    # Buscar / Scanner
    # =========================
    path("buscar/", views.buscar_variantes, name="buscar"),
    path("scan/", views.scan_add, name="scan_add"),

    # =========================
    # Carrito
    # =========================
    path("carrito/agregar/<int:variante_id>/", views.carrito_agregar, name="carrito_agregar"),
    path("carrito/qty/<int:variante_id>/", views.carrito_set_qty, name="carrito_set_qty"),
    path("carrito/quitar/<int:variante_id>/", views.carrito_quitar, name="carrito_quitar"),
    path("carrito/vaciar/", views.carrito_vaciar, name="carrito_vaciar"),

    # =========================
    # Confirmar / Ticket
    # =========================
    path("confirmar/", views.confirmar, name="confirmar"),
    path("ticket/<int:venta_id>/", views.ticket, name="ticket"),

    # =========================
    # Pagos: cuotas
    # =========================
    path("pagos/cuotas/<int:idx>/", views.pagos_cuotas, name="pagos_cuotas"),

    # =========================
    # Pagos: modal (NUEVO flujo)
    # =========================
    path("pagos/modal/open/", views.pagos_modal_open, name="pagos_modal_open"),
    path("pagos/modal/add/", views.pagos_add_modal, name="pagos_add_modal"),
    path("pagos/modal/set/<int:idx>/", views.pagos_set_modal, name="pagos_set_modal"),
    path("pagos/modal/del/<int:idx>/", views.pagos_del_modal, name="pagos_del_modal"),

    # ✅ Alias por compatibilidad (si algún template viejo lo llama)
    path("pagos/modal/save/<int:idx>/", views.pagos_set_modal, name="pagos_save_modal"),

    # =========================
    # Pagos: acciones desde la tabla del card
    # =========================
    path("pagos/table/del/<int:idx>/", views.pagos_del_table, name="pagos_del_table"),
]
