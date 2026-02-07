import re
import unicodedata
from itertools import product as cartesian_product

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from core.models import Sucursal

from .forms import GeneradorVariantesForm, ProductoForm, StockSucursalForm, VarianteForm
from .models import (
    Atributo,
    AtributoValor,
    Producto,
    StockSucursal,
    Variante,
    VarianteAtributo,
)


# ----------------------------
# HELPERS GENERALES
# ----------------------------

def _is_htmx(request) -> bool:
    """Devuelve True si la request viene desde HTMX."""
    return request.headers.get("HX-Request") == "true"


def _get_or_create_atributo(nombre: str) -> Atributo:
    """Obtiene o crea un Atributo (ej: Talle, Color)."""
    obj, _ = Atributo.objects.get_or_create(nombre=nombre, defaults={"activo": True})
    return obj


def _get_or_create_valor(atributo: Atributo, valor: str) -> AtributoValor:
    """Obtiene o crea un AtributoValor (ej: Talle=M)."""
    obj, _ = AtributoValor.objects.get_or_create(
        atributo=atributo,
        valor=valor,
        defaults={"activo": True},
    )
    return obj


def _extraer_talle_color(variante) -> tuple[str, str]:
    """Extrae (talle, color) desde VarianteAtributo de una variante."""
    talle = ""
    color = ""
    for va in variante.atributos.select_related("atributo", "valor").all():
        if va.atributo.nombre == "Talle":
            talle = va.valor.valor
        elif va.atributo.nombre == "Color":
            color = va.valor.valor
    return talle, color


def _normalize_key(s: str) -> str:
    """Normaliza valores de texto para comparar claves (talle/color)."""
    return (s or "").strip()


def _existe_combinacion_producto(
    producto_id: int,
    talle: str,
    color: str,
    exclude_variante_id: int | None = None
) -> bool:
    """
    Devuelve True si ya existe una variante del producto con (Talle, Color).
    exclude_variante_id: para editar (ignorar la propia variante).
    """
    talle = _normalize_key(talle)
    color = _normalize_key(color)

    qs = (
        Variante.objects
        .filter(producto_id=producto_id)
        .prefetch_related("atributos__atributo", "atributos__valor")
    )
    if exclude_variante_id:
        qs = qs.exclude(id=exclude_variante_id)

    for v in qs:
        t, c = _extraer_talle_color(v)
        if _normalize_key(t) == talle and _normalize_key(c) == color:
            return True

    return False


def _sku_clean(s: str) -> str:
    """Normaliza texto para SKU: mayúsculas, sin acentos, sin espacios ni símbolos."""
    s = (s or "").strip().upper()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def _sku_generado(nombre_producto: str, color: str, talle: str) -> str:
    """Genera SKU: 4 letras producto - 3 letras color - talle."""
    p = _sku_clean(nombre_producto)[:4] or "PROD"
    c = _sku_clean(color)[:3] or "SIN"
    t = _sku_clean(talle) or "U"
    return f"{p}-{c}-{t}"


# ----------------------------
# HELPERS DE RENDER (sin decorators)
# ----------------------------

def _render_productos_lista(request) -> HttpResponse:
    """Renderiza el parcial de la lista de productos (respeta ?q= si viene)."""
    q = (request.GET.get("q") or "").strip()

    qs = Producto.objects.select_related("categoria").order_by("-created_at")
    if q:
        qs = qs.filter(nombre__icontains=q)

    html = render_to_string(
        "catalogo/_productos_lista.html",
        {"productos": qs[:100]},
        request=request
    )
    return HttpResponse(html)


def _render_variantes_panel(request, producto_id: int) -> HttpResponse:
    """Renderiza el panel de variantes de un producto + stock total por variante."""
    producto = get_object_or_404(Producto.objects.select_related("categoria"), pk=producto_id)

    variantes_qs = (
        Variante.objects
        .filter(producto=producto)
        .prefetch_related("atributos__atributo", "atributos__valor")
        .order_by("-created_at")[:200]
    )

    variantes = list(variantes_qs)
    variante_ids = [v.id for v in variantes]

    totals = {}
    if variante_ids:
        rows = (
            StockSucursal.objects
            .filter(variante_id__in=variante_ids)
            .values("variante_id")
            .annotate(total=Sum("cantidad"))
        )
        totals = {r["variante_id"]: int(r["total"] or 0) for r in rows}

    items = []
    for v in variantes:
        talle, color = _extraer_talle_color(v)
        items.append({
            "v": v,
            "talle": talle or "-",
            "color": color or "-",
            "stock_total": totals.get(v.id, 0),
        })

    html = render_to_string(
        "catalogo/_variantes_panel.html",
        {"producto": producto, "variantes": items},
        request=request
    )
    return HttpResponse(html)


# ----------------------------
# PANTALLA PRINCIPAL
# ----------------------------

@login_required
@require_http_methods(["GET"])
def productos(request):
    """Pantalla principal de catálogo (productos a la izquierda, panel variantes a la derecha)."""
    productos = Producto.objects.select_related("categoria").order_by("-created_at")[:100]
    return render(request, "catalogo/productos.html", {"productos": productos})


@login_required
@require_http_methods(["GET"])
def productos_buscar(request):
    """HTMX: devuelve lista filtrada de productos (parcial)."""
    return _render_productos_lista(request)


@login_required
@require_http_methods(["GET"])
def variantes_panel(request, producto_id: int):
    """HTMX: devuelve panel de variantes del producto (parcial)."""
    return _render_variantes_panel(request, producto_id)


# ----------------------------
# PRODUCTO (nuevo/editar/toggle)
# ----------------------------

@login_required
@require_http_methods(["GET", "POST"])
def producto_nuevo(request):
    """Modal: crear producto. Al guardar, refresca lista y cierra modal."""
    form = ProductoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        resp = _render_productos_lista(request)
        resp.headers["HX-Trigger"] = "closeModal"
        return resp
    return render(request, "catalogo/_producto_form.html", {"form": form, "modo": "nuevo"})


@login_required
@require_http_methods(["GET", "POST"])
def producto_editar(request, pk: int):
    """Modal: editar producto. Al guardar, refresca lista y cierra modal."""
    producto = get_object_or_404(Producto, pk=pk)
    form = ProductoForm(request.POST or None, instance=producto)
    if request.method == "POST" and form.is_valid():
        form.save()
        resp = _render_productos_lista(request)
        resp.headers["HX-Trigger"] = "closeModal"
        return resp
    return render(
        request,
        "catalogo/_producto_form.html",
        {"form": form, "modo": "editar", "producto": producto},
    )


@login_required
@require_http_methods(["POST"])
def producto_toggle(request, pk: int):
    """HTMX: activa/desactiva producto y refresca lista."""
    producto = get_object_or_404(Producto, pk=pk)
    producto.activo = not producto.activo
    producto.save(update_fields=["activo"])
    return _render_productos_lista(request)


# ----------------------------
# VARIANTE (nuevo/editar/eliminar)
# ----------------------------

@login_required
@require_http_methods(["GET", "POST"])
def variante_nueva(request, producto_id: int):
    """Modal: crear variante manual (SKU/EAN/precio/costo + talle/color)."""
    producto = get_object_or_404(Producto, pk=producto_id)
    form = VarianteForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        talle = (form.cleaned_data.get("talle") or "").strip()
        color = (form.cleaned_data.get("color") or "").strip()
        


        # ✅ obligatorios
        if not talle or not color:
            return render(
                request,
                "catalogo/_variante_form.html",
                {
                    "form": form,
                    "modo": "nuevo",
                    "producto": producto,
                    "error_msg": "Talle y Color son obligatorios para poder manejar stock por combinación.",
                },
                status=400,
            )

        # ✅ no duplicados por combinación
        if _existe_combinacion_producto(producto.id, talle, color):
            return render(
                request,
                "catalogo/_variante_form.html",
                {
                    "form": form,
                    "modo": "nuevo",
                    "producto": producto,
                    "error_msg": f"Ya existe una variante para este producto con Talle={talle} y Color={color}.",
                },
                status=400,
            )

        v = form.save(commit=False)
        v.producto = producto
        v.save()

        attr_talle = _get_or_create_atributo("Talle")
        attr_color = _get_or_create_atributo("Color")

        with transaction.atomic():
            val_t = _get_or_create_valor(attr_talle, talle)
            VarianteAtributo.objects.update_or_create(
                variante=v, atributo=attr_talle, defaults={"valor": val_t}
            )

            val_c = _get_or_create_valor(attr_color, color)
            VarianteAtributo.objects.update_or_create(
                variante=v, atributo=attr_color, defaults={"valor": val_c}
            )

        resp = _render_variantes_panel(request, producto.id)
        resp.headers["HX-Trigger"] = "closeModal"
        return resp

    return render(
        request,
        "catalogo/_variante_form.html",
        {"form": form, "modo": "nuevo", "producto": producto},
    )


@login_required
@require_http_methods(["GET", "POST"])
def variante_editar(request, pk: int):
    """Modal: editar variante (incluye talle/color) y refresca panel."""
    v = get_object_or_404(
        Variante.objects.select_related("producto").prefetch_related("atributos__atributo", "atributos__valor"),
        pk=pk
    )
    form = VarianteForm(request.POST or None, instance=v)

    if request.method == "GET":
        talle, color = _extraer_talle_color(v)
        form.initial["talle"] = talle
        form.initial["color"] = color

    if request.method == "POST" and form.is_valid():
        talle = (form.cleaned_data.get("talle") or "").strip()
        color = (form.cleaned_data.get("color") or "").strip()

        # ✅ obligatorios
        if not talle or not color:
            return render(
                request,
                "catalogo/_variante_form.html",
                {
                    "form": form,
                    "modo": "editar",
                    "variante": v,
                    "error_msg": "Talle y Color son obligatorios para poder manejar stock por combinación.",
                },
                status=400,
            )

        # ✅ no duplicados por combinación (excluye la propia variante)
        if _existe_combinacion_producto(v.producto_id, talle, color, exclude_variante_id=v.id):
            return render(
                request,
                "catalogo/_variante_form.html",
                {
                    "form": form,
                    "modo": "editar",
                    "variante": v,
                    "error_msg": f"Ya existe otra variante con Talle={talle} y Color={color}.",
                },
                status=400,
            )

        v = form.save()

        attr_talle = _get_or_create_atributo("Talle")
        attr_color = _get_or_create_atributo("Color")

        with transaction.atomic():
            val_t = _get_or_create_valor(attr_talle, talle)
            VarianteAtributo.objects.update_or_create(
                variante=v, atributo=attr_talle, defaults={"valor": val_t}
            )

            val_c = _get_or_create_valor(attr_color, color)
            VarianteAtributo.objects.update_or_create(
                variante=v, atributo=attr_color, defaults={"valor": val_c}
            )

        resp = _render_variantes_panel(request, v.producto_id)
        resp.headers["HX-Trigger"] = "closeModal"
        return resp

    return render(request, "catalogo/_variante_form.html", {"form": form, "modo": "editar", "variante": v})


@login_required
@require_http_methods(["POST"])
def variante_eliminar(request, pk: int):
    """HTMX: elimina variante y refresca panel de variantes (cierra modal si estaba abierto)."""
    v = get_object_or_404(Variante, pk=pk)
    producto_id = v.producto_id
    v.delete()

    resp = _render_variantes_panel(request, producto_id)
    resp.headers["HX-Trigger"] = "closeModal"
    return resp


# ----------------------------
# GENERADOR DE VARIANTES (talles x colores)
# ----------------------------

@login_required
@require_http_methods(["GET", "POST"])
def variantes_generador(request, producto_id: int):
    """
    Modal: genera variantes por combinaciones (talles x colores).
    SKU automático: 4 letras producto - 3 letras color - talle.
    - Cada variante SIEMPRE tiene 1 talle + 1 color.
    - No se generan combinaciones duplicadas para el producto.
    """
    producto = get_object_or_404(Producto, pk=producto_id)
    form = GeneradorVariantesForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        talles_raw = (form.cleaned_data.get("talles") or "").strip()
        colores_raw = (form.cleaned_data.get("colores") or "").strip()
        precio = form.cleaned_data["precio"]
        costo = form.cleaned_data["costo"]
        codigo_barras_base = (form.cleaned_data.get("codigo_barras_base") or "").strip()

        
        activo = form.cleaned_data.get("activo", True)
        

        talles = [t.strip() for t in talles_raw.split(",") if t.strip()]
        colores = [c.strip() for c in colores_raw.split(",") if c.strip()]

        if not talles or not colores:
            return HttpResponse("Debe cargar talles y colores", status=400)

        attr_talle = _get_or_create_atributo("Talle")
        attr_color = _get_or_create_atributo("Color")

        existentes_sku = set(Variante.objects.values_list("sku", flat=True))

        # combinaciones ya existentes en el producto
        existentes_combo = set()
        for vv in (
            Variante.objects
            .filter(producto=producto)
            .prefetch_related("atributos__atributo", "atributos__valor")
        ):
            t0, c0 = _extraer_talle_color(vv)
            if t0 and c0:
                existentes_combo.add((t0.strip(), c0.strip()))

        relaciones = []

        with transaction.atomic():
            for talle, color in cartesian_product(talles, colores):
                combo = (talle.strip(), color.strip())
                if combo in existentes_combo:
                    continue

                sku_base = _sku_generado(producto.nombre, color, talle)
                sku = sku_base
                i = 2
                while sku in existentes_sku:
                    sku = f"{sku_base}-{i}"
                    i += 1

                v = Variante.objects.create(
                    producto=producto,
                    sku=sku,
                    codigo_barras=codigo_barras_base,  # ✅ mismo EAN para todas
                    precio=precio,
                    costo=costo,
                    activo=activo,
                )


                existentes_sku.add(sku)
                existentes_combo.add(combo)

                val_t = _get_or_create_valor(attr_talle, talle)
                relaciones.append(VarianteAtributo(variante=v, atributo=attr_talle, valor=val_t))

                val_c = _get_or_create_valor(attr_color, color)
                relaciones.append(VarianteAtributo(variante=v, atributo=attr_color, valor=val_c))

            if relaciones:
                VarianteAtributo.objects.bulk_create(relaciones)

        resp = _render_variantes_panel(request, producto.id)
        resp.headers["HX-Trigger"] = "closeModal"
        return resp

    return render(request, "catalogo/_generador_form.html", {"form": form, "producto": producto})


# ----------------------------
# STOCK POR SUCURSAL (modal por variante)
# ----------------------------

@login_required
@require_http_methods(["GET"])
def variante_stock_detalle(request, variante_id: int):
    """Modal: muestra stock por sucursal de una variante."""
    variante = get_object_or_404(Variante.objects.select_related("producto"), pk=variante_id)

    sucursales = Sucursal.objects.filter(activa=True).order_by("nombre")
    stocks = StockSucursal.objects.filter(variante=variante).select_related("sucursal")
    stock_map = {s.sucursal_id: s.cantidad for s in stocks}

    rows = []
    total = 0
    for s in sucursales:
        qty = int(stock_map.get(s.id, 0))
        total += qty
        rows.append({"sucursal": s, "cantidad": qty})

    return render(
        request,
        "catalogo/_stock_detalle_variante.html",
        {"variante": variante, "rows": rows, "total": total},
    )


@login_required
@require_http_methods(["POST"])
def variante_stock_set(request):
    """HTMX: setea stock (variante + sucursal) y refresca panel para actualizar stock total."""
    variante_id = request.POST.get("variante_id")
    sucursal_id = request.POST.get("sucursal_id")
    cantidad = request.POST.get("cantidad")

    if not variante_id or not sucursal_id:
        return HttpResponse("Faltan datos", status=400)

    try:
        qty = int(cantidad) if cantidad is not None and str(cantidad).strip() != "" else 0
    except ValueError:
        return HttpResponse("Cantidad inválida", status=400)

    if qty < 0:
        return HttpResponse("Cantidad inválida", status=400)

    variante = get_object_or_404(Variante, pk=variante_id)
    sucursal = get_object_or_404(Sucursal, pk=sucursal_id, activa=True)

    StockSucursal.objects.update_or_create(
        variante=variante,
        sucursal=sucursal,
        defaults={"cantidad": qty},
    )

    resp = _render_variantes_panel(request, variante.producto_id)
    resp.headers["HX-Trigger"] = "stockUpdated"
    return resp


# ----------------------------
# (LEGADO / OPCIONAL) PLANILLA DE STOCK POR PRODUCTO
# ----------------------------

@login_required
@require_http_methods(["GET"])
def stock_planilla(request, producto_id: int):
    """(Opcional) Planilla color x talle por sucursal para un producto."""
    producto = get_object_or_404(Producto, pk=producto_id)

    sucursal_id = request.GET.get("sucursal_id")
    sucursales = Sucursal.objects.filter(activa=True).order_by("nombre")

    if sucursal_id:
        sucursal_sel = get_object_or_404(sucursales, pk=sucursal_id)
    else:
        sucursal_sel = sucursales.first()

    variantes = (
        Variante.objects
        .filter(producto=producto)
        .prefetch_related("atributos__atributo", "atributos__valor")
        .order_by("sku")
    )

    talles_set = set()
    colores_set = set()
    combo_to_var = {}

    for v in variantes:
        talle, color = _extraer_talle_color(v)
        talle = talle or "-"
        color = color or "-"
        talles_set.add(talle)
        colores_set.add(color)
        combo_to_var[(talle, color)] = v

    talles = sorted(talles_set)
    colores = sorted(colores_set)

    stock_map = {}
    if sucursal_sel:
        stocks = (
            StockSucursal.objects
            .filter(sucursal=sucursal_sel, variante__producto=producto)
            .select_related("variante")
        )
        stock_map = {s.variante_id: s.cantidad for s in stocks}

    rows = []
    for t in talles:
        cells = []
        for c in colores:
            v = combo_to_var.get((t, c))
            cells.append({
                "talle": t,
                "color": c,
                "variante": v,
                "cantidad": stock_map.get(v.id, 0) if v else None,
            })
        rows.append({"talle": t, "cells": cells})

    ctx = {
        "producto": producto,
        "sucursales": sucursales,
        "sucursal_sel": sucursal_sel,
        "colores": colores,
        "rows": rows,
    }
    return render(request, "catalogo/_stock_planilla.html", ctx)


@login_required
@require_http_methods(["POST"])
def stock_set(request):
    """(Opcional) Setea stock desde la planilla (variante + sucursal)."""
    sucursal_id = request.POST.get("sucursal_id")
    variante_id = request.POST.get("variante_id")
    cantidad = request.POST.get("cantidad")

    if not (sucursal_id and variante_id):
        return HttpResponse("Faltan datos", status=400)

    try:
        cantidad_int = int(cantidad) if cantidad is not None and str(cantidad).strip() != "" else 0
    except ValueError:
        return HttpResponse("Cantidad inválida", status=400)

    sucursal = get_object_or_404(Sucursal, pk=sucursal_id, activa=True)
    variante = get_object_or_404(Variante, pk=variante_id)

    obj, _ = StockSucursal.objects.update_or_create(
        sucursal=sucursal,
        variante=variante,
        defaults={"cantidad": cantidad_int},
    )

    return HttpResponse(f"{obj.cantidad}")


@login_required
@require_http_methods(["GET", "POST"])
def stock_modal(request, variante_id: int):
    """(Legado) Modal de stock con select sucursal + cantidad (se puede dejar como respaldo)."""
    variante = get_object_or_404(Variante.objects.select_related("producto"), pk=variante_id)
    form = StockSucursalForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        StockSucursal.objects.update_or_create(
            variante=variante,
            sucursal=form.cleaned_data["sucursal"],
            defaults={"cantidad": form.cleaned_data["cantidad"]},
        )
        resp = _render_variantes_panel(request, variante.producto_id)
        resp.headers["HX-Trigger"] = "closeModal"
        return resp

    return render(request, "catalogo/_stock_form.html", {"form": form, "variante": variante})
