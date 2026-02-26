from decimal import Decimal

from django.test import TestCase

from core.fiscal import (
    CondicionFiscalEmpresa,
    desglosar_monto_final_gravado_con_iva,
    empresa_es_responsable_inscripto,
    get_empresa_condicion_fiscal,
    normalizar_condicion_fiscal_empresa,
    set_empresa_condicion_fiscal,
    sumar_desgloses_fiscales,
)
from core.models import AppSetting


class FiscalHelpersTests(TestCase):
    def test_desglosa_precio_final_con_iva_21(self):
        d = desglosar_monto_final_gravado_con_iva("121.00")
        self.assertEqual(d.monto_final, Decimal("121.00"))
        self.assertEqual(d.monto_sin_impuestos_nacionales, Decimal("100.00"))
        self.assertEqual(d.iva_contenido, Decimal("21.00"))
        self.assertEqual(d.impuestos_nacionales_totales, Decimal("21.00"))

    def test_redondeo_mantiene_suma_exacta(self):
        d = desglosar_monto_final_gravado_con_iva("1000.00")
        self.assertEqual(d.monto_sin_impuestos_nacionales, Decimal("826.45"))
        self.assertEqual(d.iva_contenido, Decimal("173.55"))
        self.assertEqual(
            d.monto_sin_impuestos_nacionales + d.iva_contenido + d.otros_impuestos_nacionales_indirectos,
            d.monto_final,
        )

    def test_sumar_desgloses(self):
        d1 = desglosar_monto_final_gravado_con_iva("121.00")
        d2 = desglosar_monto_final_gravado_con_iva("242.00")
        total = sumar_desgloses_fiscales([d1, d2])

        self.assertEqual(total.monto_final, Decimal("363.00"))
        self.assertEqual(total.monto_sin_impuestos_nacionales, Decimal("300.00"))
        self.assertEqual(total.iva_contenido, Decimal("63.00"))
        self.assertEqual(total.impuestos_nacionales_totales, Decimal("63.00"))

    def test_normaliza_condicion_fiscal_aliases(self):
        self.assertEqual(
            normalizar_condicion_fiscal_empresa("ri"),
            CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO,
        )
        self.assertEqual(
            normalizar_condicion_fiscal_empresa("Responsable Inscripto"),
            CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO,
        )
        self.assertEqual(
            normalizar_condicion_fiscal_empresa("monotributo"),
            CondicionFiscalEmpresa.MONOTRIBUTISTA,
        )
        self.assertEqual(
            normalizar_condicion_fiscal_empresa("monotributista"),
            CondicionFiscalEmpresa.MONOTRIBUTISTA,
        )

    def test_get_y_set_condicion_fiscal_empresa_con_appsetting(self):
        default_value = get_empresa_condicion_fiscal()
        self.assertEqual(default_value, CondicionFiscalEmpresa.DEFAULT)

        setting = AppSetting.objects.get(key=CondicionFiscalEmpresa.SETTING_KEY)
        self.assertEqual(setting.value_str, CondicionFiscalEmpresa.DEFAULT)

        saved = set_empresa_condicion_fiscal("RI")
        self.assertEqual(saved, CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO)
        self.assertTrue(empresa_es_responsable_inscripto())

        setting.refresh_from_db()
        self.assertEqual(setting.value_str, CondicionFiscalEmpresa.RESPONSABLE_INSCRIPTO)

    def test_desglose_valida_negativos(self):
        with self.assertRaises(ValueError):
            desglosar_monto_final_gravado_con_iva("-1")
