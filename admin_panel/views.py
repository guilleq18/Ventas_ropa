from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, redirect, get_object_or_404
from core.models import AppSetting
from ventas.models import Venta, VentaItem, VentaPago
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Value, Count, F, DecimalField, ExpressionWrapper
from datetime import datetime
from django.utils import timezone
from django.db.models.functions import TruncDate

from django import forms
from decimal import Decimal
from django.db.models.functions import Coalesce
from django.db import transaction
from cuentas_corrientes.models import Cliente, CuentaCorriente
from django.contrib import messages



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

