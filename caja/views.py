# caja/views.py
# Comentarios en español como pediste.

import uuid
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q, Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Sucursal
from core.models import AppSetting
from core.fiscal import (
    CondicionFiscalEmpresa,
    DesgloseFiscalMonto,
    IVA_GENERAL_PCT,
    desglosar_monto_final_gravado_con_iva,
    get_empresa_condicion_fiscal,
)
from catalogo.models import Variante, StockSucursal
from ventas.models import Venta, VentaItem, VentaPago, PlanCuotas
from ventas.services import confirmar_venta
from cuentas_corrientes.models import Cliente, CuentaCorriente, MovimientoCuentaCorriente

from admin_panel.services import permitir_vender_sin_stock, permitir_cambiar_precio_venta
from .models import CajaSesion
from .utils import handle_pos_errors





# ======================================================================
# Helpers: Formato AR (para OOB sin depender del filtro en templates)
# ======================================================================

def _fmt_ar(value, decimals: int = 2) -> str:
    """
    Formatea estilo Argentina:
      miles con punto y decimales con coma
      12345.6 -> 12.345,60
    """
    try:
        decimals = int(decimals)
    except Exception:
        decimals = 2

    if value is None or value == "":
        return ""

    try:
        n = Decimal(str(value))
    except Exception:
        return str(value)

    q = Decimal("1") if decimals <= 0 else Decimal("1." + ("0" * decimals))
    n = n.quantize(q, rounding=ROUND_HALF_UP)

    s = f"{n:,.{max(decimals,0)}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")




def _build_stock_map(sucursal, variante_ids: list[int]) -> dict:
    if not variante_ids:
        return {}

    rows = (
        StockSucursal.objects
        .filter(sucursal=sucursal, variante_id__in=variante_ids)
        .values("variante_id", "cantidad")
    )
    return {int(r["variante_id"]): int(r["cantidad"] or 0) for r in rows}

# ======================================================================
# Helpers: Pagos (session)
# ======================================================================



def _payments_get(request) -> list:
    return request.session.get("pos_payments", [])


def _payments_save(request, payments: list):
    request.session["pos_payments"] = payments
    request.session.modified = True


def _payments_default() -> dict:
    return {
        "tipo": "CONTADO",
        "monto": "0.00",
        "cuotas": 1,
        "recargo_pct": "0.00",
        "referencia": "",

        # POS
        "pos_proveedor": "",
        "pos_terminal_id": "",
        "pos_lote": "",
        "pos_cupon": "",
        "pos_autorizacion": "",
        "pos_marca": "",
        "pos_ultimos4": "",

        # Crédito
        "tarjeta": "",
        "plan_id": "",

        # Cuenta corriente
        "cc_cliente_id": "",
        "cc_q": "",
    }



def _parse_decimal_ar(raw) -> Decimal:
    s = (raw or "").strip()
    if not s:
        return Decimal("0.00")

    s = s.replace("$", "").replace(" ", "")

    # Caso típico AR: 23.648,00  -> 23648.00
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    allowed = set("0123456789.-")
    s = "".join(ch for ch in s if ch in allowed)

    if s in ("", "-", ".", "-."):
        return Decimal("0.00")

    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _payments_total(payments: list) -> Decimal:
    """
    Total realmente cobrado (lo que entra a caja).
    - CREDITO: monto + recargo
    - resto: monto
    """
    total = Decimal("0.00")

    for p in payments:
        tipo = (p.get("tipo") or "").strip()

        try:
            monto = Decimal(str(p.get("monto", "0") or "0")).quantize(Decimal("0.01"))
        except Exception:
            monto = Decimal("0.00")

        if monto <= 0:
            continue

        if tipo == "CREDITO":
            try:
                recargo_pct = Decimal(str(p.get("recargo_pct") or "0")).quantize(Decimal("0.01"))
            except Exception:
                recargo_pct = Decimal("0.00")

            recargo_monto = (monto * recargo_pct / Decimal("100")).quantize(Decimal("0.01"))
            total += (monto + recargo_monto)
        else:
            total += monto

    return total.quantize(Decimal("0.01"))


def _payments_build_ui_and_totals(payments: list, total_base: Decimal) -> dict:
    ui_payments = []
    recargos_credito = Decimal("0")

    # ---- preparar CC (pocos pagos = simple y seguro) ----
    cc_ids = []
    for p in payments:
        if (p.get("tipo") or "").strip() == "CUENTA_CORRIENTE":
            raw = (p.get("cc_cliente_id") or "").strip()
            if raw.isdigit():
                cc_ids.append(int(raw))

    clientes_map = {}
    cc_map = {}
    if cc_ids:
        clientes_map = {c.id: c for c in Cliente.objects.filter(id__in=cc_ids, activo=True)}
        cc_map = {
            cc.cliente_id: cc
            for cc in CuentaCorriente.objects.select_related("cliente").filter(cliente_id__in=cc_ids, activa=True)
        }

    for p in payments:
        tipo = (p.get("tipo") or "").strip()

        try:
            monto = Decimal(str(p.get("monto", "0") or "0"))
        except Exception:
            monto = Decimal("0")

        try:
            cuotas = int(p.get("cuotas") or 1)
        except Exception:
            cuotas = 1

        try:
            recargo_pct = Decimal(str(p.get("recargo_pct") or "0"))
        except Exception:
            recargo_pct = Decimal("0")

        recargo_monto = (monto * recargo_pct / Decimal("100")).quantize(Decimal("0.01"))
        total_tarjeta = (monto + recargo_monto).quantize(Decimal("0.01"))
        cuota_est = (total_tarjeta / Decimal(str(max(cuotas, 1)))).quantize(Decimal("0.01"))

        if tipo == "CREDITO" and monto > 0:
            recargos_credito += recargo_monto

        p_ui = dict(p)
        p_ui["monto"] = str(monto.quantize(Decimal("0.01")))
        p_ui["cuotas"] = cuotas
        p_ui["recargo_pct"] = str(recargo_pct.quantize(Decimal("0.01")))
        p_ui["recargo_monto_calc"] = recargo_monto
        p_ui["total_tarjeta_calc"] = total_tarjeta
        p_ui["cuota_calc"] = cuota_est
        p_ui["tipo_locked"] = bool(monto > 0)
        p_ui["selected_plan_id"] = (p.get("plan_id") or "").strip()

        # ---- campos UI Cuenta Corriente ----
        p_ui["cc_q"] = (p.get("cc_q") or "").strip()
        p_ui["cc_cliente_nombre"] = ""
        p_ui["cc_cliente_dni"] = ""
        p_ui["cc_ok"] = False
        p_ui["cc_saldo"] = Decimal("0.00")

        if tipo == "CUENTA_CORRIENTE":
            raw_id = (p.get("cc_cliente_id") or "").strip()
            if raw_id.isdigit():
                cid = int(raw_id)
                cli = clientes_map.get(cid)
                if cli:
                    p_ui["cc_cliente_nombre"] = f"{cli.apellido}, {cli.nombre}"
                    p_ui["cc_cliente_dni"] = cli.dni

                cc = cc_map.get(cid)
                if cc:
                    p_ui["cc_ok"] = True
                    try:
                        p_ui["cc_saldo"] = Decimal(str(cc.saldo())).quantize(Decimal("0.01"))
                    except Exception:
                        p_ui["cc_saldo"] = Decimal("0.00")

        ui_payments.append(p_ui)

    recargos_credito = recargos_credito.quantize(Decimal("0.01"))
    total_base = Decimal(total_base).quantize(Decimal("0.01"))
    total_cobrar = (total_base + recargos_credito).quantize(Decimal("0.01"))

    pagado = _payments_total(payments)
    saldo = (total_cobrar - pagado).quantize(Decimal("0.01"))

    return {
        "ui_payments": ui_payments,
        "recargos": recargos_credito,
        "total_cobrar": total_cobrar,
        "pagado": pagado,
        "saldo": saldo,
    }


def _desglose_fiscal_pos_safe(monto_final):
    try:
        return desglosar_monto_final_gravado_con_iva(monto_final)
    except Exception:
        return desglosar_monto_final_gravado_con_iva(Decimal("0.00"))


def _ctx_fiscal_empresa_pos() -> dict:
    condicion_code = get_empresa_condicion_fiscal()
    return {
        "empresa_condicion_fiscal_code": condicion_code,
        "empresa_condicion_fiscal_label": dict(CondicionFiscalEmpresa.CHOICES).get(
            condicion_code,
            condicion_code or "-",
        ),
        "empresa_es_ri": (condicion_code == CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO),
        "empresa_es_monotributista": (condicion_code == CondicionFiscalEmpresa.MONOTRIBUTISTA),
    }


def _ctx_fiscal_totales_pos(total_items) -> dict:
    try:
        total_items_dec = Decimal(str(total_items or "0")).quantize(Decimal("0.01"))
    except Exception:
        total_items_dec = Decimal("0.00")

    return {
        **_ctx_fiscal_empresa_pos(),
        "fiscal_total_items": _desglose_fiscal_pos_safe(total_items_dec),
    }


def _decorar_variantes_con_fiscal(results):
    for v in (results or []):
        try:
            v.fiscal_precio = _desglose_fiscal_pos_safe(getattr(v, "precio", Decimal("0.00")))
        except Exception:
            v.fiscal_precio = _desglose_fiscal_pos_safe(Decimal("0.00"))
    return results



def _ctx_pagos_pos(request) -> dict:
    payments = _payments_get(request) or []
    total_base = _cart_total(_cart_get(request))
    pay_ctx = _payments_build_ui_and_totals(payments, total_base)
        # =========================
    # Enriquecer pagos CC (cliente + saldo)
    # =========================
    cc_ids = []
    for p in pay_ctx["ui_payments"]:
        if (p.get("tipo") == "CUENTA_CORRIENTE") and str(p.get("cc_cliente_id") or "").isdigit():
            cc_ids.append(int(p["cc_cliente_id"]))

    clientes_map = {}
    cuentas_map = {}

    if cc_ids:
        clientes = Cliente.objects.filter(id__in=cc_ids, activo=True)
        clientes_map = {c.id: c for c in clientes}

        cuentas = CuentaCorriente.objects.filter(cliente_id__in=cc_ids)
        cuentas_map = {cc.cliente_id: cc for cc in cuentas}

        for p in pay_ctx["ui_payments"]:
            if p.get("tipo") != "CUENTA_CORRIENTE":
                continue

            cid_raw = p.get("cc_cliente_id") or ""
            if not str(cid_raw).isdigit():
                p["cc_cliente_nombre"] = ""
                p["cc_cliente_dni"] = ""
                p["cc_saldo"] = None
                p["cc_ok"] = False
                continue

            cid = int(cid_raw)
            cli = clientes_map.get(cid)
            cc = cuentas_map.get(cid)

            if cli:
                p["cc_cliente_nombre"] = f"{cli.apellido}, {cli.nombre}"
                p["cc_cliente_dni"] = cli.dni
            else:
                p["cc_cliente_nombre"] = ""
                p["cc_cliente_dni"] = ""

            if cc and cc.activa:
                # saldo() hace aggregate; como son pocos, OK
                p["cc_saldo"] = cc.saldo()
                p["cc_ok"] = True
            else:
                p["cc_saldo"] = None
                p["cc_ok"] = False
                


    tarjetas = list(
        PlanCuotas.objects.filter(activo=True)
        .values_list("tarjeta", flat=True)
        .distinct()
        .order_by("tarjeta")
    )

    tipos = list(VentaPago.Tipo.choices)

    return {
        "payments_session": payments,
        "payments": pay_ctx["ui_payments"],     # <- para templates
        "ui_payments": pay_ctx["ui_payments"],  # <- por si lo venías usando
        "total_base": total_base,
        "recargos": pay_ctx["recargos"],
        "total_cobrar": pay_ctx["total_cobrar"],
        "pagado": pay_ctx["pagado"],
        "saldo": pay_ctx["saldo"],
        "tarjetas": tarjetas,
        "tipos": tipos,
        **_ctx_fiscal_totales_pos(total_base),
    }


def _oob_pagos_html(request) -> str:
    """
    Refresca el card (tabla + totales + total_cobrar ids) sin tocar el modal.
    Requiere que existan en el DOM:
      - <div id="pagos_table">...</div>
      - <div id="pagos_totales">...</div>
      - <strong id="total_cobrar_card">...</strong>
      - <strong id="total_cobrar_confirm">...</strong>
    """
    ctx = _ctx_pagos_pos(request)

    html_table = render_to_string(
        "caja/_pagos_table.html",
        {"payments": ctx["payments"]},
        request=request
    )
    html_tot = render_to_string(
        "caja/_pagos_totales.html",
        ctx,
        request=request
    )

    total_fmt = _fmt_ar(ctx["total_cobrar"], 2)

    return (
        f'<div hx-swap-oob="innerHTML:#pagos_table">{html_table}</div>'
        f'<div hx-swap-oob="innerHTML:#pagos_totales">{html_tot}</div>'
        f'<div hx-swap-oob="innerHTML:#total_cobrar_card">{total_fmt}</div>'
        f'<div hx-swap-oob="innerHTML:#total_cobrar_confirm">{total_fmt}</div>'
    )


def _render_pagos_modal_body_html(request) -> str:
    ctx = _ctx_pagos_pos(request)

    return render_to_string("caja/_pagos_modal_body.html", {
        "payments": ctx["ui_payments"],
        "total_base": ctx["total_base"],
        "recargos": ctx["recargos"],
        "total_cobrar": ctx["total_cobrar"],
        "pagado": ctx["pagado"],
        "saldo": ctx["saldo"],
        "tarjetas": ctx["tarjetas"],
    }, request=request)



# ======================================================================
# Endpoints: Modal Pagos (abrir / agregar / guardar / quitar)
# ======================================================================

@handle_pos_errors
@login_required
def pagos_modal_open(request):
    """
    Abre el modal mostrando los pagos actuales (SIN crear uno nuevo).
    """
    _validar_caja_usuario(request)
    resp = HttpResponse(_render_pagos_modal_body_html(request) + _oob_pagos_html(request))
    resp["HX-Trigger"] = json.dumps({"openPagoModal": {}})
    return resp


@handle_pos_errors
@login_required
@require_POST
def pagos_add_modal(request):
    """
    Agrega una nueva forma de pago y abre el modal.
    Si el carrito está vacío, NO crea pagos (solo muestra el aviso del modal).
    """
    _validar_caja_usuario(request)
    total_base = _cart_total(_cart_get(request))
    if total_base <= 0:
        return pagos_modal_open(request)

    payments = _payments_get(request) or []
    payments.append(_payments_default())
    _payments_save(request, payments)

    resp = HttpResponse(_render_pagos_modal_body_html(request) + _oob_pagos_html(request))
    resp["HX-Trigger"] = json.dumps({"openPagoModal": {}})
    return resp


@handle_pos_errors
@login_required
@require_POST
def pagos_set_modal(request, idx: int):
    _validar_caja_usuario(request)
    payments = _payments_get(request) or []
    if not (0 <= idx < len(payments)):
        return HttpResponse("Índice inválido", status=400)

    p = payments[idx]

    # ✅ 1) Definí el tipo anterior (antes de pisar p["tipo"])
    tipo_prev = (p.get("tipo") or "").strip()

    # ✅ 2) Calculá el tipo nuevo
    tipo = (request.POST.get("tipo") or tipo_prev or "CONTADO").strip()
    p["tipo"] = tipo

    p["monto"] = str(_parse_decimal_ar(request.POST.get("monto")))
    p["referencia"] = (request.POST.get("referencia") or "").strip()

    if tipo == "CUENTA_CORRIENTE":
        # guardamos query del buscador si viene
        if "cc_q" in request.POST:
            p["cc_q"] = (request.POST.get("cc_q") or "").strip()

        # guardamos cliente elegido si viene
        if "cc_cliente_id" in request.POST:
            p["cc_cliente_id"] = (request.POST.get("cc_cliente_id") or "").strip()

        # si hay cliente válido -> referencia = "Apellido, Nombre - DNI"
        ref = ""
        cc_id = p.get("cc_cliente_id") or ""
        if str(cc_id).isdigit():
            cli = Cliente.objects.filter(id=int(cc_id)).only("apellido", "nombre", "dni").first()
            if cli:
                ref = f"{cli.apellido}, {cli.nombre} - {cli.dni}"

        p["referencia"] = ref

        # CC no usa crédito
        p["tarjeta"] = ""
        p["plan_id"] = ""
        p["cuotas"] = 1
        p["recargo_pct"] = "0.00"

    else:
        # si cambio a otro tipo, limpiar CC (y si venía de CC, limpiar referencia)
        p["cc_cliente_id"] = ""
        p["cc_q"] = ""

        # ✅ 3) Limpia referencia SOLO si antes era CC y ahora ya no
        if tipo_prev == "CUENTA_CORRIENTE":
            p["referencia"] = ""



    # =========================
    # CRÉDITO
    # =========================
    if tipo == "CREDITO":
        # solo actualizamos si vienen (evita borrar al tipear monto)
        if "tarjeta" in request.POST:
            p["tarjeta"] = (request.POST.get("tarjeta") or "").strip()

        if "plan_id" in request.POST:
            plan_id = (request.POST.get("plan_id") or "").strip()
            p["plan_id"] = plan_id

            if plan_id:
                plan = PlanCuotas.objects.filter(id=int(plan_id), activo=True).first()
                if plan:
                    p["cuotas"] = int(plan.cuotas)
                    p["recargo_pct"] = str(Decimal(str(plan.recargo_pct)).quantize(Decimal("0.01")))
                    p["tarjeta"] = plan.tarjeta
            else:
                p["cuotas"] = 1
                p["recargo_pct"] = "0.00"

    else:
        # si no es crédito, limpiar campos de crédito
        p["tarjeta"] = ""
        p["plan_id"] = ""
        p["cuotas"] = 1
        p["recargo_pct"] = "0.00"

    payments[idx] = p
    _payments_save(request, payments)

    return HttpResponse(_render_pagos_modal_body_html(request) + _oob_pagos_html(request))


@handle_pos_errors
@login_required
@require_POST
def pagos_del_modal(request, idx: int):
    """
    Quitar desde el modal (y refrescar modal + card).
    """
    _validar_caja_usuario(request)
    payments = _payments_get(request) or []
    if 0 <= idx < len(payments):
        payments.pop(idx)
        _payments_save(request, payments)

    return HttpResponse(_render_pagos_modal_body_html(request) + _oob_pagos_html(request))


@handle_pos_errors
@login_required
@require_POST
def pagos_del_table(request, idx: int):
    """
    Quitar desde la tabla del card (sin devolver modal).
    """
    _validar_caja_usuario(request)
    payments = _payments_get(request) or []
    if 0 <= idx < len(payments):
        payments.pop(idx)
        _payments_save(request, payments)

    return HttpResponse(_oob_pagos_html(request))


@handle_pos_errors
@login_required
@require_POST
def pagos_vaciar_table(request):
    """
    Vaciar pagos desde el card (sin devolver modal).
    """
    _validar_caja_usuario(request)
    _payments_save(request, [])
    return HttpResponse(_oob_pagos_html(request))


# ======================================================================
# Endpoint: Cuotas (HTMX)
# ======================================================================

@handle_pos_errors
@login_required
def pagos_cuotas(request, idx: int):
    _validar_caja_usuario(request)
    tarjeta = (request.GET.get("tarjeta") or "").strip()

    planes = (
        PlanCuotas.objects
        .filter(activo=True, tarjeta=tarjeta)
        .order_by("cuotas")
    )

    payments = _payments_get(request) or []
    selected_plan_id = ""
    if 0 <= idx < len(payments):
        selected_plan_id = (payments[idx].get("plan_id") or "").strip()

    return render(request, "caja/_cuotas_options.html", {
        "idx": idx,
        "tarjeta": tarjeta,
        "planes": planes,
        "selected_plan_id": selected_plan_id,
    })


# ======================================================================
# Helpers: Sucursal (usuario -> perfil; fallback opcional por settings)
# ======================================================================

def _get_pos_sucursal(request):
    user = getattr(request, "user", None)

    if user is not None and getattr(user, "is_authenticated", False):
        try:
            profile = user.panel_profile
        except ObjectDoesNotExist:
            profile = None

        if profile and profile.sucursal_id:
            sucursal = profile.sucursal
            if not sucursal.activa:
                raise ValidationError(
                    "Tu usuario tiene una sucursal asignada pero está inactiva. "
                    "Contactá a un administrador."
                )
            return sucursal

    # Compatibilidad temporal: permite seguir operando con la sucursal fija
    # mientras se termina de asignar sucursal a todos los usuarios.
    if getattr(settings, "POS_SUCURSAL_FALLBACK_TO_SETTINGS", True):
        sid = getattr(settings, "POS_SUCURSAL_ID", None)
        if sid is not None:
            try:
                return Sucursal.objects.get(id=sid, activa=True)
            except Sucursal.DoesNotExist:
                pass

    raise ValidationError(
        "No tenés una sucursal asignada para operar en Caja. "
        "Asignala desde Admin Panel > Usuarios."
    )


def _nombre_usuario_caja(user) -> str:
    if not user:
        return "usuario"
    full_name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    return full_name or getattr(user, "username", "") or f"Usuario #{getattr(user, 'id', '')}"


def _get_caja_sesion_activa(sucursal, for_update: bool = False):
    qs = CajaSesion.objects.select_related("cajero_apertura").filter(
        sucursal=sucursal,
        cerrada_en__isnull=True,
    )
    if for_update:
        qs = qs.select_for_update()
    return qs.first()


def _validar_caja_usuario(request, sucursal=None, for_update: bool = False):
    """
    Devuelve la sesión de caja abierta si el usuario autenticado es quien la abrió.
    Si no hay caja abierta o está abierta por otro cajero, levanta ValidationError.
    """
    if sucursal is None:
        sucursal = _get_pos_sucursal(request)

    sesion = _get_caja_sesion_activa(sucursal, for_update=for_update)
    if not sesion:
        raise ValidationError(
            f"La caja de {sucursal.nombre} está cerrada. Abrila para poder vender."
        )

    if sesion.cajero_apertura_id != request.user.id:
        raise ValidationError(
            f"La caja de {sucursal.nombre} está abierta por {_nombre_usuario_caja(sesion.cajero_apertura)}. "
            "Solo ese cajero puede vender hasta cerrar caja."
        )

    return sesion


def _build_caja_estado(request, sucursal):
    sesion = _get_caja_sesion_activa(sucursal)
    if not sesion:
        return {
            "caja_sesion_activa": None,
            "caja_activa": False,
            "caja_puede_vender": False,
            "caja_abierta_por_otro": False,
            "caja_cajero_activo": None,
            "caja_estado_texto": "Caja cerrada. Abrí caja para habilitar ventas en esta sucursal.",
        }

    es_cajero_actual = sesion.cajero_apertura_id == getattr(request.user, "id", None)
    if es_cajero_actual:
        texto = (
            f"Caja abierta por vos desde {timezone.localtime(sesion.abierta_en):%d/%m/%Y %H:%M}. "
            "Podés operar ventas."
        )
    else:
        texto = (
            f"Caja abierta por {_nombre_usuario_caja(sesion.cajero_apertura)} desde "
            f"{timezone.localtime(sesion.abierta_en):%d/%m/%Y %H:%M}. "
            "No podés vender hasta que cierre caja."
        )

    return {
        "caja_sesion_activa": sesion,
        "caja_activa": True,
        "caja_puede_vender": es_cajero_actual,
        "caja_abierta_por_otro": not es_cajero_actual,
        "caja_cajero_activo": sesion.cajero_apertura,
        "caja_estado_texto": texto,
    }


def _get_ticket_empresa_nombre(sucursal=None) -> str:
    """
    Nombre de empresa para el ticket.
    Prioridad:
    1) AppSetting key='empresa.nombre' (value_str)
    2) settings.EMPRESA_NOMBRE
    3) nombre de sucursal (fallback)
    """
    setting = AppSetting.objects.filter(key="empresa.nombre").only("value_str").first()
    if setting and (setting.value_str or "").strip():
        return setting.value_str.strip()

    cfg = (getattr(settings, "EMPRESA_NOMBRE", "") or "").strip()
    if cfg:
        return cfg

    if sucursal is not None:
        return (getattr(sucursal, "nombre", "") or "").strip() or "Mi empresa"

    return "Mi empresa"


def _get_ticket_empresa_datos(sucursal=None, venta=None) -> dict:
    def _get_setting_str(key: str) -> str:
        row = AppSetting.objects.filter(key=key).only("value_str").first()
        return (getattr(row, "value_str", "") or "").strip()

    venta = venta or None
    snap_cond = (getattr(venta, "empresa_condicion_fiscal_snapshot", "") or "").strip() if venta else ""
    condicion_code = snap_cond or get_empresa_condicion_fiscal()
    condicion_label = dict(CondicionFiscalEmpresa.CHOICES).get(condicion_code, condicion_code or "-")

    snap_nombre = (getattr(venta, "empresa_nombre_snapshot", "") or "").strip() if venta else ""
    snap_razon_social = (getattr(venta, "empresa_razon_social_snapshot", "") or "").strip() if venta else ""
    snap_cuit = (getattr(venta, "empresa_cuit_snapshot", "") or "").strip() if venta else ""
    snap_direccion = (getattr(venta, "empresa_direccion_snapshot", "") or "").strip() if venta else ""

    return {
        "nombre": snap_nombre or _get_ticket_empresa_nombre(sucursal),
        "razon_social": snap_razon_social or _get_setting_str("empresa.razon_social"),
        "cuit": snap_cuit or _get_setting_str("empresa.cuit"),
        "direccion": snap_direccion or _get_setting_str("empresa.direccion"),
        "condicion_fiscal_code": condicion_code,
        "condicion_fiscal_label": condicion_label,
        "es_responsable_inscripto": (
            condicion_code == CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO
        ),
        "es_monotributista": (
            condicion_code == CondicionFiscalEmpresa.MONOTRIBUTISTA
        ),
    }


# ======================================================================
# Helpers: Carrito (session)
# ======================================================================

def _cart_get(request) -> dict:
    return request.session.get("pos_cart", {})


def _cart_save(request, cart: dict):
    request.session["pos_cart"] = cart
    request.session.modified = True


def _cart_total(cart: dict) -> Decimal:
    """
    Total del carrito robusto: tolera datos sucios y carrito vacío.
    """
    total = Decimal("0.00")

    for item in cart.values():
        try:
            precio = Decimal(str(item.get("precio", "0") or "0")).quantize(Decimal("0.01"))
        except Exception:
            precio = Decimal("0.00")

        try:
            qty = int(item.get("qty", 0) or 0)
        except Exception:
            qty = 0

        if qty <= 0:
            continue

        total += (precio * qty)

    return total.quantize(Decimal("0.01"))


def _build_cart_context(request):
    cart = _cart_get(request)
    variante_ids = [int(k) for k in cart.keys()] if cart else []

    variantes = {
        v.id: v
        for v in Variante.objects.select_related("producto").filter(id__in=variante_ids)
    }

    rows = []
    total = Decimal("0.00")

    for vid_str, item in cart.items():
        try:
            vid = int(vid_str)
        except Exception:
            continue

        v = variantes.get(vid)
        if not v:
            continue

        try:
            qty = int(item.get("qty", 0) or 0)
        except Exception:
            qty = 0

        try:
            precio = Decimal(str(item.get("precio", "0") or "0")).quantize(Decimal("0.01"))
        except Exception:
            precio = Decimal("0.00")

        if qty <= 0:
            continue

        subtotal_bruto = (precio * qty).quantize(Decimal("0.01"))
        # Preparado para promociones futuras: descuento por línea (hoy en 0).
        descuento = Decimal("0.00")
        subtotal = (subtotal_bruto - descuento).quantize(Decimal("0.01"))
        total += subtotal

        rows.append({
            "variante": v,
            "qty": qty,
            "precio": precio,
            "subtotal_bruto": subtotal_bruto,
            "descuento": descuento,
            "subtotal": subtotal,
            "fiscal_precio": _desglose_fiscal_pos_safe(precio),
            "fiscal_subtotal": _desglose_fiscal_pos_safe(subtotal),
        })

    total = total.quantize(Decimal("0.01"))
    return {"items": rows, "total": total}


def _get_stock_disponible(sucursal, variante_id: int) -> int:
    row = (
        StockSucursal.objects
        .filter(sucursal=sucursal, variante_id=variante_id)
        .values_list("cantidad", flat=True)
        .first()
    )
    return int(row or 0)


# ======================================================================
# Render: Carrito (HTMX) + OOB Pagos
# ======================================================================

def _render_cart(request):
    sucursal = _get_pos_sucursal(request)

    cart_ctx = _build_cart_context(request)
    variante_ids = [row["variante"].id for row in cart_ctx["items"]]
    stock_map = _build_stock_map(sucursal, variante_ids)


    # Totales de pagos para los OOB del carrito
    payments = _payments_get(request) or []
    total_base = Decimal(cart_ctx["total"]).quantize(Decimal("0.01"))
    pay_ctx = _payments_build_ui_and_totals(payments, total_base)

    return render(request, "caja/_carrito.html", {
        "items": cart_ctx["items"],
        "total": cart_ctx["total"],

        # ✅ esto evita "Sin stock" falso
        "sucursal": sucursal,
        "stock_map": stock_map,

        # ✅ para que los includes/OOB de totales no queden vacíos
        "total_base": total_base,
        "recargos": pay_ctx["recargos"],
        "total_cobrar": pay_ctx["total_cobrar"],
        "pagado": pay_ctx["pagado"],
        "saldo": pay_ctx["saldo"],

        # si ya no usás pagos_body, dejalo apagado
        "oob_pagos": False,

        # permisos/flags
        "permitir_cambiar_precio_venta": permitir_cambiar_precio_venta(sucursal),
        "permitir_sin_stock": permitir_vender_sin_stock(sucursal),
        **_ctx_fiscal_totales_pos(total_base),
    })



def _render_cart_with_toast(request, message: str):
    resp = _render_cart(request)
    resp["HX-Trigger"] = json.dumps({"posToast": {"message": message}})
    return resp


# ======================================================================
# Pantalla POS
# ======================================================================

@handle_pos_errors
@login_required
def pos(request):
    token = request.session.get("pos_confirm_token")
    if not token:
        token = str(uuid.uuid4())
        request.session["pos_confirm_token"] = token

    sucursal = _get_pos_sucursal(request)
    caja_estado = _build_caja_estado(request, sucursal)
    caja_cierre_resumen = request.session.pop("pos_caja_cierre_resumen", None)
    cart_ctx = _build_cart_context(request)
    cart_variante_ids = [row["variante"].id for row in cart_ctx["items"]]
    stock_map = _build_stock_map(sucursal, cart_variante_ids)

    payments = _payments_get(request) or []
    total_base = Decimal(cart_ctx["total"]).quantize(Decimal("0.01"))
    pay_ctx = _payments_build_ui_and_totals(payments, total_base)

    tarjetas = list(
        PlanCuotas.objects.filter(activo=True)
        .values_list("tarjeta", flat=True)
        .distinct()
        .order_by("tarjeta")
    )

    # Venta recién confirmada (para mostrar modal una sola vez)
    last_sale = None
    last_sale_total_items = Decimal("0.00")
    last_sale_total_recargos = Decimal("0.00")
    last_sale_total_final = Decimal("0.00")
    last_sale_pagos = []

    last_sale_id = request.session.pop("pos_last_sale_id", None)
    if last_sale_id:
        try:
            last_sale = (
                Venta.objects
                .select_related("sucursal")
                .prefetch_related("items__variante__producto", "pagos__plan")
                .get(id=int(last_sale_id))
            )

            last_sale_total_items = sum(
                (it.subtotal or Decimal("0.00")) for it in last_sale.items.all()
            ).quantize(Decimal("0.01"))

            last_sale_pagos = list(last_sale.pagos.select_related("plan").all())

            rec = Decimal("0.00")
            for p in last_sale_pagos:
                if p.tipo == "CREDITO":
                    rm = p.recargo_monto or Decimal("0.00")
                    p.recargo_calc = Decimal(rm).quantize(Decimal("0.01"))
                else:
                    p.recargo_calc = Decimal("0.00")
                rec += p.recargo_calc

            last_sale_total_recargos = rec.quantize(Decimal("0.01"))
            last_sale_total_final = (last_sale_total_items + last_sale_total_recargos).quantize(Decimal("0.01"))

        except (Venta.DoesNotExist, ValueError, TypeError):
            last_sale = None

    request.session.modified = True

    return render(request, "caja/pos.html", {
        "sucursal": sucursal,
        "confirm_token": token,
        "cart_items": cart_ctx["items"],
        "cart_total": cart_ctx["total"],
        "stock_map": stock_map,
        "permitir_cambiar_precio_venta": permitir_cambiar_precio_venta(sucursal),
        "permitir_sin_stock": permitir_vender_sin_stock(sucursal),

        # pagos para el card
        "payments": pay_ctx["ui_payments"],
        "total_base": total_base,
        "recargos": pay_ctx["recargos"],
        "total_cobrar": pay_ctx["total_cobrar"],
        "pagado": pay_ctx["pagado"],
        "saldo": pay_ctx["saldo"],
        "tarjetas": tarjetas,

        # modal venta confirmada
        "last_sale": last_sale,
        "last_sale_total_items": last_sale_total_items,
        "last_sale_pagos": last_sale_pagos,
        "last_sale_total_final": last_sale_total_final,
        "caja_cierre_resumen": caja_cierre_resumen,
        **_ctx_fiscal_totales_pos(total_base),
        **caja_estado,
    })


@handle_pos_errors
@login_required
@require_POST
def caja_abrir(request):
    sucursal = _get_pos_sucursal(request)

    with transaction.atomic():
        # MySQL no soporta la unique constraint condicional; serializamos por sucursal.
        Sucursal.objects.select_for_update().only("id").get(id=sucursal.id)
        sesion = _get_caja_sesion_activa(sucursal, for_update=True)
        if sesion:
            if sesion.cajero_apertura_id != request.user.id:
                raise ValidationError(
                    f"La caja de {sucursal.nombre} ya está abierta por {_nombre_usuario_caja(sesion.cajero_apertura)}."
                )
        else:
            CajaSesion.objects.create(
                sucursal=sucursal,
                cajero_apertura=request.user,
            )

    request.session["pos_confirm_token"] = str(uuid.uuid4())
    request.session.modified = True

    is_hx = request.headers.get("HX-Request") == "true" or request.META.get("HTTP_HX_REQUEST") == "true"
    if is_hx:
        resp = HttpResponse("")
        resp["HX-Redirect"] = "/caja/"
        return resp
    return redirect("caja:pos")


@handle_pos_errors
@login_required
@require_POST
def caja_cerrar(request):
    sucursal = _get_pos_sucursal(request)

    with transaction.atomic():
        sesion = _validar_caja_usuario(request, sucursal=sucursal, for_update=True)
        sesion.cerrar(user=request.user)
        sesion.save(update_fields=["cajero_cierre", "cerrada_en"])

        resumen = (
            Venta.objects
            .filter(caja_sesion=sesion, estado=Venta.Estado.CONFIRMADA)
            .aggregate(cantidad=Count("id"), total=Sum("total"))
        )

    _cart_save(request, {})
    _payments_save(request, [])
    request.session["pos_confirm_token"] = str(uuid.uuid4())
    request.session["pos_caja_cierre_resumen"] = {
        "cantidad": int(resumen.get("cantidad") or 0),
        "total": str((resumen.get("total") or Decimal("0.00")).quantize(Decimal("0.01"))),
    }
    request.session.modified = True

    is_hx = request.headers.get("HX-Request") == "true" or request.META.get("HTTP_HX_REQUEST") == "true"
    if is_hx:
        resp = HttpResponse("")
        resp["HX-Redirect"] = "/caja/"
        return resp
    return redirect("caja:pos")


# ======================================================================
# Búsqueda / Scanner
# ======================================================================

@handle_pos_errors
@login_required
def buscar_variantes(request):
    q = (request.GET.get("q") or "").strip()
    results = []

    if q:
        qs = (
            Variante.objects
            .select_related("producto")
            .filter(activo=True, producto__activo=True)
            .filter(
                Q(sku__icontains=q) |
                Q(codigo_barras__icontains=q) |
                Q(producto__nombre__icontains=q)
            )
            .order_by("producto__nombre", "sku")[:50]
        )
        results = list(qs)
        _decorar_variantes_con_fiscal(results)

    sucursal = _get_pos_sucursal(request)

    stock_map = {}
    if results:
        ids = [v.id for v in results]
        stock_rows = (
            StockSucursal.objects
            .filter(sucursal=sucursal, variante_id__in=ids)
            .values("variante_id", "cantidad")
        )
        stock_map = {r["variante_id"]: r["cantidad"] for r in stock_rows}

    return render(request, "caja/_resultados.html", {
        "results": results,
        "sucursal": sucursal,
        "stock_map": stock_map,
        "permitir_sin_stock": permitir_vender_sin_stock(sucursal),
        **_ctx_fiscal_empresa_pos(),
    })


@handle_pos_errors
@login_required
@require_POST
def scan_add(request):
    _validar_caja_usuario(request)
    q = (request.POST.get("q") or "").strip()
    if not q:
        return HttpResponse("Código vacío", status=400)

    exact_qs = (
        Variante.objects
        .select_related("producto")
        .filter(activo=True, producto__activo=True)
        .filter(Q(sku=q) | Q(codigo_barras=q))
    )

    if exact_qs.count() == 1:
        v = exact_qs.first()
        cart = _cart_get(request)
        key = str(v.id)

        if key not in cart:
            cart[key] = {"qty": 1, "precio": str(v.precio)}
        else:
            cart[key]["qty"] = int(cart[key]["qty"]) + 1

        _cart_save(request, cart)

        # ✅ Siempre devolver el carrito (NO crea pagos, NO abre modal)
        return _render_cart(request)

    results = list(
        Variante.objects
        .select_related("producto")
        .filter(activo=True, producto__activo=True)
        .filter(
            Q(sku__icontains=q) |
            Q(codigo_barras__icontains=q) |
            Q(producto__nombre__icontains=q)
        )
        .order_by("producto__nombre", "sku")[:50]
    )
    _decorar_variantes_con_fiscal(results)

    sucursal = _get_pos_sucursal(request)
    stock_map = {}
    if results:
        ids = [v.id for v in results]
        stock_rows = (
            StockSucursal.objects
            .filter(sucursal=sucursal, variante_id__in=ids)
            .values("variante_id", "cantidad")
        )
        stock_map = {r["variante_id"]: r["cantidad"] for r in stock_rows}

    resp = render(request, "caja/_resultados.html", {
        "results": results,
        "sucursal": sucursal,
        "stock_map": stock_map,
        "permitir_sin_stock": permitir_vender_sin_stock(sucursal),
        **_ctx_fiscal_empresa_pos(),
    })
    resp["HX-Retarget"] = "#resultados"
    resp["HX-Reswap"] = "innerHTML"
    return resp


# ======================================================================
# Carrito
# ======================================================================

@handle_pos_errors
@login_required
@require_POST
def carrito_agregar(request, variante_id: int):
    v = get_object_or_404(Variante, id=variante_id, activo=True)
    sucursal = _get_pos_sucursal(request)
    _validar_caja_usuario(request, sucursal=sucursal)

    stock = _get_stock_disponible(sucursal, v.id)
    cart = _cart_get(request)
    key = str(v.id)
    qty_actual = int(cart.get(key, {}).get("qty", 0))
    perm_sin_stock = permitir_vender_sin_stock(sucursal)

    # Cuando no está permitido vender sin stock, validar disponibilidad
    if not perm_sin_stock:
        if stock <= 0:
            return _render_cart_with_toast(request, f"Sin stock en {sucursal.nombre}.")
        if qty_actual + 1 > stock:
            return _render_cart_with_toast(request, f"Stock insuficiente. Disponible: {stock} en {sucursal.nombre}.")

    # Añadir al carrito (siempre): incrementar cantidad o crear entrada
    if key not in cart:
        cart[key] = {"qty": 1, "precio": str(v.precio)}
    else:
        cart[key]["qty"] = qty_actual + 1

    _cart_save(request, cart)
    return _render_cart(request)


@handle_pos_errors
@login_required
@require_POST
def carrito_set_qty(request, variante_id: int):
    _validar_caja_usuario(request)
    cart = _cart_get(request)
    key = str(variante_id)
    sucursal = _get_pos_sucursal(request)
    perm_sin_stock = permitir_vender_sin_stock(sucursal)

    if key not in cart:
        return _render_cart(request)

    try:
        qty = int(request.POST.get("qty") or 1)
    except ValueError:
        qty = 1

    if qty < 1:
        qty = 1

    stock = _get_stock_disponible(sucursal, variante_id)

    if stock <= 0 and not perm_sin_stock:
        del cart[key]
        _cart_save(request, cart)
        return _render_cart_with_toast(request, f"Sin stock en {sucursal.nombre}. Se quitó del carrito.")


    if qty > stock and not perm_sin_stock:
        cart[key]["qty"] = stock
        _cart_save(request, cart)
        return _render_cart_with_toast(request, f"Cantidad ajustada al stock disponible: {stock} en {sucursal.nombre}.")


    cart[key]["qty"] = qty
    _cart_save(request, cart)
    return _render_cart(request)


@handle_pos_errors
@login_required
@require_POST
def carrito_set_precio(request, variante_id: int):
    """Permite actualizar el precio unitario en el carrito si el flag lo autoriza."""
    _validar_caja_usuario(request)
    sucursal = _get_pos_sucursal(request)
    if not permitir_cambiar_precio_venta(sucursal):
        return _render_cart(request)

    cart = _cart_get(request)
    key = str(variante_id)
    if key not in cart:
        return _render_cart(request)

    raw = (request.POST.get("precio") or "").strip()
    try:
        precio = _parse_decimal_ar(raw)
    except Exception:
        precio = Decimal("0.00")

    if precio <= 0:
        # no permitimos precios nulos o negativos; dejar como estaba
        return _render_cart(request)

    cart[key]["precio"] = str(precio)
    _cart_save(request, cart)
    return _render_cart(request)


@handle_pos_errors
@login_required
@require_POST
def carrito_quitar(request, variante_id: int):
    _validar_caja_usuario(request)
    cart = _cart_get(request)
    key = str(variante_id)
    if key in cart:
        del cart[key]
        _cart_save(request, cart)
    return _render_cart(request)


@handle_pos_errors
@login_required
@require_POST
def carrito_vaciar(request):
    _validar_caja_usuario(request)
    _cart_save(request, {})
    _payments_save(request, [])  # limpiar pagos
    return _render_cart(request)


# ======================================================================
# Confirmar
# ======================================================================

@handle_pos_errors
@login_required
@require_POST
def confirmar(request):
    sent_token = (request.POST.get("confirm_token") or "").strip()
    session_token = request.session.get("pos_confirm_token")

    if not session_token or sent_token != session_token:
        resp = HttpResponse("Operación ya procesada o token inválido.", status=409)
        is_hx = request.headers.get("HX-Request") == "true" or request.META.get("HTTP_HX_REQUEST") == "true"
        if is_hx:
            # Refresca el POS para resincronizar token y estado en caso de doble click o token viejo.
            resp["HX-Redirect"] = "/caja/"
        return resp

    sucursal = _get_pos_sucursal(request)
    _validar_caja_usuario(request, sucursal=sucursal)

    cart = _cart_get(request)
    if not cart:
        return HttpResponse("Carrito vacío", status=400)

    total_base = _cart_total(cart).quantize(Decimal("0.01"))

    payments = _payments_get(request) or []
    if not payments:
        return HttpResponse("No hay pagos cargados.", status=400)

    pagos_limpios = []
    suma_montos_base = Decimal("0.00")
    suma_recargos = Decimal("0.00")

    for p in payments:
        tipo = (p.get("tipo") or "").strip()
        if not tipo:
            return HttpResponse("Pago sin tipo.", status=400)

        try:
            monto = Decimal(str(p.get("monto", "0") or "0")).quantize(Decimal("0.01"))
        except Exception:
            return HttpResponse("Monto inválido en pagos.", status=400)

        if monto <= 0:
            continue

        try:
            cuotas = int(p.get("cuotas") or 1)
        except Exception:
            cuotas = 1

        try:
            recargo_pct = Decimal(str(p.get("recargo_pct") or "0")).quantize(Decimal("0.01"))
        except Exception:
            recargo_pct = Decimal("0.00")

        referencia = (p.get("referencia") or "").strip()

        if tipo == "CREDITO":
            if cuotas < 1:
                return HttpResponse("Cuotas inválidas en pago con crédito.", status=400)
            if recargo_pct < 0:
                return HttpResponse("Recargo % inválido en crédito.", status=400)

        recargo_monto = (monto * recargo_pct / Decimal("100")).quantize(Decimal("0.01"))
        coeficiente = (Decimal("1.00") + (recargo_pct / Decimal("100"))).quantize(Decimal("0.0001"))

        suma_montos_base += monto
        if tipo == "CREDITO":
            suma_recargos += recargo_monto

        pagos_limpios.append({
            "tipo": tipo,
            "monto": monto,
            "cuotas": cuotas,
            "recargo_pct": recargo_pct,
            "recargo_monto": recargo_monto,
            "coeficiente": coeficiente,
            "referencia": referencia,

            "pos_proveedor": (p.get("pos_proveedor") or "").strip(),
            "pos_terminal_id": (p.get("pos_terminal_id") or "").strip(),
            "pos_lote": (p.get("pos_lote") or "").strip(),
            "pos_cupon": (p.get("pos_cupon") or "").strip(),
            "pos_autorizacion": (p.get("pos_autorizacion") or "").strip(),
            "pos_marca": (p.get("pos_marca") or "").strip(),
            "pos_ultimos4": (p.get("pos_ultimos4") or "").strip(),

            "cc_cliente_id": (p.get("cc_cliente_id") or "").strip(),
            "plan_id": (p.get("plan_id") or "").strip(),
        })

    suma_montos_base = suma_montos_base.quantize(Decimal("0.01"))
    suma_recargos = suma_recargos.quantize(Decimal("0.01"))

    if suma_montos_base != total_base:
        return HttpResponse(
            f"Pagos base incompletos. Total ${total_base} - Base cargada ${suma_montos_base}.",
            status=400
        )

    total_cobrar = (total_base + suma_recargos).quantize(Decimal("0.01"))
    total_pagado = _payments_total(pagos_limpios).quantize(Decimal("0.01"))

    if total_pagado != total_cobrar:
        return HttpResponse(
            f"Pagos incompletos. Total a cobrar ${total_cobrar} - Pagado ${total_pagado}.",
            status=400
        )

    # Validación previa (sin lock) de CC
    for p in pagos_limpios:
        if p["tipo"] == "CUENTA_CORRIENTE":
            if not str(p.get("cc_cliente_id") or "").isdigit():
                return HttpResponse("Cuenta corriente: falta seleccionar cliente.", status=400)

            cuenta = CuentaCorriente.objects.filter(
                cliente_id=int(p["cc_cliente_id"]),
                activa=True
            ).first()

            if not cuenta:
                return HttpResponse("Cuenta corriente: el cliente no tiene cuenta corriente activa.", status=400)

    try:
        with transaction.atomic():
            caja_sesion = _validar_caja_usuario(request, sucursal=sucursal, for_update=True)
            # =========================
            # Venta
            # =========================
            venta = Venta.objects.create(
                sucursal=sucursal,
                caja_sesion=caja_sesion,
                cajero=request.user,
                estado=Venta.Estado.BORRADOR,
                medio_pago=Venta.MedioPago.EFECTIVO,
                total=total_cobrar,
            )

            # =========================
            # Items
            # =========================
            for vid_str, item in cart.items():
                v = get_object_or_404(Variante, id=int(vid_str), activo=True)
                qty = int(item["qty"])
                precio = Decimal(item["precio"]).quantize(Decimal("0.01"))
                VentaItem.objects.create(
                    venta=venta,
                    variante=v,
                    cantidad=qty,
                    precio_unitario=precio,
                )

            # =========================
            # Pagos
            # =========================
            for p in pagos_limpios:
                plan_obj = None
                if p.get("plan_id"):
                    try:
                        plan_obj = PlanCuotas.objects.filter(id=int(p["plan_id"]), activo=True).first()
                    except (TypeError, ValueError):
                        plan_obj = None
                        # Si es Cuenta Corriente, guardamos el label en referencia (Apellido, Nombre - DNI)
                if p.get("tipo") == "CUENTA_CORRIENTE":
                    cc_id = (p.get("cc_cliente_id") or "").strip()
                    if cc_id.isdigit():
                        cli = Cliente.objects.filter(id=int(cc_id), activo=True).first()
                        if cli:
                            p["referencia"] = f"{cli.apellido}, {cli.nombre} - {cli.dni}"
                        else:
                            p["referencia"] = ""
                    else:
                        p["referencia"] = ""


                VentaPago.objects.create(
                    venta=venta,
                    plan=plan_obj,
                    tipo=p["tipo"],
                    monto=p["monto"],
                    cuotas=p["cuotas"],
                    recargo_pct=p["recargo_pct"],
                    recargo_monto=p["recargo_monto"],
                    coeficiente=p["coeficiente"],
                    referencia=p["referencia"],
                    pos_proveedor=p.get("pos_proveedor", ""),
                    pos_terminal_id=p.get("pos_terminal_id", ""),
                    pos_lote=p.get("pos_lote", ""),
                    pos_cupon=p.get("pos_cupon", ""),
                    pos_autorizacion=p.get("pos_autorizacion", ""),
                    pos_marca=p.get("pos_marca", ""),
                    pos_ultimos4=(p.get("pos_ultimos4", "") or "")[:4],
                )

            # =========================
            # Confirmar venta (stock/estado/etc)
            # =========================
            confirmar_venta(venta)

            # =========================
            # Cuenta Corriente: generar DÉBITO (con lock)
            # =========================
            for p in pagos_limpios:
                if p.get("tipo") != "CUENTA_CORRIENTE":
                    continue

                cc_cliente_id = p.get("cc_cliente_id")
                if not str(cc_cliente_id or "").isdigit():
                    raise ValidationError("Cuenta corriente: falta seleccionar cliente.")

                cc_cliente_id = int(cc_cliente_id)

                cuenta = (
                    CuentaCorriente.objects
                    .select_for_update()
                    .filter(cliente_id=cc_cliente_id, activa=True)
                    .first()
                )

                if not cuenta:
                    raise ValidationError("Cuenta corriente: el cliente no tiene cuenta corriente activa.")

                MovimientoCuentaCorriente.objects.create(
                    cuenta=cuenta,
                    tipo=MovimientoCuentaCorriente.Tipo.DEBITO,
                    monto=p["monto"].quantize(Decimal("0.01")),
                    venta=venta,
                    referencia=f"Venta #{venta.id}",
                    observacion="Débito generado desde POS",
                )

            # Por si confirmar_venta() recalcula y pisa el total:
            venta.total = total_cobrar
            venta.save(update_fields=["total"])

    except ValidationError as e:
        return HttpResponse(str(e), status=400)

    request.session["pos_last_sale_id"] = venta.id

    _cart_save(request, {})
    _payments_save(request, [])
    request.session["pos_confirm_token"] = str(uuid.uuid4())
    request.session.modified = True

    resp = HttpResponse("")
    resp["HX-Redirect"] = "/caja/"
    return resp


# ======================================================================
# Ticket
# ======================================================================

@login_required
def ticket(request, venta_id: int):
    venta = get_object_or_404(
        Venta.objects
        .select_related("sucursal", "cajero")
        .prefetch_related(
            "items__variante__producto",
            "items__variante__atributos__atributo",
            "items__variante__atributos__valor",
            "pagos__plan",
        ),
        id=venta_id
    )

    def build_nombre_cliente(variante):
        base = (variante.producto.nombre or "").strip()

        color = ""
        talle = ""

        for va in variante.atributos.all():
            nom = (va.atributo.nombre or "").strip().lower()
            val = (va.valor.valor or "").strip()
            if not val:
                continue

            if nom == "color":
                color = val
            elif nom in ("talle", "tamaño", "tamanio", "size"):
                talle = val

        partes = [p for p in (base, color, talle) if p]
        return " - ".join(partes) if partes else (variante.sku or base or "Item")

    # pegamos el nombre “cliente” directo en cada item (lo más confiable)
    for it in venta.items.all():
        it.nombre_cliente = build_nombre_cliente(it.variante)

    total_items = sum((it.subtotal or Decimal("0.00")) for it in venta.items.all()).quantize(Decimal("0.01"))
    total_recargos = sum((p.recargo_monto or Decimal("0.00")) for p in venta.pagos.all()).quantize(Decimal("0.01"))
    total_final = (venta.total or Decimal("0.00")).quantize(Decimal("0.01"))
    empresa_datos = _get_ticket_empresa_datos(venta.sucursal, venta=venta)

    if (
        getattr(venta, "fiscal_items_sin_impuestos_nacionales", None) is not None
        and getattr(venta, "fiscal_items_iva_contenido", None) is not None
    ):
        fiscal_items = DesgloseFiscalMonto(
            monto_final=total_items,
            monto_sin_impuestos_nacionales=Decimal(venta.fiscal_items_sin_impuestos_nacionales or 0).quantize(Decimal("0.01")),
            iva_contenido=Decimal(venta.fiscal_items_iva_contenido or 0).quantize(Decimal("0.01")),
            iva_alicuota_pct=IVA_GENERAL_PCT,
            otros_impuestos_nacionales_indirectos=Decimal(
                getattr(venta, "fiscal_items_otros_impuestos_nacionales_indirectos", 0) or 0
            ).quantize(Decimal("0.01")),
        )
    else:
        fiscal_items = desglosar_monto_final_gravado_con_iva(total_items)

    cajero_nombre = "-"
    cajero_id = None
    if getattr(venta, "cajero", None):
        cajero_id = venta.cajero_id
        cajero_nombre = (venta.cajero.get_full_name() or "").strip() or venta.cajero.username

    auto_print = (request.GET.get("print") == "1")

    return render(request, "caja/ticket.html", {
        "venta": venta,
        "empresa_nombre": empresa_datos["nombre"],
        "empresa_razon_social": empresa_datos["razon_social"],
        "empresa_cuit": empresa_datos["cuit"],
        "empresa_direccion": empresa_datos["direccion"],
        "empresa_condicion_fiscal_code": empresa_datos["condicion_fiscal_code"],
        "empresa_condicion_fiscal_label": empresa_datos["condicion_fiscal_label"],
        "empresa_es_ri": empresa_datos["es_responsable_inscripto"],
        "empresa_es_monotributista": empresa_datos["es_monotributista"],
        "fiscal_items": fiscal_items,
        "cajero_nombre": cajero_nombre,
        "cajero_id": cajero_id,
        "total_items": total_items,
        "total_recargos": total_recargos,
        "total_final": total_final,
        "auto_print": auto_print,
    })
# ======================================================================
# Compatibilidad: Endpoints viejos (pagos_add / pagos_del / pagos_set)
# ======================================================================

def _render_pagos_body_html(request) -> str:
    """
    Render legacy: si todavía tenés pantallas viejas que usan #pagos_body.
    Si el template no existe, podés borrarlo o cambiarlo por el que uses.
    """
    ctx = _ctx_pagos_pos(request)
    return render_to_string("caja/_pagos_body.html", ctx, request=request)


@handle_pos_errors
@login_required
@require_POST
def pagos_add(request):
    """
    Endpoint viejo: agrega un pago y devuelve body + OOB.
    """
    _validar_caja_usuario(request)
    total_base = _cart_total(_cart_get(request))
    if total_base <= 0:
        # si el carrito está vacío, solo refrescamos para no crear pagos basura
        return HttpResponse(_render_pagos_body_html(request) + _oob_pagos_html(request))

    payments = _payments_get(request) or []
    payments.append(_payments_default())
    _payments_save(request, payments)

    return HttpResponse(_render_pagos_body_html(request) + _oob_pagos_html(request))


@handle_pos_errors
@login_required
@require_POST
def pagos_del(request, idx: int):
    """
    Endpoint viejo: quita un pago por índice y devuelve body + OOB.
    """
    _validar_caja_usuario(request)
    payments = _payments_get(request) or []
    if 0 <= idx < len(payments):
        payments.pop(idx)
        _payments_save(request, payments)

    return HttpResponse(_render_pagos_body_html(request) + _oob_pagos_html(request))


@handle_pos_errors
@login_required
@require_POST
def pagos_set(request, idx: int):
    _validar_caja_usuario(request)
    payments = _payments_get(request) or []
    if not (0 <= idx < len(payments)):
        return HttpResponse("Índice inválido", status=400)

    p = payments[idx]

    tipo = (request.POST.get("tipo") or p.get("tipo") or "CONTADO").strip()
    p["tipo"] = tipo

    p["monto"] = str(_parse_decimal_ar(request.POST.get("monto")))
    p["referencia"] = (request.POST.get("referencia") or "").strip()

    if tipo == "CREDITO":
        tarjeta = (request.POST.get("tarjeta") or "").strip()

        if tarjeta and p.get("tarjeta") != tarjeta:
            p.pop("cuotas", None)
            p.pop("plan_id", None)

        if tarjeta:
            p["tarjeta"] = tarjeta
        else:
            p.pop("tarjeta", None)
            p.pop("cuotas", None)
            p.pop("plan_id", None)
    else:
        p.pop("tarjeta", None)
        p.pop("cuotas", None)
        p.pop("plan_id", None)

    payments[idx] = p
    _payments_save(request, payments)  # importante que marque modified

    # ✅ Render del fragmento que vive dentro de #pagos_table
    
    # ✅ esto TIENE que ejecutarse antes del return
    request.session["payments"] = payments
    request.session.modified = True

    

    return HttpResponse(_render_pagos_body_html(request) + _oob_pagos_html(request))

# =========================
# Cuenta Corriente: búsqueda y selección de cliente (modal)
# =========================

@handle_pos_errors
@login_required
def cc_buscar_clientes(request, idx: int):
    _validar_caja_usuario(request)
    q = (request.GET.get("q") or "").strip()

    results = []
    if q:
        qs = Cliente.objects.filter(activo=True)
        if q.isdigit():
            qs = qs.filter(dni__icontains=q)
        else:
            qs = qs.filter(Q(apellido__icontains=q) | Q(nombre__icontains=q))
        results = list(qs.order_by("apellido", "nombre")[:20])

    # Para pintar si tiene CC activa (sin calcular saldo acá)
    cc_activa_ids = set(
        CuentaCorriente.objects.filter(activa=True, cliente__in=results)
        .values_list("cliente_id", flat=True)
    )

    return render(request, "caja/_cc_results.html", {
        "idx": idx,
        "q": q,
        "results": results,
        "cc_activa_ids": cc_activa_ids,
    })


@handle_pos_errors
@login_required
@require_POST
def cc_pick_cliente_modal(request, idx: int, cliente_id: int):
    _validar_caja_usuario(request)
    payments = _payments_get(request) or []
    if not (0 <= idx < len(payments)):
        return HttpResponse("Índice inválido", status=400)

    p = payments[idx]
    # Solo tiene sentido si ese pago está en CC
    p["tipo"] = p.get("tipo") or "CUENTA_CORRIENTE"
    p["cc_cliente_id"] = str(cliente_id)

    payments[idx] = p
    _payments_save(request, payments)

    return HttpResponse(_render_pagos_modal_body_html(request) + _oob_pagos_html(request))


