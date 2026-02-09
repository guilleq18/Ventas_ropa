# caja/views.py
# Comentarios en español como pediste.

from decimal import Decimal
import uuid
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.models import Sucursal
from catalogo.models import Variante, StockSucursal
from ventas.models import Venta, VentaItem, VentaPago, PlanCuotas
from ventas.services import confirmar_venta


# =========================
# Helpers: Pagos (session)
# =========================

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

        # POS (opcionales)
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
    }


def _payments_total(payments: list) -> Decimal:
    """
    Total realmente cobrado (lo que entra a caja).
    - CREDITO: monto + recargo
    - resto: monto
    """
    total = Decimal("0")
    for p in payments:
        tipo = (p.get("tipo") or "").strip()

        try:
            monto = Decimal(str(p.get("monto", "0") or "0"))
        except Exception:
            monto = Decimal("0")

        if monto <= 0:
            continue

        if tipo == "CREDITO":
            try:
                recargo_pct = Decimal(str(p.get("recargo_pct") or "0"))
            except Exception:
                recargo_pct = Decimal("0")

            recargo_monto = (monto * recargo_pct / Decimal("100")).quantize(Decimal("0.01"))
            total += (monto + recargo_monto)
        else:
            total += monto

    return total.quantize(Decimal("0.01"))


def _payments_build_ui_and_totals(payments: list, total_base: Decimal) -> dict:
    """
    Devuelve:
      - ui_payments: lista de pagos enriquecidos (tipo_locked, cálculos, selected_plan_id, etc.)
      - recargos: suma recargos crédito
      - total_cobrar: total_base + recargos
      - pagado: _payments_total(payments)
      - saldo: total_cobrar - pagado
    """
    ui_payments = []
    recargos_credito = Decimal("0")

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

        # bloquear SOLO el select de TIPO cuando el monto ya impactó (monto > 0)
        p_ui["tipo_locked"] = bool(monto > 0)

        # para marcar seleccionado en cuotas (si tu _cuotas_options lo usa)
        p_ui["selected_plan_id"] = (p.get("plan_id") or "").strip()

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


# =========================
# Helpers: Sucursal fija
# =========================

def _get_pos_sucursal():
    sid = getattr(settings, "POS_SUCURSAL_ID", 1)
    return Sucursal.objects.get(id=sid, activa=True)


# =========================
# Helpers: Carrito (session)
# =========================

def _cart_get(request) -> dict:
    return request.session.get("pos_cart", {})


def _cart_save(request, cart: dict):
    request.session["pos_cart"] = cart
    request.session.modified = True


def _cart_total(cart: dict) -> Decimal:
    """
    Total del carrito robusto: tolera datos sucios y carrito vacío.
    """
    total = Decimal("0")
    for item in cart.values():
        try:
            precio = Decimal(str(item.get("precio", "0") or "0"))
        except Exception:
            precio = Decimal("0")

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
    total = Decimal("0")

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
            precio = Decimal(str(item.get("precio", "0") or "0"))
        except Exception:
            precio = Decimal("0")

        if qty <= 0:
            continue

        subtotal = (precio * qty).quantize(Decimal("0.01"))
        total += subtotal

        rows.append({
            "variante": v,
            "qty": qty,
            "precio": precio,
            "subtotal": subtotal,
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


def _render_cart_with_toast(request, message: str):
    resp = _render_cart(request)
    resp["HX-Trigger"] = json.dumps({"posToast": {"message": message}})
    return resp


def _render_cart(request):
    cart = _cart_get(request)
    total = _cart_total(cart)

    variante_ids = [int(k) for k in cart.keys()] if cart else []

    variantes = {
        v.id: v
        for v in Variante.objects.select_related("producto").filter(id__in=variante_ids)
    }

    rows = []
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
            precio = Decimal(str(item.get("precio", "0") or "0"))
        except Exception:
            precio = Decimal("0")

        if qty <= 0:
            continue

        rows.append({
            "variante": v,
            "qty": qty,
            "precio": precio,
            "subtotal": (precio * qty).quantize(Decimal("0.01")),
        })

    sucursal = _get_pos_sucursal()

    stock_map = {}
    if variante_ids:
        stock_rows = (
            StockSucursal.objects
            .filter(sucursal=sucursal, variante_id__in=variante_ids)
            .values("variante_id", "cantidad")
        )
        stock_map = {r["variante_id"]: r["cantidad"] for r in stock_rows}

    # asegurar pagos SOLO si hay total > 0
    payments = _payments_get(request)
    if total > 0 and not payments:
        _payments_save(request, [_payments_default()])
        payments = _payments_get(request)

    total_base = total
    pay_ctx = _payments_build_ui_and_totals(payments, total_base)

    tarjetas = list(
        PlanCuotas.objects.filter(activo=True)
        .values_list("tarjeta", flat=True)
        .distinct()
        .order_by("tarjeta")
    )

    return render(request, "caja/_carrito.html", {
        "items": rows,
        "total": total,
        "sucursal": sucursal,
        "stock_map": stock_map,

        # OOB pagos desde carrito
        "oob_pagos": True,

        # para _pagos_body.html (usa payments + totales)
        "payments": pay_ctx["ui_payments"],
        "total_base": total_base,
        "recargos": pay_ctx["recargos"],
        "total_cobrar": pay_ctx["total_cobrar"],
        "pagado": pay_ctx["pagado"],
        "saldo": pay_ctx["saldo"],
        "tarjetas": tarjetas,
    })


# =========================
# Render de Pagos (UI)
# =========================

def _render_pagos(request):
    payments = _payments_get(request)
    total_base = _cart_total(_cart_get(request))

    pay_ctx = _payments_build_ui_and_totals(payments, total_base)

    tarjetas = list(
        PlanCuotas.objects.filter(activo=True)
        .values_list("tarjeta", flat=True)
        .distinct()
        .order_by("tarjeta")
    )

    # DEVUELVE SOLO EL BODY (hx-target="#pagos_body")
    return render(request, "caja/_pagos_body.html", {
        "payments": pay_ctx["ui_payments"],
        "total_base": total_base,
        "recargos": pay_ctx["recargos"],
        "total_cobrar": pay_ctx["total_cobrar"],
        "pagado": pay_ctx["pagado"],
        "saldo": pay_ctx["saldo"],
        "tarjetas": tarjetas,
    })


# =========================
# Endpoints Pagos
# =========================

@login_required
def pagos_cuotas(request, idx: int):
    tarjeta = (request.GET.get("tarjeta") or "").strip()

    planes = (
        PlanCuotas.objects
        .filter(activo=True, tarjeta=tarjeta)
        .order_by("cuotas")
    )

    payments = _payments_get(request)
    selected_plan_id = ""
    if 0 <= idx < len(payments):
        selected_plan_id = (payments[idx].get("plan_id") or "").strip()

    return render(request, "caja/_cuotas_options.html", {
        "idx": idx,
        "tarjeta": tarjeta,
        "planes": planes,
        "selected_plan_id": selected_plan_id,
    })


@login_required
@require_POST
def pagos_add(request):
    payments = _payments_get(request)
    payments.append(_payments_default())
    _payments_save(request, payments)
    return _render_pagos(request)


@login_required
@require_POST
def pagos_del(request, idx: int):
    payments = _payments_get(request)
    if 0 <= idx < len(payments):
        payments.pop(idx)
        _payments_save(request, payments)
    return _render_pagos(request)


@login_required
@require_POST
def pagos_set(request, idx: int):
    payments = _payments_get(request)
    if not (0 <= idx < len(payments)):
        return _render_pagos(request)

    p = payments[idx]

    if request.POST.get("monto") is not None:
        p["monto"] = request.POST.get("monto") or p.get("monto", "0.00")

    if request.POST.get("tipo") is not None:
        p["tipo"] = (request.POST.get("tipo") or p.get("tipo", "CONTADO")).strip()

    tipo = (p.get("tipo") or "CONTADO").strip()

    if tipo != "CREDITO":
        p["tarjeta"] = ""
        p["plan_id"] = ""
        p["cuotas"] = 1
        p["recargo_pct"] = "0.00"

    if request.POST.get("tarjeta") is not None:
        tarjeta = (request.POST.get("tarjeta") or "").strip()

        if tarjeta:
            p["tipo"] = "CREDITO"
            p["tarjeta"] = tarjeta

            plan_default = (
                PlanCuotas.objects
                .filter(activo=True, tarjeta=tarjeta)
                .order_by("cuotas")
                .first()
            )

            if plan_default:
                p["plan_id"] = str(plan_default.id)
                p["cuotas"] = int(plan_default.cuotas)
                p["recargo_pct"] = str(plan_default.recargo_pct)
            else:
                p["plan_id"] = ""
                p["cuotas"] = 1
                p["recargo_pct"] = "0.00"
        else:
            p["tarjeta"] = ""
            p["plan_id"] = ""
            p["cuotas"] = 1
            p["recargo_pct"] = "0.00"

    plan_id = (request.POST.get("plan_id") or "").strip()
    if plan_id:
        try:
            plan = PlanCuotas.objects.get(id=int(plan_id), activo=True)
        except (PlanCuotas.DoesNotExist, ValueError):
            return HttpResponse("Plan inválido", status=400)

        p["tipo"] = "CREDITO"
        p["plan_id"] = str(plan.id)
        p["tarjeta"] = plan.tarjeta
        p["cuotas"] = int(plan.cuotas)
        p["recargo_pct"] = str(plan.recargo_pct)

    if request.POST.get("referencia") is not None:
        p["referencia"] = request.POST.get("referencia") or ""

    p["pos_proveedor"] = request.POST.get("pos_proveedor") or p.get("pos_proveedor", "")
    p["pos_terminal_id"] = request.POST.get("pos_terminal_id") or p.get("pos_terminal_id", "")
    p["pos_lote"] = request.POST.get("pos_lote") or p.get("pos_lote", "")
    p["pos_cupon"] = request.POST.get("pos_cupon") or p.get("pos_cupon", "")
    p["pos_autorizacion"] = request.POST.get("pos_autorizacion") or p.get("pos_autorizacion", "")
    p["pos_marca"] = request.POST.get("pos_marca") or p.get("pos_marca", "")
    p["pos_ultimos4"] = request.POST.get("pos_ultimos4") or p.get("pos_ultimos4", "")

    payments[idx] = p
    _payments_save(request, payments)
    return _render_pagos(request)


# =========================
# Pantalla POS
# =========================

@login_required
def pos(request):
    token = str(uuid.uuid4())
    request.session["pos_confirm_token"] = token

    sucursal = _get_pos_sucursal()
    cart_ctx = _build_cart_context(request)

    payments = _payments_get(request)
    if not payments:
        payments = [_payments_default()]
        _payments_save(request, payments)

    total_base = Decimal(cart_ctx["total"]).quantize(Decimal("0.01"))
    pay_ctx = _payments_build_ui_and_totals(payments, total_base)

    tarjetas = list(
        PlanCuotas.objects.filter(activo=True)
        .values_list("tarjeta", flat=True)
        .distinct()
        .order_by("tarjeta")
    )

    return render(request, "caja/pos.html", {
        "sucursal": sucursal,
        "confirm_token": token,
        "cart_items": cart_ctx["items"],
        "cart_total": cart_ctx["total"],

        "payments": pay_ctx["ui_payments"],
        "total_base": total_base,
        "recargos": pay_ctx["recargos"],
        "total_cobrar": pay_ctx["total_cobrar"],
        "pagado": pay_ctx["pagado"],
        "saldo": pay_ctx["saldo"],
        "tarjetas": tarjetas,
    })


# =========================
# Búsqueda / Scanner
# =========================

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

    sucursal = _get_pos_sucursal()

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
    })


@login_required
@require_POST
def scan_add(request):
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

        payments = _payments_get(request)
        if not payments:
            _payments_save(request, [_payments_default()])

        # ✅ SIEMPRE devolver el carrito en match exacto
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

    sucursal = _get_pos_sucursal()
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
    })
    resp["HX-Retarget"] = "#resultados"
    resp["HX-Reswap"] = "innerHTML"
    return resp


# =========================
# Carrito
# =========================

@login_required
@require_POST
def carrito_agregar(request, variante_id: int):
    v = get_object_or_404(Variante, id=variante_id, activo=True)
    sucursal = _get_pos_sucursal()

    stock = _get_stock_disponible(sucursal, v.id)
    cart = _cart_get(request)
    key = str(v.id)
    qty_actual = int(cart.get(key, {}).get("qty", 0))

    if stock <= 0:
        return _render_cart_with_toast(request, f"Sin stock en {sucursal.nombre}.")

    if qty_actual + 1 > stock:
        return _render_cart_with_toast(request, f"Stock insuficiente. Disponible: {stock} en {sucursal.nombre}.")

    if key not in cart:
        cart[key] = {"qty": 1, "precio": str(v.precio)}
    else:
        cart[key]["qty"] = qty_actual + 1

    _cart_save(request, cart)
    return _render_cart(request)


@login_required
@require_POST
def carrito_set_qty(request, variante_id: int):
    cart = _cart_get(request)
    key = str(variante_id)

    if key not in cart:
        return _render_cart(request)

    try:
        qty = int(request.POST.get("qty") or 1)
    except ValueError:
        qty = 1

    if qty < 1:
        qty = 1

    sucursal = _get_pos_sucursal()
    stock = _get_stock_disponible(sucursal, variante_id)

    if stock <= 0:
        del cart[key]
        _cart_save(request, cart)
        return _render_cart_with_toast(request, f"Sin stock en {sucursal.nombre}. Se quitó del carrito.")

    if qty > stock:
        cart[key]["qty"] = stock
        _cart_save(request, cart)
        return _render_cart_with_toast(request, f"Cantidad ajustada al stock disponible: {stock} en {sucursal.nombre}.")

    cart[key]["qty"] = qty
    _cart_save(request, cart)
    return _render_cart(request)


@login_required
@require_POST
def carrito_quitar(request, variante_id: int):
    cart = _cart_get(request)
    key = str(variante_id)
    if key in cart:
        del cart[key]
        _cart_save(request, cart)
    return _render_cart(request)


@login_required
@require_POST
def carrito_vaciar(request):
    _cart_save(request, {})
    _payments_save(request, [])  # limpiar pagos
    return _render_cart(request)


# =========================
# Confirmar
# =========================

@login_required
@require_POST
def confirmar(request):
    sent_token = (request.POST.get("confirm_token") or "").strip()
    session_token = request.session.get("pos_confirm_token")

    if not session_token or sent_token != session_token:
        return HttpResponse("Operación ya procesada o token inválido.", status=409)

    sucursal = _get_pos_sucursal()

    cart = _cart_get(request)
    if not cart:
        return HttpResponse("Carrito vacío", status=400)

    total_venta = _cart_total(cart).quantize(Decimal("0.01"))

    payments = _payments_get(request)
    if not payments:
        return HttpResponse("No hay pagos cargados.", status=400)

    pagos_limpios = []
    suma = Decimal("0")

    for p in payments:
        tipo = (p.get("tipo") or "").strip()
        if not tipo:
            return HttpResponse("Pago sin tipo.", status=400)

        try:
            monto = Decimal(str(p.get("monto", "0") or "0"))
        except Exception:
            return HttpResponse("Monto inválido en pagos.", status=400)

        if monto <= 0:
            continue

        try:
            cuotas = int(p.get("cuotas") or 1)
        except Exception:
            cuotas = 1

        try:
            recargo_pct = Decimal(str(p.get("recargo_pct") or "0"))
        except Exception:
            recargo_pct = Decimal("0")

        referencia = (p.get("referencia") or "").strip()

        if tipo == "CREDITO":
            if cuotas < 1:
                return HttpResponse("Cuotas inválidas en pago con crédito.", status=400)
            if recargo_pct < 0:
                return HttpResponse("Recargo % inválido en crédito.", status=400)

        recargo_monto = (monto * recargo_pct / Decimal("100")).quantize(Decimal("0.01"))
        coeficiente = (Decimal("1") + (recargo_pct / Decimal("100"))).quantize(Decimal("0.0001"))

        if tipo == "CREDITO":
            suma += (monto + recargo_monto)
        else:
            suma += monto

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

            "tarjeta": (p.get("tarjeta") or "").strip(),
            "plan_id": (p.get("plan_id") or "").strip(),
        })

    if suma.quantize(Decimal("0.01")) != total_venta:
        return HttpResponse(
            f"Pagos incompletos. Total ${total_venta} - Pagado ${suma.quantize(Decimal('0.01'))}.",
            status=400
        )

    venta = Venta.objects.create(
        sucursal=sucursal,
        estado=Venta.Estado.BORRADOR,
        medio_pago=Venta.MedioPago.EFECTIVO,  # compat
    )

    for vid_str, item in cart.items():
        v = get_object_or_404(Variante, id=int(vid_str), activo=True)
        qty = int(item["qty"])
        precio = Decimal(item["precio"])
        VentaItem.objects.create(
            venta=venta,
            variante=v,
            cantidad=qty,
            precio_unitario=precio,
        )

    for p in pagos_limpios:
        plan_obj = None
        if p.get("plan_id"):
            try:
                plan_obj = PlanCuotas.objects.filter(id=int(p["plan_id"]), activo=True).first()
            except (TypeError, ValueError):
                plan_obj = None

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

    try:
        confirmar_venta(venta)
    except ValidationError as e:
        venta.estado = Venta.Estado.ANULADA
        venta.save()
        return HttpResponse(str(e), status=400)

    # limpiar sesión
    _cart_save(request, {})
    _payments_save(request, [_payments_default()])

    # rotar token solo si OK
    new_token = str(uuid.uuid4())
    request.session["pos_confirm_token"] = new_token
    request.session.modified = True

    return render(request, "caja/_confirm_ok.html", {
        "venta": venta,
        "new_token": new_token,
        "payments": _payments_get(request),
        "total": Decimal("0"),
        "pagado": Decimal("0"),
        "saldo": Decimal("0"),
        "sucursal": sucursal,
    })


@login_required
def ticket(request, venta_id: int):
    venta = get_object_or_404(
        Venta.objects.select_related("sucursal").prefetch_related("items__variante__producto"),
        id=venta_id
    )
    return render(request, "caja/ticket.html", {"venta": venta})
