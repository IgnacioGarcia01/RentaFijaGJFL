"""Compara contra los indicadores de 1816 del 30/10/2026 (liquidación 02/11/2026).

1816 descuenta los flujos en la fecha de pago efectiva (día hábil) y la planilla
en la fecha teórica: en bonos en USD eso da diferencias de hasta ~3 pb.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from bonos import BONOS_PESOS, BONOS_USD, FECHA_REF, TAMAR_REF
from calc import analizar_usd
from referencia_1816 import PESOS, USD
from valuacion import Supuestos, armar_mercado, indicadores_pesos


@pytest.mark.parametrize("bono", [b for b in BONOS_USD if b.ticker in USD and b.ticker != "BPOC7"],
                         ids=lambda b: b.ticker)
def test_usd(bono):
    precio, tea, dur, par = USD[bono.ticker]
    r = analizar_usd(bono, precio, FECHA_REF)
    assert r.tir == pytest.approx(tea, abs=3e-4)
    assert r.paridad == pytest.approx(par, abs=1e-4)
    assert r.duration == pytest.approx(dur, abs=1e-2)


@pytest.mark.parametrize("bono", BONOS_PESOS, ids=lambda b: b.ticker)
def test_pesos(bono):
    """Tasa fija y TAMAR: TIR nominal. CER y DL: TIR real / USD (convención de 1816)."""
    precio, tea, tna, tem = PESOS[bono.ticker]
    m = armar_mercado(FECHA_REF, {})
    r = indicadores_pesos(bono, precio, m, Supuestos(tamar=TAMAR_REF))
    tir = r["TIR real / USD"] if bono.tipo in ("CER", "Dólar linked") else r["TIR (TEA)"]
    # CER: el respaldo del Excel no tiene el IPC de sep-26 (supone 2%), 1816 usa el real -> ~2 pb.
    tol = {"CER": 3e-4, "TAMAR": 1e-4}.get(bono.tipo, 5e-6)
    assert tir == pytest.approx(tea, abs=tol)
