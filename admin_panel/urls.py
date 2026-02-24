from django.urls import path
from . import views

app_name = "admin_panel"

urlpatterns = [
     path("", views.dashboard, name="dashboard"),
    path("catalogo/", views.catalogo_home, name="catalogo_home"),
    path("ventas/", views.ventas_lista, name="ventas_lista"),
    path("usuarios/", views.usuarios_lista, name="usuarios_lista"),
    path("settings/", views.settings_view, name="settings"),
    path("tarjetas/", views.tarjetas_view, name="tarjetas"),
    path("ventas/<int:venta_id>/", views.ventas_detalle, name="ventas_detalle"),
    path("balances/", views.balances, name="balances"),
    path("cuentas-corrientes/", views.cc_lista, name="cc_lista"),
    path("cuentas-corrientes/<int:cuenta_id>/", views.cc_detalle, name="cc_detalle"),
    path("cuentas-corrientes/<int:cuenta_id>/toggle/", views.cc_toggle_activa, name="cc_toggle_activa"),
    path("cuentas-corrientes/<int:cuenta_id>/pago/", views.cc_registrar_pago, name="cc_registrar_pago"),
    path("cuentas-corrientes/nueva/", views.cc_crear, name="cc_crear"),



]
