from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from core.models import AppSetting


MONEY_QUANT = Decimal("0.01")
ZERO = Decimal("0.00")
ONE = Decimal("1")
HUNDRED = Decimal("100")
IVA_GENERAL_PCT = Decimal("21.00")


class CondicionFiscalEmpresa:
    """
    Valores canonicos para configurar la condicion fiscal de la empresa.
    """

    RESPONSABLE_INSCRIPTO = "RESPONSABLE_INSCRIPTO"
    MONOTRIBUTISTA = "MONOTRIBUTISTA"

    DEFAULT = MONOTRIBUTISTA
    SETTING_KEY = "empresa.condicion_fiscal"
    SETTING_DESCRIPTION = (
        "Condicion fiscal de la empresa (Responsable Inscripto / Monotributista) "
        "para reglas de precios, POS y ticket."
    )

    CHOICES = (
        (RESPONSABLE_INSCRIPTO, "Responsable Inscripto"),
        (MONOTRIBUTISTA, "Monotributista"),
    )


@dataclass(frozen=True)
class DesgloseFiscalMonto:
    """
    Desglose fiscal de un monto final gravado con IVA.

    Sirve tanto para un precio de producto como para un subtotal/total de items.
    La idea para futuras promociones/descuentos es pasar aca el monto final ya
    ajustado (por ejemplo, subtotal de items con promo aplicada).
    """

    monto_final: Decimal
    monto_sin_impuestos_nacionales: Decimal
    iva_contenido: Decimal
    iva_alicuota_pct: Decimal
    otros_impuestos_nacionales_indirectos: Decimal = ZERO

    @property
    def impuestos_nacionales_totales(self) -> Decimal:
        return money(
            self.iva_contenido + (self.otros_impuestos_nacionales_indirectos or ZERO)
        )


@dataclass(frozen=True)
class ResumenFiscalMontos:
    monto_final: Decimal
    monto_sin_impuestos_nacionales: Decimal
    iva_contenido: Decimal
    otros_impuestos_nacionales_indirectos: Decimal = ZERO

    @property
    def impuestos_nacionales_totales(self) -> Decimal:
        return money(
            self.iva_contenido + (self.otros_impuestos_nacionales_indirectos or ZERO)
        )


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Valor decimal invalido: {value!r}") from exc


def money(value) -> Decimal:
    return _to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def normalizar_condicion_fiscal_empresa(
    value: str | None,
    *,
    default: str = CondicionFiscalEmpresa.DEFAULT,
) -> str:
    raw = (value or "").strip()
    compact = (
        raw.upper()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )

    if compact in {"RI", "RESPONSABLEINSCRIPTO"}:
        return CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO
    if compact in {"MONOTRIBUTO", "MONOTRIBUTISTA"}:
        return CondicionFiscalEmpresa.MONOTRIBUTISTA

    if default and default != value:
        fallback = normalizar_condicion_fiscal_empresa(default, default="")
        if fallback:
            return fallback

    return CondicionFiscalEmpresa.DEFAULT


def get_empresa_condicion_fiscal(
    *,
    default: str = CondicionFiscalEmpresa.DEFAULT,
) -> str:
    default_norm = normalizar_condicion_fiscal_empresa(default)
    setting, _ = AppSetting.objects.get_or_create(
        key=CondicionFiscalEmpresa.SETTING_KEY,
        defaults={
            "value_str": default_norm,
            "description": CondicionFiscalEmpresa.SETTING_DESCRIPTION,
        },
    )

    current_norm = normalizar_condicion_fiscal_empresa(setting.value_str, default=default_norm)
    update_fields = []
    if (setting.value_str or "").strip() != current_norm:
        setting.value_str = current_norm
        update_fields.append("value_str")
    if not (setting.description or "").strip():
        setting.description = CondicionFiscalEmpresa.SETTING_DESCRIPTION
        update_fields.append("description")
    if update_fields:
        setting.save(update_fields=update_fields)

    return current_norm


def set_empresa_condicion_fiscal(condicion: str) -> str:
    condicion_norm = normalizar_condicion_fiscal_empresa(condicion)
    setting, _ = AppSetting.objects.get_or_create(
        key=CondicionFiscalEmpresa.SETTING_KEY,
        defaults={
            "value_str": condicion_norm,
            "description": CondicionFiscalEmpresa.SETTING_DESCRIPTION,
        },
    )
    setting.value_str = condicion_norm
    if not (setting.description or "").strip():
        setting.description = CondicionFiscalEmpresa.SETTING_DESCRIPTION
        setting.save(update_fields=["value_str", "description"])
    else:
        setting.save(update_fields=["value_str"])
    return condicion_norm


def empresa_es_responsable_inscripto() -> bool:
    return get_empresa_condicion_fiscal() == CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO


def empresa_es_monotributista() -> bool:
    return get_empresa_condicion_fiscal() == CondicionFiscalEmpresa.MONOTRIBUTISTA


def desglosar_monto_final_gravado_con_iva(
    monto_final,
    *,
    iva_alicuota_pct=IVA_GENERAL_PCT,
) -> DesgloseFiscalMonto:
    """
    Desglosa un monto final con IVA incluido.

    Uso actual:
    - Precio final de un producto
    - Subtotal de linea
    - Total de items de una venta

    Uso futuro (promociones):
    - Pasar el monto final de items ya descontado por la promo
      (los recargos/ajustes se podran modelar como componentes separados).
    """
    total = money(monto_final)
    alicuota = _to_decimal(iva_alicuota_pct).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    if total < ZERO:
        raise ValueError("El monto final no puede ser negativo.")
    if alicuota < ZERO:
        raise ValueError("La alicuota de IVA no puede ser negativa.")

    if alicuota == ZERO or total == ZERO:
        return DesgloseFiscalMonto(
            monto_final=total,
            monto_sin_impuestos_nacionales=total,
            iva_contenido=ZERO,
            iva_alicuota_pct=alicuota,
            otros_impuestos_nacionales_indirectos=ZERO,
        )

    factor = ONE + (alicuota / HUNDRED)
    neto = (total / factor).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    iva = (total - neto).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    return DesgloseFiscalMonto(
        monto_final=total,
        monto_sin_impuestos_nacionales=neto,
        iva_contenido=iva,
        iva_alicuota_pct=alicuota,
        otros_impuestos_nacionales_indirectos=ZERO,
    )


def sumar_desgloses_fiscales(desgloses: Iterable[DesgloseFiscalMonto]) -> ResumenFiscalMontos:
    total_final = ZERO
    total_sin_impuestos = ZERO
    total_iva = ZERO
    total_otros = ZERO

    for d in desgloses:
        total_final += money(d.monto_final)
        total_sin_impuestos += money(d.monto_sin_impuestos_nacionales)
        total_iva += money(d.iva_contenido)
        total_otros += money(getattr(d, "otros_impuestos_nacionales_indirectos", ZERO) or ZERO)

    return ResumenFiscalMontos(
        monto_final=money(total_final),
        monto_sin_impuestos_nacionales=money(total_sin_impuestos),
        iva_contenido=money(total_iva),
        otros_impuestos_nacionales_indirectos=money(total_otros),
    )

