from django.contrib.auth.decorators import login_required
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


def _field_exists(model, field_name: str) -> bool:
    if not model or not field_name:
        return False
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


@login_required
def dashboard(request):
    hoy = timezone.localdate()

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
            qs_hoy = qs_hoy.filter(**{FIELDS["venta_estado"]: FIELDS["venta_estado_ok"]})

        # KPIs
        ventas_hoy = qs_hoy.aggregate(c=Count("id"))["c"] or 0

        venta_total = FIELDS["venta_total"]
        if _field_exists(Venta, venta_total):
            ingresos_hoy = qs_hoy.aggregate(s=Sum(venta_total))["s"] or 0
            ticket_promedio = (ingresos_hoy / ventas_hoy) if ventas_hoy else 0

        # Últimas ventas (máximo 10)
        # Intentamos ordenar por fecha si existe
        if _field_exists(Venta, venta_fecha):
            qs_last = qs.order_by(f"-{venta_fecha}")[:10]
        else:
            qs_last = qs.order_by("-id")[:10]

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
                cliente_txt = "Consumidor Final"
            else:
                cliente_txt = str(cliente_txt)

            total_val = getattr(v, venta_total, 0) if _field_exists(Venta, venta_total) else 0
            estado_val = getattr(v, FIELDS["venta_estado"], "OK") if _field_exists(Venta, FIELDS["venta_estado"]) else "OK"

            ultimas_ventas.append({
                "fecha": fecha_txt,
                "nro": nro_txt,
                "cliente": cliente_txt,
                "total": total_val or 0,
                "estado": estado_val,
            })

    # =========================
    # STOCK BAJO
    # =========================
    if Producto:
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
    if MovimientoCaja:
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
    ctx = {
        "hoy": hoy,
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
