from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Sucursal
from catalogo.models import Variante, VarianteAtributo
from ventas.models import Venta, VentaItem
from ventas.services import confirmar_venta
from django.core.exceptions import ValidationError
from django.db.models import Q




def _cart_get(request) -> dict:
    return request.session.get("pos_cart", {})  # { "variante_id": {"qty": 2, "precio": "12000.00"} }


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

    ctx = {
        "items": rows,
        "total": _cart_total(cart),
    }
    return render(request, "caja/_carrito.html", ctx)


@login_required
def pos(request):
    sucursales = Sucursal.objects.filter(activa=True).order_by("nombre")
    return render(request, "caja/pos.html", {"sucursales": sucursales})


@login_required
def buscar_variantes(request):
    q = (request.GET.get("q") or "").strip()
    results = []
    if q:
        qs = (
            Variante.objects
            .select_related("producto")
            .filter(activo=True)
            .filter(
                Q(sku__icontains=q) |
                Q(codigo_barras__icontains=q) |
                Q(producto__nombre__icontains=q)
            )
            .order_by("producto__nombre", "sku")[:20]
        )
        results = list(qs)

    return render(request, "caja/_resultados.html", {"results": results})


@login_required
@require_POST
def scan_add(request):
    q = (request.POST.get("q") or "").strip()
    if not q:
        return HttpResponse("Código vacío", status=400)

    # 1) Intento exacto (rápido y el más común con scanner)
    exact = (
        Variante.objects
        .select_related("producto")
        .filter(activo=True)
        .filter(Q(sku=q) | Q(codigo_barras=q))
    )

    if exact.count() == 1:
        v = exact.first()
        cart = _cart_get(request)
        key = str(v.id)

        if key not in cart:
            cart[key] = {"qty": 1, "precio": str(v.precio)}
        else:
            cart[key]["qty"] = int(cart[key]["qty"]) + 1

        _cart_save(request, cart)
        return _render_cart(request)

    # 2) Si no hubo match exacto único, devolvemos resultados para elegir
    results = list(
        Variante.objects
        .select_related("producto")
        .filter(activo=True)
        .filter(
            Q(sku__icontains=q) |
            Q(codigo_barras__icontains=q) |
            Q(producto__nombre__icontains=q)
        )
        .order_by("producto__nombre", "sku")[:20]
    )
    return render(request, "caja/_resultados.html", {"results": results})



@login_required
@require_POST
def carrito_agregar(request, variante_id: int):
    v = get_object_or_404(Variante, id=variante_id, activo=True)
    cart = _cart_get(request)
    key = str(v.id)

    if key not in cart:
        cart[key] = {"qty": 1, "precio": str(v.precio)}
    else:
        cart[key]["qty"] = int(cart[key]["qty"]) + 1

    _cart_save(request, cart)
    return _render_cart(request)


@login_required
@require_POST
def carrito_set_qty(request, variante_id: int):
    qty = int(request.POST.get("qty") or 1)
    if qty < 1:
        qty = 1

    cart = _cart_get(request)
    key = str(variante_id)
    if key in cart:
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
def confirmar(request):
    sucursal_id = request.POST.get("sucursal_id")
    medio_pago = request.POST.get("medio_pago") or Venta.MedioPago.EFECTIVO

    cart = _cart_get(request)
    if not cart:
        return HttpResponse("Carrito vacío", status=400)

    sucursal = get_object_or_404(Sucursal, id=sucursal_id, activa=True)

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
        confirmar_venta(venta)  # descuenta stock y pasa a CONFIRMADA
    except ValidationError as e:
        venta.estado = Venta.Estado.ANULADA
        venta.save()
        return HttpResponse(str(e), status=400)

    # limpiar carrito
    _cart_save(request, {})
    return render(request, "caja/_confirm_ok.html", {"venta": venta})
