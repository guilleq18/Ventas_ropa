from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, redirect, get_object_or_404
from core.models import AppSetting
from ventas.models import Venta, VentaItem, VentaPago
from django.core.paginator import Paginator
from django.db.models import Q
from datetime import datetime
from django.utils import timezone
from django.db.models.functions import TruncDate
from django.db.models import Sum, Count

import json


def _parse_date(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@login_required
def balances(request):
    raw_from = request.GET.get("from", None)
    raw_to = request.GET.get("to", None)

    date_from = _parse_date(raw_from or "")
    date_to = _parse_date(raw_to or "")

    # default: hoy
    if raw_from is None and raw_to is None:
        hoy = timezone.localdate()
        date_from = hoy
        date_to = hoy
        raw_from = hoy.strftime("%Y-%m-%d")
        raw_to = hoy.strftime("%Y-%m-%d")

    qs = Venta.objects.select_related("sucursal").filter(estado=Venta.Estado.CONFIRMADA)

    if date_from:
        qs = qs.filter(fecha__date__gte=date_from)
    if date_to:
        qs = qs.filter(fecha__date__lte=date_to)

    # KPIs
    kpi = qs.aggregate(
        total=Sum("total"),
        cantidad=Count("id"),
    )
    total = kpi["total"] or 0
    cantidad = kpi["cantidad"] or 0
    ticket_prom = (total / cantidad) if cantidad else 0

    # Serie por día
    por_dia = (
        qs.annotate(dia=TruncDate("fecha"))
          .values("dia")
          .annotate(total=Sum("total"), cantidad=Count("id"))
          .order_by("dia")
    )
    labels_dia = [x["dia"].strftime("%d/%m/%Y") for x in por_dia]
    data_total_dia = [float(x["total"] or 0) for x in por_dia]

    # Por medio de pago
    por_medio = (
        qs.values("medio_pago")
          .annotate(total=Sum("total"), cantidad=Count("id"))
          .order_by("-total")
    )
    labels_medio = [dict(Venta.MedioPago.choices).get(x["medio_pago"], x["medio_pago"]) for x in por_medio]
    data_medio = [float(x["total"] or 0) for x in por_medio]

    # Por sucursal
    por_sucursal = (
        qs.values("sucursal__nombre")
          .annotate(total=Sum("total"), cantidad=Count("id"))
          .order_by("-total")
    )
    labels_sucursal = [x["sucursal__nombre"] for x in por_sucursal]
    data_sucursal = [float(x["total"] or 0) for x in por_sucursal]

    return render(request, "admin_panel/balances.html", {
        "from": raw_from or "",
        "to": raw_to or "",

        "total": total,
        "cantidad": cantidad,
        "ticket_prom": ticket_prom,

        # JSON para charts
        "labels_dia_json": json.dumps(labels_dia),
        "data_total_dia_json": json.dumps(data_total_dia),

        "labels_medio_json": json.dumps(labels_medio),
        "data_medio_json": json.dumps(data_medio),

        "labels_sucursal_json": json.dumps(labels_sucursal),
        "data_sucursal_json": json.dumps(data_sucursal),
    })

def _parse_date(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@login_required
def ventas_lista(request):
    q = (request.GET.get("q") or "").strip()
    sucursal_id = (request.GET.get("sucursal") or "").strip()
    estado = (request.GET.get("estado") or "").strip()

    # None si NO viene en la URL
    raw_from = request.GET.get("from", None)
    raw_to = request.GET.get("to", None)

    date_from = _parse_date(raw_from or "")
    date_to = _parse_date(raw_to or "")

    # ✅ Default: hoy SOLO cuando abrís la pantalla sin filtros
    if raw_from is None and raw_to is None:
        hoy = timezone.localdate()
        date_from = hoy
        date_to = hoy
        raw_from = hoy.strftime("%Y-%m-%d")
        raw_to = hoy.strftime("%Y-%m-%d")

    qs = Venta.objects.select_related("sucursal").all()

    if date_from:
        qs = qs.filter(fecha__date__gte=date_from)
    if date_to:
        qs = qs.filter(fecha__date__lte=date_to)

    if sucursal_id.isdigit():
        qs = qs.filter(sucursal_id=int(sucursal_id))

    if estado:
        qs = qs.filter(estado=estado)

    if q:
        filtros = Q()
        if q.isdigit():
            filtros |= Q(id=int(q))
        filtros |= Q(sucursal__nombre__icontains=q)
        qs = qs.filter(filtros)

    qs = qs.order_by("-id")

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "admin_panel/ventas_lista.html", {
        "page_obj": page_obj,
        "q": q,
        "from": raw_from or "",
        "to": raw_to or "",
        "sucursal": sucursal_id,
        "estado": estado,
        "estados": Venta.Estado.choices,
        # debug para confirmar que aplica
        "debug_from": raw_from or "",
        "debug_to": raw_to or "",
    })

@login_required
def ventas_detalle(request, venta_id: int):
    venta = get_object_or_404(
        Venta.objects.select_related("sucursal"),
        id=venta_id
    )

    # gracias a related_name
    items = venta.items.select_related("variante").order_by("id")
    pagos = venta.pagos.select_related("plan").order_by("id")

    return render(request, "admin_panel/ventas_detalle.html", {
        "venta": venta,
        "items": items,
        "pagos": pagos,
    })

@login_required
@permission_required("core.change_appsetting", raise_exception=True)
def settings_view(request):
    setting, _ = AppSetting.objects.get_or_create(
        key="ventas.permitir_sin_stock",
        defaults={"value_bool": False, "description": "Permite confirmar venta aunque no haya stock suficiente."}
    )

    if request.method == "POST":
        setting.value_bool = (request.POST.get("permitir_sin_stock") == "on")
        setting.save()
        return redirect("admin_panel:settings")

    return render(request, "admin_panel/settings.html", {
        "permitir_sin_stock": bool(setting.value_bool),
        "setting_desc": setting.description,
    })


@login_required
def dashboard(request):
    return render(request, "admin_panel/dashboard.html")

@login_required
def catalogo_home(request):
    return render(request, "admin_panel/catalogo_home.html")

@login_required
def usuarios_lista(request):
    return render(request, "admin_panel/usuarios_lista.html")
