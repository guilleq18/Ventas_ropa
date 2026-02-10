from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()

def _to_decimal(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None

@register.filter(name="moneda_ar")
def moneda_ar(value, simbolo="$"):
    dec = _to_decimal(value)
    if dec is None:
        return ""
    dec = dec.quantize(Decimal("0.01"))

    s = f"{dec:,.2f}"                    # 12,345.67
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")  # 12.345,67

    simbolo = "" if simbolo is None else str(simbolo)
    return s if simbolo == "" else f"{simbolo} {s}"
