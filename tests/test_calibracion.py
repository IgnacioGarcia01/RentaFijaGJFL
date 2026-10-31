"""Valida el motor contra los indicadores de 1816 del 30/10/2026.

Correr con:  python -m pytest -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from bonos import BONOS_PESOS, BONOS_USD, FECHA_REF
from calc import analizar_usd
from referencia_1816 import PESOS, USD
from valuacion import armar_mercado, indicadores_pesos


@pytest.mark.parametrize("bono", [b for b in BONOS_USD if b.ticker in USD], ids=lambda b: b.ticker)
def test_usd(bono):
    precio, tea, dur, par = USD[bono.ticker]
    r = analizar_usd(bono, precio, FECHA_REF)
    assert r.tir == pytest.approx(tea, abs=5e-5)        # < 0,5 pb
    assert r.paridad == pytest.approx(par, abs=1e-4)
    assert r.duration == pytest.approx(dur, abs=6e-3)


@pytest.mark.parametrize("bono", BONOS_PESOS, ids=lambda b: b.ticker)
def test_pesos(bono):
    precio, tea, tna, tem = PESOS[bono.ticker]
    m = armar_mercado(FECHA_REF, {})
    r = indicadores_pesos(bono, precio, m)
    assert r["TIR (TEA)"] == pytest.approx(tea, abs=5e-5)
    assert r["TNA"] == pytest.approx(tna, abs=5e-5)
    assert r["TEM"] == pytest.approx(tem, abs=5e-6)
