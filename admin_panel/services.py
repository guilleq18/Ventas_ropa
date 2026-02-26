from core.models import AppSetting


VENTAS_FLAGS_META = {
    "permitir_sin_stock": {
        "default": False,
        "label": "Permitir vender sin stock",
        "help_text": "Habilita confirmar ventas aunque la sucursal no tenga stock suficiente.",
        "description": "Permite confirmar venta aunque no haya stock suficiente.",
        "section": "operacion",
        "order": 10,
    },
    "permitir_cambiar_precio_venta": {
        "default": False,
        "label": "Permitir cambiar precio de venta",
        "help_text": "Permite editar el precio unitario desde el carrito del POS.",
        "description": "Permite cambiar el precio de venta en el POS.",
        "section": "operacion",
        "order": 20,
    },
}

def get_bool_setting(key: str, default: bool, description: str) -> bool:
    s, _ = AppSetting.objects.get_or_create(
        key=key,
        defaults={"value_bool": default, "description": description},
    )
    return bool(s.value_bool)


def get_str_setting(key: str, default: str, description: str) -> str:
    s, _ = AppSetting.objects.get_or_create(
        key=key,
        defaults={"value_str": default, "description": description},
    )
    return (s.value_str or default or "").strip()

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


def set_str_setting(key: str, value: str, default: str, description: str) -> None:
    s, _ = AppSetting.objects.get_or_create(
        key=key,
        defaults={"value_str": default, "description": description},
    )
    s.value_str = (value or "").strip()
    if not s.description:
        s.description = description
    s.save(update_fields=["value_str", "description"])

def _coerce_sucursal_id(sucursal=None):
    if sucursal is None:
        return None
    sid = getattr(sucursal, "id", sucursal)
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return None
    return sid if sid > 0 else None


def _ventas_sucursal_key(sucursal_id: int, flag_name: str) -> str:
    return f"ventas.sucursal.{int(sucursal_id)}.{flag_name}"


def _get_bool_setting_optional(key: str):
    value = (
        AppSetting.objects
        .filter(key=key)
        .values_list("value_bool", flat=True)
        .first()
    )
    if value is None:
        return None
    return bool(value)


def get_ventas_flags_catalog() -> list[dict]:
    rows = []
    for name, meta in VENTAS_FLAGS_META.items():
        rows.append({
            "name": name,
            "default": bool(meta.get("default", False)),
            "label": meta.get("label") or name.replace("_", " ").capitalize(),
            "help_text": meta.get("help_text") or meta.get("description") or "",
            "description": meta.get("description") or "",
            "section": meta.get("section") or "general",
            "order": int(meta.get("order") or 0),
        })
    rows.sort(key=lambda r: (r["section"], r["order"], r["label"]))
    return rows


def _get_ventas_flag(flag_name: str, sucursal=None) -> bool:
    meta = VENTAS_FLAGS_META[flag_name]
    sucursal_id = _coerce_sucursal_id(sucursal)

    if sucursal_id:
        override_key = _ventas_sucursal_key(sucursal_id, flag_name)
        override_value = _get_bool_setting_optional(override_key)
        if override_value is not None:
            return override_value

    return get_bool_setting(
        f"ventas.{flag_name}",
        meta["default"],
        meta["description"],
    )


def get_ventas_flags_ui(sucursal=None) -> list[dict]:
    sucursal_id = _coerce_sucursal_id(sucursal)
    rows = []
    for item in get_ventas_flags_catalog():
        source = "global"
        value = None
        if sucursal_id:
            override_key = _ventas_sucursal_key(sucursal_id, item["name"])
            override_val = _get_bool_setting_optional(override_key)
            if override_val is not None:
                value = override_val
                source = "sucursal"

        if value is None:
            value = get_bool_setting(
                f"ventas.{item['name']}",
                item["default"],
                item["description"],
            )

        rows.append({
            **item,
            "value": bool(value),
            "source": source,
        })
    return rows


def set_ventas_flags(*, sucursal, **flag_values) -> None:
    sucursal_id = _coerce_sucursal_id(sucursal)
    if not sucursal_id:
        raise ValueError("Sucursal inválida para configurar flags de ventas.")

    for item in get_ventas_flags_catalog():
        flag_name = item["name"]
        if flag_name not in flag_values:
            continue
        meta = VENTAS_FLAGS_META[flag_name]
        set_bool_setting(
            _ventas_sucursal_key(sucursal_id, flag_name),
            bool(flag_values[flag_name]),
            meta["default"],
            f"{meta['description']} (Sucursal #{sucursal_id})",
        )


def get_ventas_flags(sucursal=None) -> dict:
    permitir_sin_stock = _get_ventas_flag("permitir_sin_stock", sucursal=sucursal)
    permitir_cambiar_precio_venta = _get_ventas_flag(
        "permitir_cambiar_precio_venta",
        sucursal=sucursal,
    )
    return {
        "permitir_sin_stock": permitir_sin_stock,
        "permitir_cambiar_precio_venta": permitir_cambiar_precio_venta,
    }


def permitir_vender_sin_stock(sucursal=None) -> bool:
    return _get_ventas_flag("permitir_sin_stock", sucursal=sucursal)


def permitir_cambiar_precio_venta(sucursal=None) -> bool:
    return _get_ventas_flag("permitir_cambiar_precio_venta", sucursal=sucursal)
