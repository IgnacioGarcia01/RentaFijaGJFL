"""Reproduce la planilla Soberanos_por_asset_class.xlsx."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import calendario
from bonos import BONOS_PESOS, BONOS_USD
from calc import analizar_usd
from referencia_excel import BREAKEVEN, FERIADOS_HOJA, FERIADOS_HOJA_CER, LIQ, PESOS, USD
from valuacion import Supuestos, armar_mercado, be_inflacion, indicadores_pesos

LIQ_D = date.fromisoformat(LIQ)
SUP = Supuestos(inflacion_default=0.02, tamar=0.22)
B = {b.ticker: b for b in BONOS_PESOS}


@pytest.fixture
def feriados_excel():
    original = set(calendario.FERIADOS)

    def usar(lista):
        calendario.FERIADOS.clear()
        calendario.FERIADOS.update(date.fromisoformat(d) for d in lista)

    yield usar
    calendario.FERIADOS.clear()
    calendario.FERIADOS.update(original)


@pytest.mark.parametrize("ticker", list(USD))
def test_usd(ticker):
    precio, tir, md, par = USD[ticker]
    b = next(x for x in BONOS_USD if x.ticker == ticker)
    r = analizar_usd(b, precio, LIQ_D)
    assert r.tir == pytest.approx(tir, abs=1e-7)
    assert r.duration == pytest.approx(md, abs=1e-6)
    assert r.paridad == pytest.approx(par, abs=1e-6)


def test_tasa_fija():
    m = armar_mercado(LIQ_D, {})
    precio, tir = PESOS["T31Y7"]
    assert indicadores_pesos(B["T31Y7"], precio, m, SUP)["TIR (TEA)"] == pytest.approx(tir, abs=1e-6)


def test_cer(feriados_excel):
    feriados_excel(FERIADOS_HOJA_CER)
    m = armar_mercado(LIQ_D, {})
    precio, tir = PESOS["TZXY7"]
    assert indicadores_pesos(B["TZXY7"], precio, m, SUP)["TIR (TEA)"] == pytest.approx(tir, abs=1e-6)


def test_breakeven(feriados_excel):
    feriados_excel(FERIADOS_HOJA)
    m = armar_mercado(LIQ_D, {})
    r = be_inflacion(B["T31Y7"], PESOS["T31Y7"][0], B["TZXY7"], PESOS["TZXY7"][0], m, SUP)
    assert r["valor"] == pytest.approx(BREAKEVEN["mensual"], abs=1e-6)
    assert r["acumulada"] == pytest.approx(BREAKEVEN["acumulada"], abs=1e-6)
    assert r["cer_ini"] == pytest.approx(BREAKEVEN["cer_ini"], abs=1e-3)
    assert r["proyectada"] == pytest.approx(BREAKEVEN["proyectada"], abs=1e-5)


def test_tamar_con_feriados_reales():
    """Con el calendario real (incluye 2027) la diferencia con la planilla es < 5 pb."""
    m = armar_mercado(LIQ_D, {})
    precio, tir = PESOS["TML27"]
    assert indicadores_pesos(B["TML27"], precio, m, SUP)["TIR (TEA)"] == pytest.approx(tir, abs=5e-4)
