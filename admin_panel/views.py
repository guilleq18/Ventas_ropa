from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, redirect, get_object_or_404
from core.models import AppSetting
from ventas.models import Venta, VentaItem, VentaPago, PlanCuotas
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Value, Count, F, DecimalField, ExpressionWrapper
from datetime import datetime
from django.utils import timezone
from django.db.models.functions import TruncDate, ExtractHour

from django import forms
from decimal import Decimal
from django.db.models.functions import Coalesce
from django.db import transaction, IntegrityError
import calendar
from cuentas_corrientes.models import Cliente, CuentaCorriente
from django.contrib import messages
from admin_panel.services import get_ventas_flags, set_bool_setting



import json


def _parse_date(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _shift_months(d, months: int):
    month_idx = (d.month - 1) + months
    year = d.year + (month_idx // 12)
    month = (month_idx % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day)
    return d.replace(year=year, month=month, day=day)


def _venta_pago_tipo_label(tipo: str) -> str:
    return dict(VentaPago.Tipo.choices).get(tipo, tipo or "—")


def _venta_medio_pago_resumen(venta: Venta) -> str:
    pagos = list(getattr(venta, "_prefetched_objects_cache", {}).get("pagos", []) or venta.pagos.all())
    if not pagos:
        return dict(Venta.MedioPago.choices).get(venta.medio_pago, venta.medio_pago)

    tipos = []
    for p in pagos:
        t = (p.tipo or "").strip()
        if t and t not in tipos:
            tipos.append(t)

    if not tipos:
        return dict(Venta.MedioPago.choices).get(venta.medio_pago, venta.medio_pago)
    if len(tipos) == 1:
        return _venta_pago_tipo_label(tipos[0])

    labels = [_venta_pago_tipo_label(t) for t in tipos[:2]]
    extra = len(tipos) - len(labels)
    base = " + ".join(labels)
    if extra > 0:
        base += f" + {extra} más"
    return f"Mixto ({base})"


def _build_nombre_item_venta(variante):
    producto = getattr(variante, "producto", None)
    base = (getattr(producto, "nombre", "") or "").strip()

    color = ""
    talle = ""

    attrs_mgr = getattr(variante, "atributos", None)
    attrs = attrs_mgr.all() if attrs_mgr is not None else []
    for va in attrs:
        nom = (getattr(getattr(va, "atributo", None), "nombre", "") or "").strip().lower()
        val = (getattr(getattr(va, "valor", None), "valor", "") or "").strip()
        if not val:
            continue
        if nom == "color":
            color = val
        elif nom in ("talle", "tamaño", "tamanio", "size"):
            talle = val

    partes = [p for p in (base, color, talle) if p]
    return " - ".join(partes) if partes else (getattr(variante, "sku", "") or base or "Item")


@login_required
def balances(request):
    vista = (request.GET.get("vista") or "ventas").strip().lower()
    if vista not in {"ventas", "productos", "pagos"}:
        vista = "ventas"

    raw_from = request.GET.get("from", None)
    raw_to = request.GET.get("to", None)
    hoy = timezone.localdate()

    date_from = _parse_date(raw_from or "")
    date_to = _parse_date(raw_to or "")

    # default: hoy
    if raw_from is None and raw_to is None:
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
    data_cantidad_dia = [int(x["cantidad"] or 0) for x in por_dia]

    # Por medio de pago (real según VentaPago del POS; incluye recargos de crédito)
    pago_total_field = DecimalField(max_digits=14, decimal_places=2)
    pagos_qs = VentaPago.objects.filter(venta__estado=Venta.Estado.CONFIRMADA)
    if date_from:
        pagos_qs = pagos_qs.filter(venta__fecha__date__gte=date_from)
    if date_to:
        pagos_qs = pagos_qs.filter(venta__fecha__date__lte=date_to)

    por_medio_pagos = (
        pagos_qs.values("tipo")
        .annotate(
            total=Sum(
                ExpressionWrapper(
                    Coalesce(F("monto"), Value(0, output_field=pago_total_field)) +
                    Coalesce(F("recargo_monto"), Value(0, output_field=pago_total_field)),
                    output_field=pago_total_field,
                )
            ),
            cantidad_ventas=Count("venta_id", distinct=True),
        )
        .order_by("-total")
    )

    # Fallback para ventas legacy sin detalle de pagos
    ventas_sin_pagos = qs.filter(pagos__isnull=True)
    por_medio_legacy = (
        ventas_sin_pagos.values("medio_pago")
        .annotate(total=Sum("total"), cantidad=Count("id"))
        .order_by("-total")
    )

    medio_totales = {}
    medio_cantidades = {}
    for row in por_medio_pagos:
        label = dict(VentaPago.Tipo.choices).get(row["tipo"], row["tipo"] or "—")
        medio_totales[label] = (medio_totales.get(label, Decimal("0.00")) + Decimal(row["total"] or 0)).quantize(Decimal("0.01"))
        medio_cantidades[label] = int(medio_cantidades.get(label, 0) + int(row.get("cantidad_ventas") or 0))

    for row in por_medio_legacy:
        label = dict(Venta.MedioPago.choices).get(row["medio_pago"], row["medio_pago"] or "—")
        medio_totales[label] = (medio_totales.get(label, Decimal("0.00")) + Decimal(row["total"] or 0)).quantize(Decimal("0.01"))
        medio_cantidades[label] = int(medio_cantidades.get(label, 0) + int(row.get("cantidad") or 0))

    medios_ordenados = sorted(medio_totales.items(), key=lambda it: it[1], reverse=True)
    labels_medio = [k for k, _ in medios_ordenados]
    data_medio = [float(v or 0) for _, v in medios_ordenados]
    data_medio_cantidad_ventas = [int(medio_cantidades.get(k, 0)) for k, _ in medios_ordenados]

    # Por sucursal
    por_sucursal = (
        qs.values("sucursal__nombre")
          .annotate(total=Sum("total"), cantidad=Count("id"))
          .order_by("-total")
    )
    labels_sucursal = [x["sucursal__nombre"] for x in por_sucursal]
    data_sucursal = [float(x["total"] or 0) for x in por_sucursal]
    data_sucursal_cantidad = [int(x["cantidad"] or 0) for x in por_sucursal]

    # Ventas por hora del día (agregado en el rango)
    por_hora = (
        qs.annotate(hora=ExtractHour("fecha"))
          .values("hora")
          .annotate(total=Sum("total"), cantidad=Count("id"))
          .order_by("hora")
    )
    por_hora_map = {int(x["hora"]): float(x["total"] or 0) for x in por_hora if x["hora"] is not None}
    por_hora_cant_map = {int(x["hora"]): int(x["cantidad"] or 0) for x in por_hora if x["hora"] is not None}
    labels_hora = [f"{h:02d}:00" for h in range(24)]
    data_hora = [por_hora_map.get(h, 0.0) for h in range(24)]
    data_hora_cantidad = [por_hora_cant_map.get(h, 0) for h in range(24)]

    # Ventas por categoría / producto (desde items reales del POS)
    items_qs = VentaItem.objects.filter(venta__estado=Venta.Estado.CONFIRMADA)
    if date_from:
        items_qs = items_qs.filter(venta__fecha__date__gte=date_from)
    if date_to:
        items_qs = items_qs.filter(venta__fecha__date__lte=date_to)

    por_categoria = (
        items_qs.values("variante__producto__categoria__nombre")
        .annotate(total=Sum("subtotal"), cantidad=Sum("cantidad"))
        .order_by("-total")
    )
    labels_categoria = [x["variante__producto__categoria__nombre"] or "Sin categoría" for x in por_categoria[:12]]
    data_categoria = [float(x["total"] or 0) for x in por_categoria[:12]]
    data_categoria_cantidad = [int(x["cantidad"] or 0) for x in por_categoria[:12]]

    por_producto = (
        items_qs.values("variante__producto__nombre")
        .annotate(total=Sum("subtotal"), cantidad=Sum("cantidad"))
        .order_by("-total")
    )
    labels_producto = [x["variante__producto__nombre"] or "Sin nombre" for x in por_producto[:15]]
    data_producto = [float(x["total"] or 0) for x in por_producto[:15]]
    data_producto_cantidad = [int(x["cantidad"] or 0) for x in por_producto[:15]]

    rangos_fecha = {
        "1m": {"label": "1 mes", "from": _shift_months(hoy, -1).strftime("%Y-%m-%d"), "to": hoy.strftime("%Y-%m-%d"), "vista": vista},
        "3m": {"label": "3 meses", "from": _shift_months(hoy, -3).strftime("%Y-%m-%d"), "to": hoy.strftime("%Y-%m-%d"), "vista": vista},
        "6m": {"label": "6 meses", "from": _shift_months(hoy, -6).strftime("%Y-%m-%d"), "to": hoy.strftime("%Y-%m-%d"), "vista": vista},
        "1y": {"label": "1 año", "from": _shift_months(hoy, -12).strftime("%Y-%m-%d"), "to": hoy.strftime("%Y-%m-%d"), "vista": vista},
    }

    return render(request, "admin_panel/balances.html", {
        "from": raw_from or "",
        "to": raw_to or "",
        "vista": vista,
        "rangos_fecha": rangos_fecha,

        "total": total,
        "cantidad": cantidad,
        "ticket_prom": ticket_prom,

        # JSON para charts
        "labels_dia_json": json.dumps(labels_dia),
        "data_total_dia_json": json.dumps(data_total_dia),
        "data_cantidad_dia_json": json.dumps(data_cantidad_dia),
        "labels_hora_json": json.dumps(labels_hora),
        "data_hora_json": json.dumps(data_hora),
        "data_hora_cantidad_json": json.dumps(data_hora_cantidad),

        "labels_medio_json": json.dumps(labels_medio),
        "data_medio_json": json.dumps(data_medio),
        "data_medio_cantidad_ventas_json": json.dumps(data_medio_cantidad_ventas),

        "labels_sucursal_json": json.dumps(labels_sucursal),
        "data_sucursal_json": json.dumps(data_sucursal),
        "data_sucursal_cantidad_json": json.dumps(data_sucursal_cantidad),

        "labels_categoria_json": json.dumps(labels_categoria),
        "data_categoria_json": json.dumps(data_categoria),
        "data_categoria_cantidad_json": json.dumps(data_categoria_cantidad),
        "labels_producto_json": json.dumps(labels_producto),
        "data_producto_json": json.dumps(data_producto),
        "data_producto_cantidad_json": json.dumps(data_producto_cantidad),
    })

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

    qs = Venta.objects.select_related("sucursal").prefetch_related("pagos").all()

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

    qs = qs.order_by("-fecha", "-id")

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    for v in page_obj.object_list:
        v.medio_pago_ui = _venta_medio_pago_resumen(v)

    return render(request, "admin_panel/ventas_lista.html", {
        "page_obj": page_obj,
        "q": q,
        "from": raw_from or "",
        "to": raw_to or "",
        "sucursal": sucursal_id,
        "estado": estado,
        "estados": Venta.Estado.choices,
    })

@login_required
def ventas_detalle(request, venta_id: int):
    venta = get_object_or_404(
        Venta.objects.select_related("sucursal", "cliente"),
        id=venta_id
    )

    # gracias a related_name
    items = (
        venta.items
        .select_related("variante", "variante__producto")
        .prefetch_related("variante__atributos__atributo", "variante__atributos__valor")
        .order_by("id")
    )
    pagos = venta.pagos.select_related("plan").order_by("id")

    total_items = Decimal("0.00")
    total_recargos = Decimal("0.00")
    total_pagado = Decimal("0.00")

    for it in items:
        it.nombre_admin = _build_nombre_item_venta(it.variante)
        subtotal = Decimal(it.subtotal or 0).quantize(Decimal("0.01"))
        total_items += subtotal

    for p in pagos:
        p.recargo_monto_safe = Decimal(p.recargo_monto or 0).quantize(Decimal("0.01"))
        p.total_pago_admin = (Decimal(p.monto or 0) + p.recargo_monto_safe).quantize(Decimal("0.01"))
        total_recargos += p.recargo_monto_safe
        total_pagado += p.total_pago_admin

    venta.medio_pago_ui = _venta_medio_pago_resumen(venta)

    return render(request, "admin_panel/ventas_detalle.html", {
        "venta": venta,
        "items": items,
        "pagos": pagos,
        "total_items": total_items.quantize(Decimal("0.01")),
        "total_recargos": total_recargos.quantize(Decimal("0.01")),
        "total_pagado": total_pagado.quantize(Decimal("0.01")),
    })

@login_required
@permission_required("core.change_appsetting", raise_exception=True)
def settings_view(request):
    if request.method == "POST":
        set_bool_setting(
            "ventas.permitir_sin_stock",
            request.POST.get("permitir_sin_stock") == "on",
            False,
            "Permite confirmar venta aunque no haya stock suficiente."
        )
        set_bool_setting(
            "ventas.permitir_cambiar_precio_venta",
            request.POST.get("permitir_cambiar_precio_venta") == "on",
            False,
            "Permite cambiar el precio de venta en el POS."
        )
        messages.success(request, "Configuración de ventas actualizada.")
        return redirect("admin_panel:settings")

    flags = get_ventas_flags()

    return render(request, "admin_panel/settings.html", {
        **flags,
    })


def _parse_recargo_pct_input(raw: str) -> Decimal:
    txt = (raw or "").strip().replace("%", "").replace(" ", "")
    txt = txt.replace(".", "").replace(",", ".") if ("," in txt and "." in txt) else txt.replace(",", ".")
    return Decimal(txt or "0")


@login_required
def tarjetas_view(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "plan_create":
            tarjeta = (request.POST.get("tarjeta") or "").strip().upper()
            try:
                cuotas = int(request.POST.get("cuotas") or 0)
            except ValueError:
                cuotas = 0
            try:
                recargo_pct = _parse_recargo_pct_input(request.POST.get("recargo_pct") or "0")
            except Exception:
                recargo_pct = None
            activo = request.POST.get("activo") == "on"

            if not tarjeta:
                messages.error(request, "La tarjeta es obligatoria.")
                return redirect("admin_panel:tarjetas")
            if cuotas <= 0:
                messages.error(request, "Las cuotas deben ser mayores a 0.")
                return redirect("admin_panel:tarjetas")
            if recargo_pct is None or recargo_pct < 0:
                messages.error(request, "El recargo debe ser un número válido mayor o igual a 0.")
                return redirect("admin_panel:tarjetas")

            try:
                PlanCuotas.objects.create(
                    tarjeta=tarjeta,
                    cuotas=cuotas,
                    recargo_pct=recargo_pct,
                    activo=activo,
                )
                messages.success(request, "Plan de cuotas creado.")
            except IntegrityError:
                messages.error(request, f"Ya existe un plan para {tarjeta} en {cuotas} cuotas.")
            return redirect("admin_panel:tarjetas")

        if action == "plan_update":
            plan = get_object_or_404(PlanCuotas, id=request.POST.get("plan_id"))
            tarjeta = (request.POST.get("tarjeta") or "").strip().upper()
            try:
                cuotas = int(request.POST.get("cuotas") or 0)
            except ValueError:
                cuotas = 0
            try:
                recargo_pct = _parse_recargo_pct_input(request.POST.get("recargo_pct") or "0")
            except Exception:
                recargo_pct = None
            activo = request.POST.get("activo") == "on"

            if not tarjeta or cuotas <= 0 or recargo_pct is None or recargo_pct < 0:
                messages.error(request, "No se pudo guardar: revisá tarjeta, cuotas y recargo.")
                return redirect("admin_panel:tarjetas")

            plan.tarjeta = tarjeta
            plan.cuotas = cuotas
            plan.recargo_pct = recargo_pct
            plan.activo = activo
            try:
                plan.save()
                messages.success(request, f"Plan actualizado: {plan.tarjeta} {plan.cuotas} cuotas.")
            except IntegrityError:
                messages.error(request, f"Ya existe un plan para {tarjeta} en {cuotas} cuotas.")
            return redirect("admin_panel:tarjetas")

        if action == "plan_delete":
            plan = get_object_or_404(PlanCuotas, id=request.POST.get("plan_id"))
            plan_desc = f"{plan.tarjeta} {plan.cuotas} cuotas"
            plan.delete()
            messages.success(request, f"Plan eliminado: {plan_desc}.")
            return redirect("admin_panel:tarjetas")

        messages.error(request, "Acción no reconocida.")
        return redirect("admin_panel:tarjetas")

    q = (request.GET.get("q") or "").strip()
    planes_cuotas = PlanCuotas.objects.all()
    if q:
        planes_cuotas = planes_cuotas.filter(tarjeta__icontains=q)
    planes_cuotas = planes_cuotas.order_by("tarjeta", "cuotas")
    return render(request, "admin_panel/tarjetas.html", {
        "planes_cuotas": planes_cuotas,
        "q": q,
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

#cuenta corriente
from cuentas_corrientes.models import CuentaCorriente, MovimientoCuentaCorriente


class PagoCCForm(forms.Form):
    monto = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    referencia = forms.CharField(max_length=120, required=False)
    observacion = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


@login_required
def cc_lista(request):
    q = (request.GET.get("q") or "").strip()
    activa = request.GET.get("activa", "1")  # "1" activa, "0" inactiva, "" todas

    saldo_field = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(0, output_field=saldo_field)

    qs = (
        CuentaCorriente.objects
        .select_related("cliente")
        .annotate(
            debitos=Coalesce(
                Sum(
                    "movimientos__monto",
                    filter=Q(movimientos__tipo=MovimientoCuentaCorriente.Tipo.DEBITO)
                ),
                zero,
                output_field=saldo_field,
            ),
            creditos=Coalesce(
                Sum(
                    "movimientos__monto",
                    filter=Q(movimientos__tipo=MovimientoCuentaCorriente.Tipo.CREDITO)
                ),
                zero,
                output_field=saldo_field,
            ),
        )
        .annotate(
            saldo_calc=ExpressionWrapper(F("debitos") - F("creditos"), output_field=saldo_field)
        )
        .order_by("cliente__apellido", "cliente__nombre")
    )


    if activa in ("0", "1"):
        qs = qs.filter(activa=(activa == "1"))

    if q:
        qs = qs.filter(
            Q(cliente__dni__icontains=q) |
            Q(cliente__apellido__icontains=q) |
            Q(cliente__nombre__icontains=q)
        )

    return render(request, "admin_panel/cc_lista.html", {
        "cuentas": qs,
        "q": q,
        "activa": activa,
    })


@login_required
def cc_detalle(request, cuenta_id: int):
    cuenta = get_object_or_404(
        CuentaCorriente.objects.select_related("cliente"),
        id=cuenta_id
    )

    movimientos = (cuenta.movimientos
                   .select_related("venta")
                   .order_by("-fecha", "-id")[:200])  # límite razonable al inicio

    form = PagoCCForm()

    return render(request, "admin_panel/cc_detalle.html", {
        "cuenta": cuenta,
        "cliente": cuenta.cliente,
        "saldo": cuenta.saldo(),  # usa tu método
        "movimientos": movimientos,
        "form": form,
    })


@login_required
def cc_toggle_activa(request, cuenta_id: int):
    if request.method != "POST":
        return redirect("admin_panel:cc_detalle", cuenta_id=cuenta_id)

    cuenta = get_object_or_404(CuentaCorriente, id=cuenta_id)
    cuenta.activa = not cuenta.activa
    cuenta.save(update_fields=["activa"])

    messages.success(request, "Estado de la cuenta corriente actualizado.")
    return redirect("admin_panel:cc_detalle", cuenta_id=cuenta_id)


@login_required
def cc_registrar_pago(request, cuenta_id: int):
    if request.method != "POST":
        return redirect("admin_panel:cc_detalle", cuenta_id=cuenta_id)

    cuenta = get_object_or_404(CuentaCorriente.objects.select_related("cliente"), id=cuenta_id)
    form = PagoCCForm(request.POST)

    if not form.is_valid():
        movimientos = cuenta.movimientos.select_related("venta").order_by("-fecha", "-id")[:200]
        return render(request, "admin_panel/cc_detalle.html", {
            "cuenta": cuenta,
            "cliente": cuenta.cliente,
            "saldo": cuenta.saldo(),
            "movimientos": movimientos,
            "form": form,
        })

    MovimientoCuentaCorriente.objects.create(
        cuenta=cuenta,
        tipo=MovimientoCuentaCorriente.Tipo.CREDITO,
        monto=form.cleaned_data["monto"],
        fecha=timezone.now(),
        referencia=form.cleaned_data.get("referencia", ""),
        observacion=form.cleaned_data.get("observacion", ""),
        venta=None,  # importante para pasar tu clean()
    )

    messages.success(request, "Pago registrado en cuenta corriente.")
    return redirect("admin_panel:cc_detalle", cuenta_id=cuenta_id)
class NuevaCCForm(forms.Form):
    dni = forms.CharField(max_length=20)
    nombre = forms.CharField(max_length=80)
    apellido = forms.CharField(max_length=80)
    telefono = forms.CharField(max_length=40, required=False)
    direccion = forms.CharField(max_length=200, required=False)
    fecha_nacimiento = forms.DateField(required=False, input_formats=["%Y-%m-%d"])  # viene de input date


@login_required
def cc_crear(request):
    if request.method != "POST":
        return redirect("admin_panel:cc_lista")

    form = NuevaCCForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisá los datos del formulario.")
        return redirect("admin_panel:cc_lista")

    dni = form.cleaned_data["dni"].strip()

    # Si ya existe cliente y/o CC
    if Cliente.objects.filter(dni=dni).exists():
        cliente = Cliente.objects.get(dni=dni)
        if hasattr(cliente, "cuenta_corriente"):
            messages.error(request, f"El cliente {dni} ya tiene cuenta corriente.")
            return redirect("admin_panel:cc_lista")
    else:
        cliente = None

    try:
        with transaction.atomic():
            if cliente is None:
                cliente = Cliente.objects.create(
                    dni=dni,
                    nombre=form.cleaned_data["nombre"].strip(),
                    apellido=form.cleaned_data["apellido"].strip(),
                    telefono=(form.cleaned_data.get("telefono") or "").strip(),
                    direccion=(form.cleaned_data.get("direccion") or "").strip(),
                    fecha_nacimiento=form.cleaned_data.get("fecha_nacimiento"),
                    activo=True,
                )

            CuentaCorriente.objects.create(
                cliente=cliente,
                activa=True,
            )

        messages.success(request, "Cuenta corriente creada correctamente.")
        return redirect("admin_panel:cc_detalle", cuenta_id=cliente.cuenta_corriente.id)

    except Exception as e:
        messages.error(request, f"No se pudo crear la cuenta corriente: {e}")
        return redirect("admin_panel:cc_lista")

