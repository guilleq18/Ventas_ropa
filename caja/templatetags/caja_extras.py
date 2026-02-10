# caja/templatetags/caja_extras.py
# Comentarios en español como pediste.

from django import template
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

register = template.Library()


@register.filter(name="num_ar")
def num_ar(value, decimals=2):
    """
    Formatea números estilo Argentina:
      miles con punto y decimales con coma
      12345.6 -> 12.345,60

    Uso en template:
      {{ total|num_ar }}
      {{ total|num_ar:0 }}
    """
    try:
        decimals = int(decimals)
    except Exception:
        decimals = 2

    if value is None or value == "":
        return ""

    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value

    # Redondeo fijo
    q = Decimal("1") if decimals <= 0 else Decimal("1." + ("0" * decimals))
    n = n.quantize(q, rounding=ROUND_HALF_UP)

    # Formato US: miles con coma, decimales con punto
    s = f"{n:,.{max(decimals,0)}f}"

    # Pasar a AR: miles con punto, decimales con coma
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@register.filter
def nombre_cliente(variante):
    """
    Devuelve: Producto - Color - Talle (si existen).
    Requiere prefetch de atributos para evitar N+1.
    """
    if not variante:
        return ""

    try:
        base = (variante.producto.nombre or "").strip()
    except Exception:
        base = ""

    color = ""
    talle = ""

    try:
        for va in variante.atributos.all():  # related_name="atributos"
            nom = (va.atributo.nombre or "").strip().lower()
            val = (va.valor.valor or "").strip()
            if not val:
                continue

            if nom == "color":
                color = val
            elif nom in ("talle", "tamaño", "tamanio", "size"):
                talle = val
    except Exception:
        pass

    partes = [p for p in (base, color, talle) if p]
    return " - ".join(partes) if partes else ((getattr(variante, "sku", "") or base or "Producto").strip())


@register.filter
def get_item(d, key):
    if d is None:
        return None
    try:
        return d.get(key)
    except Exception:
        return None
