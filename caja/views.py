# caja/views.py
from decimal import Decimal
import uuid

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.models import Sucursal
from catalogo.models import Variante
from ventas.models import Venta, VentaItem
from ventas.services import confirmar_venta
from django.core.exceptions import ValidationError
from django.db.models import Q
from decimal import Decimal
from catalogo.models import Variante
from django.conf import settings
from core.models import Sucursal
from catalogo.models import Variante, StockSucursal
import json
from django.views.decorators.http import require_POST
from catalogo.models import StockSucursal

def _get_stock_disponible(sucursal, variante_id: int) -> int:
    row = (
        StockSucursal.objects
        .filter(sucursal=sucursal, variante_id=variante_id)
        .values_list("cantidad", flat=True)
        .first()
    )
    return int(row or 0)

def _render_cart_with_toast(request, message: str):
    resp = _render_cart(request)  # tu función que renderiza caja/_carrito.html
    resp["HX-Trigger"] = json.dumps({"posToast": {"message": message}})
    return resp



def _get_pos_sucursal():
    sid = getattr(settings, "POS_SUCURSAL_ID", 1)
    return Sucursal.objects.get(id=sid, activa=True)


def _build_cart_context(request):
    cart = request.session.get("pos_cart", {})
    variante_ids = [int(k) for k in cart.keys()]
    variantes = {
        v.id: v
        for v in Variante.objects.select_related("producto").filter(id__in=variante_ids)
    }

    rows = []
    total = Decimal("0")

    for vid_str, item in cart.items():
        vid = int(vid_str)
        v = variantes.get(vid)
        if not v:
            continue

        qty = int(item["qty"])
        precio = Decimal(item["precio"])
        subtotal = precio * qty
        total += subtotal

        rows.append({
            "variante": v,
            "qty": qty,
            "precio": precio,
            "subtotal": subtotal,
        })

    return {"items": rows, "total": total}

def _cart_get(request) -> dict:
    return request.session.get("pos_cart", {})

def _cart_save(request, cart: dict):
    request.session["pos_cart"] = cart
    request.session.modified = True

def _cart_total(cart: dict) -> Decimal:
    total = Decimal("0")
    for _, item in cart.items():
        total += Decimal(item["precio"]) * int(item["qty"])
    return total

def _render_cart(request):
    cart = _cart_get(request)
    variante_ids = [int(k) for k in cart.keys()]
    variantes = {v.id: v for v in Variante.objects.select_related("producto").filter(id__in=variante_ids)}

    rows = []
    for vid_str, item in cart.items():
        vid = int(vid_str)
        v = variantes.get(vid)
        if not v:
            continue
        rows.append({
            "variante": v,
            "qty": int(item["qty"]),
            "precio": Decimal(item["precio"]),
            "subtotal": Decimal(item["precio"]) * int(item["qty"]),
        })

    total = _cart_total(cart)

    # ✅ stock por sucursal fija
    sucursal = _get_pos_sucursal()
    stock_map = {}
    if variante_ids:
        stock_rows = (
            StockSucursal.objects
            .filter(sucursal=sucursal, variante_id__in=variante_ids)
            .values("variante_id", "cantidad")
        )
        stock_map = {r["variante_id"]: r["cantidad"] for r in stock_rows}

    return render(request, "caja/_carrito.html", {
        "items": rows,
        "total": total,
        "sucursal": sucursal,
        "stock_map": stock_map,
    })



@login_required
def pos(request):
    token = str(uuid.uuid4())
    request.session["pos_confirm_token"] = token

    sucursal = _get_pos_sucursal()
    cart_ctx = _build_cart_context(request)

    return render(request, "caja/pos.html", {
        "sucursal": sucursal,
        "confirm_token": token,
        "cart_items": cart_ctx["items"],
        "cart_total": cart_ctx["total"],
    })


@login_required
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
            .order_by("producto__nombre", "sku")[:20]
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
    """
    Modo scanner:
    - Si q matchea EXACTO (sku o codigo_barras) y es único -> agrega directo al carrito y devuelve _carrito.html
    - Si no es único / no hay match exacto -> devuelve lista de resultados (_resultados.html) pero retarget a #resultados
    """
    q = (request.POST.get("q") or "").strip()
    if not q:
        return HttpResponse("Código vacío", status=400)

    # 1) Match exacto por SKU o código de barras
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
        return _render_cart(request)

    # 2) Si no hubo match exacto único, caemos a búsqueda normal (lista)
    results = list(
        Variante.objects
        .select_related("producto")
        .filter(activo=True, producto__activo=True)
        .filter(
            Q(sku__icontains=q) |
            Q(codigo_barras__icontains=q) |
            Q(producto__nombre__icontains=q)
        )
        .order_by("producto__nombre", "sku")[:20]
    )

    # 3) Stock en la sucursal fija
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
        return _render_cart_with_toast(
            request,
            f"Stock insuficiente. Disponible: {stock} en {sucursal.nombre}."
        )

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

    qty_actual = int(cart.get(key, {}).get("qty", 0))
    if stock <= 0:
        # si no hay stock, lo sacamos del carrito
        del cart[key]
        _cart_save(request, cart)
        return _render_cart_with_toast(request, f"Sin stock en {sucursal.nombre}. Se quitó del carrito.")

    if qty > stock:
        cart[key]["qty"] = stock
        _cart_save(request, cart)
        return _render_cart_with_toast(
            request,
            f"Cantidad ajustada al stock disponible: {stock} en {sucursal.nombre}."
        )

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
    return _render_cart(request)


@login_required
@require_POST
def confirmar(request):
    # 1) Idempotencia anti doble click / doble submit
    sent_token = (request.POST.get("confirm_token") or "").strip()
    session_token = request.session.get("pos_confirm_token")

    if not session_token or sent_token != session_token:
        return HttpResponse("Operación ya procesada o token inválido.", status=409)

    # Rotamos token apenas entra (si reintenta, ya no pasa)
    request.session["pos_confirm_token"] = str(uuid.uuid4())
    request.session.modified = True

    sucursal = _get_pos_sucursal()  # <- SIEMPRE esta, nunca del POST
    medio_pago = request.POST.get("medio_pago") or Venta.MedioPago.EFECTIVO

    cart = _cart_get(request)
    if not cart:
        return HttpResponse("Carrito vacío", status=400)

    venta = Venta.objects.create(
        sucursal=sucursal,
        medio_pago=medio_pago,
        estado=Venta.Estado.BORRADOR,
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

    try:
        confirmar_venta(venta)
    except ValidationError as e:
        venta.estado = Venta.Estado.ANULADA
        venta.save()
        return HttpResponse(str(e), status=400)

    _cart_save(request, {})
    return render(request, "caja/_confirm_ok.html", {"venta": venta})


@login_required
def ticket(request, venta_id: int):
    venta = get_object_or_404(
        Venta.objects.select_related("sucursal").prefetch_related("items__variante__producto"),
        id=venta_id
    )
    return render(request, "caja/ticket.html", {"venta": venta})
