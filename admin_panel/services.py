from core.models import AppSetting

def get_bool_setting(key: str, default: bool, description: str) -> bool:
    s, _ = AppSetting.objects.get_or_create(
        key=key,
        defaults={"value_bool": default, "description": description},
    )
    return bool(s.value_bool)

def set_bool_setting(key: str, value: bool, default: bool, description: str) -> None:
    s, _ = AppSetting.objects.get_or_create(
        key=key,
        defaults={"value_bool": default, "description": description},
    )
    s.value_bool = bool(value)
    # opcional: si description puede cambiar
    if not s.description:
        s.description = description
    s.save(update_fields=["value_bool", "description"])

def get_ventas_flags() -> dict:
    permitir_sin_stock = get_bool_setting(
        "ventas.permitir_sin_stock",
        False,
        "Permite confirmar venta aunque no haya stock suficiente."
    )
    permitir_cambiar_precio_venta = get_bool_setting(
        "ventas.permitir_cambiar_precio_venta",
        False,
        "Permite cambiar el precio de venta en el POS."
    )
    return {
        "permitir_sin_stock": permitir_sin_stock,
        "permitir_cambiar_precio_venta": permitir_cambiar_precio_venta,
    }
def permitir_vender_sin_stock() -> bool:
    return get_bool_setting(
        "ventas.permitir_sin_stock",
        False,
        "Permite confirmar venta aunque no haya stock suficiente."
    )

def permitir_cambiar_precio_venta() -> bool:
    return get_bool_setting(
        "ventas.permitir_cambiar_precio_venta",
        False,
        "Permite cambiar el precio de venta en el POS."
    )
