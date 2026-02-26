from django.urls import path
from django.contrib.auth.decorators import login_required, permission_required
from . import views

app_name = "admin_panel"

ADMIN_PANEL_GATE_PERMISSION = "admin_panel.view_usuarioperfil"


def _admin_panel_protect(view_func):
    """
    Bloquea acceso al Admin Panel a usuarios sin permiso explícito del app.
    Superusers pasan por has_perm automáticamente.
    """
    return login_required(
        permission_required(ADMIN_PANEL_GATE_PERMISSION, raise_exception=True)(view_func)
    )


urlpatterns = [
    path("", _admin_panel_protect(views.dashboard), name="dashboard"),
    path("catalogo/", _admin_panel_protect(views.catalogo_home), name="catalogo_home"),
    path("ventas/", _admin_panel_protect(views.ventas_lista), name="ventas_lista"),
    path("usuarios/", _admin_panel_protect(views.usuarios_lista), name="usuarios_lista"),
    path("settings/", _admin_panel_protect(views.settings_view), name="settings"),
    path("empresa/", _admin_panel_protect(views.empresa_datos), name="empresa_datos"),
    path("tarjetas/", _admin_panel_protect(views.tarjetas_view), name="tarjetas"),
    path("ventas/<int:venta_id>/", _admin_panel_protect(views.ventas_detalle), name="ventas_detalle"),
    path("balances/", _admin_panel_protect(views.balances), name="balances"),
    path("cuentas-corrientes/", _admin_panel_protect(views.cc_lista), name="cc_lista"),
    path("cuentas-corrientes/<int:cuenta_id>/", _admin_panel_protect(views.cc_detalle), name="cc_detalle"),
    path("cuentas-corrientes/<int:cuenta_id>/toggle/", _admin_panel_protect(views.cc_toggle_activa), name="cc_toggle_activa"),
    path("cuentas-corrientes/<int:cuenta_id>/pago/", _admin_panel_protect(views.cc_registrar_pago), name="cc_registrar_pago"),
    path("cuentas-corrientes/nueva/", _admin_panel_protect(views.cc_crear), name="cc_crear"),



]
