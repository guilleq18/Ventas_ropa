from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Sum, Count, Avg, F, Q
from django.shortcuts import render
from django.utils import timezone


# =========================
# CONFIGURACIÓN (ajustar)
# =========================
# Cambiá estos imports según tus modelos reales.
# Ejemplos comunes:
# - ventas/models.py: Venta (cabecera), VentaItem (detalle)
# - caja/models.py: MovimientoCaja
# - catalogo/models.py: Producto
try:
    from ventas.models import Venta, VentaItem
except Exception:
    Venta = None
    VentaItem = None

try:
    from caja.models import MovimientoCaja
except Exception:
    MovimientoCaja = None

try:
    from catalogo.models import Producto
except Exception:
    Producto = None


# Si tus campos se llaman distinto, ajustalos acá.
FIELDS = {
    # Venta (cabecera)
    "venta_fecha": "fecha",         # DateTimeField o DateField (ej: created_at, fecha)
    "venta_total": "total",         # Decimal/Float/Int (ej: total_final, total)
    "venta_estado": "estado",       # opcional (ej: estado), si no existe se ignora
    "venta_estado_ok": "OK",        # valor que indica venta válida (si usás estados)
    "venta_anulada": "anulada",     # opcional boolean (si existe)

    # Caja movimientos
    "caja_fecha": "fecha",          # DateTimeField o DateField
    "caja_tipo": "tipo",            # 'I'/'E' o 'INGRESO'/'EGRESO' (ajustar abajo)
    "caja_monto": "monto",          # Decimal
    "caja_tipo_ingreso": "INGRESO", # o "I"
    "caja_tipo_egreso": "EGRESO",   # o "E"

    # Producto
    "prod_stock": "stock",          # int
    "prod_minimo": "stock_minimo",  # int
    "prod_activo": "activo",        # opcional bool
}

SENSITIVE_DASHBOARD_PERMISSION = "admin_panel.view_usuarioperfil"
CAJA_POS_PERMISSION = "ventas.usar_caja_pos"


def _field_exists(model, field_name: str) -> bool:
    if not model or not field_name:
        return False
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _can_view_sensitive_dashboard(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.has_perm(SENSITIVE_DASHBOARD_PERMISSION)


def _can_access_caja(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.has_perm(CAJA_POS_PERMISSION)


def _get_user_sucursal(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        profile = user.panel_profile
    except ObjectDoesNotExist:
        return None
    if profile and profile.sucursal_id:
        return profile.sucursal
    return None


@login_required
def dashboard(request):
    hoy = timezone.localdate()
    can_view_sensitive_dashboard = _can_view_sensitive_dashboard(request.user)
    can_access_caja = _can_access_caja(request.user)
    can_access_admin_panel = can_view_sensitive_dashboard
    user_sucursal = _get_user_sucursal(request.user)

    # Defaults
    ventas_hoy = 0
    ingresos_hoy = 0
    ticket_promedio = 0
    stock_bajo = 0

    caja_saldo = 0
    caja_ingresos_hoy = 0
    caja_egresos_hoy = 0

    ultimas_ventas = []
    alertas = []

    # =========================
    # VENTAS
    # =========================
    if Venta:
        qs = Venta.objects.all()
        estado_ok = FIELDS["venta_estado_ok"]

        # Compatibilidad con modelos reales del proyecto (CONFIRMADA en ventas.Venta).
        try:
            estado_ok = getattr(Venta.Estado, "CONFIRMADA", estado_ok)
        except Exception:
            pass

        # Vendedor básico: limitar alcance para evitar exposición global.
        if not can_view_sensitive_dashboard:
            if user_sucursal and _field_exists(Venta, "sucursal"):
                qs = qs.filter(sucursal=user_sucursal)
            elif _field_exists(Venta, "cajero"):
                qs = qs.filter(cajero=request.user)
            else:
                qs = qs.none()

        # Filtrar por hoy (si el campo es DateTime, usamos __date; si es Date, usamos exacto)
        venta_fecha = FIELDS["venta_fecha"]
        if _field_exists(Venta, venta_fecha):
            # Intentamos filtrar como DateTime primero
            try:
                qs_hoy = qs.filter(**{f"{venta_fecha}__date": hoy})
            except Exception:
                qs_hoy = qs.filter(**{venta_fecha: hoy})
        else:
            qs_hoy = qs.none()

        # Excluir anuladas si existe el campo
        if _field_exists(Venta, FIELDS["venta_anulada"]):
            qs_hoy = qs_hoy.filter(**{FIELDS["venta_anulada"]: False})

        # Filtrar estado OK si existe
        if _field_exists(Venta, FIELDS["venta_estado"]):
            qs_hoy = qs_hoy.filter(**{FIELDS["venta_estado"]: estado_ok})

        # KPIs
        ventas_hoy = qs_hoy.aggregate(c=Count("id"))["c"] or 0

        venta_total = FIELDS["venta_total"]
        if _field_exists(Venta, venta_total) and can_view_sensitive_dashboard:
            ingresos_hoy = qs_hoy.aggregate(s=Sum(venta_total))["s"] or 0
            ticket_promedio = (ingresos_hoy / ventas_hoy) if ventas_hoy else 0

        # Actividad reciente: mostrar siempre lo de la sucursal asignada.
        qs_actividad = qs
        if _field_exists(Venta, "sucursal") and user_sucursal:
            qs_actividad = qs_actividad.filter(sucursal=user_sucursal)
        elif not can_view_sensitive_dashboard:
            qs_actividad = qs_actividad.none()

        # Últimas ventas
        # Intentamos ordenar por fecha si existe
        if _field_exists(Venta, venta_fecha):
            qs_last = qs_actividad.order_by(f"-{venta_fecha}")[:10 if can_view_sensitive_dashboard else 6]
        else:
            qs_last = qs_actividad.order_by("-id")[:10 if can_view_sensitive_dashboard else 6]

        # Armado “safe” (si no existen campos, mostramos lo que se pueda)
        for v in qs_last:
            # fecha
            fecha_val = getattr(v, venta_fecha, None)
            if fecha_val:
                try:
                    fecha_txt = timezone.localtime(fecha_val).strftime("%d/%m %H:%M")
                except Exception:
                    fecha_txt = str(fecha_val)
            else:
                fecha_txt = "-"

            # nro: si tenés numero/folio, podés cambiar esto
            nro_txt = f"#{v.id}"

            # cliente: si tenés FK cliente o nombre, ajustalo
            cliente_txt = getattr(v, "cliente", None)
            if cliente_txt is None:
                cliente_txt = "Consumidor Final" if can_view_sensitive_dashboard else "-"
            else:
                cliente_txt = str(cliente_txt) if can_view_sensitive_dashboard else "-"

            total_val = (
                getattr(v, venta_total, 0)
                if (_field_exists(Venta, venta_total) and can_view_sensitive_dashboard)
                else 0
            )
            estado_val = getattr(v, FIELDS["venta_estado"], "OK") if _field_exists(Venta, FIELDS["venta_estado"]) else "OK"

            ultimas_ventas.append({
                "id": v.id,
                "fecha": fecha_txt,
                "nro": nro_txt,
                "cliente": cliente_txt,
                "total": total_val or 0,
                "estado": estado_val,
            })

    # =========================
    # STOCK BAJO
    # =========================
    if Producto and can_view_sensitive_dashboard:
        prod_stock = FIELDS["prod_stock"]
        prod_min = FIELDS["prod_minimo"]

        if _field_exists(Producto, prod_stock) and _field_exists(Producto, prod_min):
            qs_p = Producto.objects.all()

            # Filtrar activos si existe
            if _field_exists(Producto, FIELDS["prod_activo"]):
                qs_p = qs_p.filter(**{FIELDS["prod_activo"]: True})

            stock_bajo = qs_p.filter(**{f"{prod_stock}__lte": F(prod_min)}).count()

            if stock_bajo > 0:
                alertas.append({"tipo": "stock", "texto": f"{stock_bajo} productos con stock bajo"})

    # =========================
    # CAJA
    # =========================
    if MovimientoCaja and can_view_sensitive_dashboard:
        caja_fecha = FIELDS["caja_fecha"]
        caja_tipo = FIELDS["caja_tipo"]
        caja_monto = FIELDS["caja_monto"]

        qs_c = MovimientoCaja.objects.all()

        # Hoy
        if _field_exists(MovimientoCaja, caja_fecha):
            try:
                qs_c_hoy = qs_c.filter(**{f"{caja_fecha}__date": hoy})
            except Exception:
                qs_c_hoy = qs_c.filter(**{caja_fecha: hoy})
        else:
            qs_c_hoy = qs_c.none()

        # Ingresos/Egresos del día
        if _field_exists(MovimientoCaja, caja_tipo) and _field_exists(MovimientoCaja, caja_monto):
            caja_ingresos_hoy = qs_c_hoy.filter(**{caja_tipo: FIELDS["caja_tipo_ingreso"]}).aggregate(s=Sum(caja_monto))["s"] or 0
            caja_egresos_hoy = qs_c_hoy.filter(**{caja_tipo: FIELDS["caja_tipo_egreso"]}).aggregate(s=Sum(caja_monto))["s"] or 0

            # Saldo “histórico” = ingresos - egresos
            ing_total = qs_c.filter(**{caja_tipo: FIELDS["caja_tipo_ingreso"]}).aggregate(s=Sum(caja_monto))["s"] or 0
            egr_total = qs_c.filter(**{caja_tipo: FIELDS["caja_tipo_egreso"]}).aggregate(s=Sum(caja_monto))["s"] or 0
            caja_saldo = ing_total - egr_total

    # =========================
    # Contexto final
    # =========================
    if can_view_sensitive_dashboard:
        dashboard_scope_label = "Vista gerencial"
    elif user_sucursal:
        dashboard_scope_label = f"Vista operativa ({user_sucursal.nombre})"
    else:
        dashboard_scope_label = "Vista operativa"

    ctx = {
        "hoy": hoy,
        "can_view_sensitive_dashboard": can_view_sensitive_dashboard,
        "can_access_caja": can_access_caja,
        "can_access_admin_panel": can_access_admin_panel,
        "dashboard_scope_label": dashboard_scope_label,
        "user_sucursal": user_sucursal,
        "kpis": {
            "ventas_hoy": ventas_hoy,
            "ingresos_hoy": ingresos_hoy,
            "ticket_promedio": ticket_promedio,
            "stock_bajo": stock_bajo,
        },
        "caja": {
            "saldo_actual": caja_saldo,
            "ingresos_hoy": caja_ingresos_hoy,
            "egresos_hoy": caja_egresos_hoy,
        },
        "ultimas_ventas": ultimas_ventas,
        "alertas": alertas,
    }
    return render(request, "core/dashboard.html", ctx)
