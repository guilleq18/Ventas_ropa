from django.urls import path
from . import views

app_name = "admin_panel"

urlpatterns = [
     path("", views.dashboard, name="dashboard"),
    path("catalogo/", views.catalogo_home, name="catalogo_home"),
    path("ventas/", views.ventas_lista, name="ventas_lista"),
    path("usuarios/", views.usuarios_lista, name="usuarios_lista"),
    path("settings/", views.settings_view, name="settings"),
    path("ventas/<int:venta_id>/", views.ventas_detalle, name="ventas_detalle"),
    path("balances/", views.balances, name="balances"),

]