"""Valuación de bonos en pesos, escenarios y breakevens.

`Mercado` reúne las series que hacen falta (CER, TAMAR, A3500) ya
"completadas" con valores de respaldo si el BCRA no respondió.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from bonos import BONOS_PESOS, CER_REF_FALLBACK, FECHA_REF, TAMAR_REF, BonoPesos
from calc import (
    factor_tamar, meses_ipc_entre, proyectar_cer, serie_diaria, tea_desde_pago,
    tem_desde_tea, tna_180_360, tna_32_365, tna_plazo_rem, valor_en,
)
from calendario import sumar_habiles

REZAGO_CER = 10      # hábiles
REZAGO_TAMAR = 10    # hábiles
REZAGO_A3500 = 3     # hábiles

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def nombre_mes(ym: tuple[int, int]) -> str:
    return f"{MESES[ym[1] - 1]}-{str(ym[0])[2:]}"


def fmt_fecha(d: date) -> str:
    return d.strftime("%d/%m/%Y")


@dataclass
class Mercado:
    liq: date
    cer: pd.Series             # diaria, ffill
    tamar: pd.Series           # diaria, decimal TNA
    a3500: pd.Series           # diaria
    fuentes: dict = field(default_factory=dict)

    @property
    def ultimo_cer(self) -> date:
        return self.cer.index.max().date()

    @property
    def tamar_actual(self) -> float:
        return float(self.tamar.iloc[-1])

    @property
    def a3500_actual(self) -> float:
        return float(self.a3500.iloc[-1])


def armar_mercado(liq: date, series: dict[str, pd.Series]) -> Mercado:
    fuentes = {}
    cer = series.get("cer")
    if cer is not None and not cer.empty:
        cer = serie_diaria(cer)
        fuentes["CER"] = "BCRA"
    else:
        # Respaldo: CER normalizado (=1 a t-10 de la fecha de referencia) y
        # constante. Las TIR reales siguen siendo correctas (son relativas).
        d0 = sumar_habiles(FECHA_REF, -REZAGO_CER)
        idx = pd.date_range(d0 - timedelta(days=400), max(liq, d0), freq="D")
        cer = pd.Series(CER_REF_FALLBACK, index=idx)
        fuentes["CER"] = "respaldo (CER normalizado, BCRA sin respuesta)"
    tamar = series.get("tamar")
    if tamar is not None and not tamar.empty:
        tamar = serie_diaria(tamar)
        fuentes["TAMAR"] = "BCRA"
    else:
        idx = pd.date_range(FECHA_REF - timedelta(days=60), liq, freq="D")
        tamar = pd.Series(TAMAR_REF, index=idx)
        fuentes["TAMAR"] = f"respaldo ({TAMAR_REF:.2%}, implícita en 1816 al 30/10/2026)"
    a3500 = series.get("a3500")
    if a3500 is not None and not a3500.empty:
        a3500 = serie_diaria(a3500)
        fuentes["A3500"] = "BCRA"
    else:
        ref = next(b.a3500_ref for b in BONOS_PESOS if b.a3500_ref)
        idx = pd.date_range(FECHA_REF - timedelta(days=60), liq, freq="D")
        a3500 = pd.Series(ref, index=idx)
        fuentes["A3500"] = f"respaldo ({ref:,.2f}, implícito en TZV27 al 30/10/2026)"
    return Mercado(liq=liq, cer=cer, tamar=tamar, a3500=a3500, fuentes=fuentes)


# ---------------------------------------------------------------------------
# Supuestos de proyección
# ---------------------------------------------------------------------------


@dataclass
class Supuestos:
    """Supuestos de un escenario. Si un dict está vacío se usa el default."""
    inflacion: dict[tuple[int, int], float] = field(default_factory=dict)
    inflacion_default: float = 0.0       # 0 = CER "plano" (convención TIR real)
    tamar: float | None = None           # TNA proyectada (None = última)
    tamar_mensual: dict[tuple[int, int], float] = field(default_factory=dict)
    a3500_mensual: dict[tuple[int, int], float] = field(default_factory=dict)
    a3500_default: float = 0.0           # devaluación mensual
    mep_mensual: dict[tuple[int, int], float] = field(default_factory=dict)
    mep_default: float = 0.0
    cer_plano: bool = True               # True: CER constante desde t-10 de la liquidación


def proyectar_mensual(valor0: float, d0: date, d1: date, tasas: dict, default: float) -> float:
    """Valor en d1 partiendo de valor0 en d0 con tasas mensuales (capitalización diaria)."""
    v = valor0
    d = d0
    while d < d1:
        d += timedelta(days=1)
        r = tasas.get((d.year, d.month), default)
        dias_mes = (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - date(d.year, d.month, 1)).days
        v *= (1 + r) ** (1 / dias_mes)
    return v


def serie_tamar_proyectada(m: Mercado, hasta: date, sup: Supuestos) -> pd.Series:
    s = m.tamar
    if not sup.tamar_mensual and sup.tamar is None:
        return s
    ult = s.index.max().date()
    vals = {}
    d = ult
    while d < hasta:
        d += timedelta(days=1)
        vals[pd.Timestamp(d)] = sup.tamar_mensual.get((d.year, d.month),
                                                      sup.tamar if sup.tamar is not None else float(s.iloc[-1]))
    return pd.concat([s, pd.Series(vals)]) if vals else s


# ---------------------------------------------------------------------------
# Valuación de bonos en pesos
# ---------------------------------------------------------------------------


def cer_base(b: BonoPesos, m: Mercado) -> float:
    """CER base del bono: CER[t-10 de FECHA_REF] / (VT_ref / 100)."""
    c_ref = valor_en(m.cer, sumar_habiles(FECHA_REF, -REZAGO_CER))
    return c_ref / (b.vt_ref / 100)


def valor_tecnico(b: BonoPesos, m: Mercado) -> float | None:
    if b.tipo == "CER":
        c = valor_en(m.cer, sumar_habiles(m.liq, -REZAGO_CER))
        return 100 * c / cer_base(b, m)
    if b.tipo == "TAMAR":
        if m.liq >= FECHA_REF:
            return b.vt_ref * factor_tamar(m.tamar, FECHA_REF, m.liq, b.margen, m.tamar_actual, REZAGO_TAMAR)
        return b.vt_ref / factor_tamar(m.tamar, m.liq, FECHA_REF, b.margen, m.tamar_actual, REZAGO_TAMAR)
    if b.tipo == "Dólar linked":
        # 100 USD de VN valuados al último A3500 (en pesos, como cotiza el bono)
        return 100.0 * m.a3500_actual
    return None


def pago_final(b: BonoPesos, m: Mercado, sup: Supuestos | None = None) -> float:
    """Pago al vencimiento (por 100 VN) bajo los supuestos dados.

    Sin supuestos -> convención de mercado (la de 1816): CER constante desde
    t-10 de la liquidación, TAMAR constante en el último dato y A3500 constante.
    """
    sup = sup or Supuestos()
    if b.tipo == "Tasa fija":
        return b.pago_final
    if b.tipo == "CER":
        f_cer = sumar_habiles(b.vencimiento, -REZAGO_CER)
        if sup.cer_plano:
            c = valor_en(m.cer, sumar_habiles(m.liq, -REZAGO_CER))
        else:
            serie = proyectar_cer(m.cer, f_cer, sup.inflacion, sup.inflacion_default)
            c = valor_en(serie, f_cer)
        return 100 * c / cer_base(b, m)
    if b.tipo == "TAMAR":
        vt = valor_tecnico(b, m)
        tam = serie_tamar_proyectada(m, b.vencimiento, sup)
        proy = sup.tamar if sup.tamar is not None else m.tamar_actual
        return vt * factor_tamar(tam, m.liq, b.vencimiento, b.margen, proy, REZAGO_TAMAR)
    if b.tipo == "Dólar linked":
        f_a = sumar_habiles(b.vencimiento, -REZAGO_A3500)
        a0 = m.a3500_actual
        d0 = m.a3500.index.max().date()
        a = proyectar_mensual(a0, d0, f_a, sup.a3500_mensual, sup.a3500_default) if f_a > d0 else valor_en(m.a3500, f_a)
        return 100 * a
    raise ValueError(b.tipo)


def indicadores_pesos(b: BonoPesos, precio: float, m: Mercado, sup: Supuestos | None = None) -> dict:
    dias = (b.vencimiento - m.liq).days
    pago = pago_final(b, m, sup)
    tea = tea_desde_pago(precio, pago, dias)
    if b.tipo == "Tasa fija":
        tna, conv = tna_plazo_rem(precio, pago, dias), "plazo rem."
        tem = tem_desde_tea(tea)
    elif b.tipo == "TAMAR":
        tna, conv = tna_32_365(tea), "32/365"
        tem = tem_desde_tea(tea)
    else:
        tna, conv = tna_180_360(tea), "180/360"
        tem = tem_desde_tea(tea)
    vt = valor_tecnico(b, m)
    return {
        "Ticker": b.ticker,
        "Tipo": b.tipo,
        "Vencimiento": b.vencimiento,
        "Días": dias,
        "Precio": precio,
        "VT": vt,
        "Paridad": precio / vt if vt else None,
        "Pago final": pago,
        "TNA": tna,
        "Conv. TNA": conv,
        "TEM": tem,
        "TIR (TEA)": tea,
        "Duration": dias / 365,
        "Dur. mod.": dias / 365 / (1 + tea),
    }


# ---------------------------------------------------------------------------
# Breakevens
# ---------------------------------------------------------------------------


def _biseccion(f, lo, hi, it=120):
    flo = f(lo)
    for _ in range(it):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def par_mas_cercano(b: BonoPesos, candidatos: list[BonoPesos]) -> BonoPesos:
    return min(candidatos, key=lambda c: abs((c.vencimiento - b.vencimiento).days))


def retorno_objetivo(ref: BonoPesos, p_ref: float, obj: BonoPesos, m: Mercado,
                     sup: Supuestos | None = None) -> tuple[float, float]:
    """Factor que debe rendir `obj` para igualar a `ref` (misma TEA al plazo de obj)."""
    dias_ref = (ref.vencimiento - m.liq).days
    tea_ref = tea_desde_pago(p_ref, pago_final(ref, m, sup), dias_ref)
    dias_obj = (obj.vencimiento - m.liq).days
    return (1 + tea_ref) ** (dias_obj / 365), tea_ref


def be_inflacion(fija: BonoPesos, p_fija: float, cer: BonoPesos, p_cer: float, m: Mercado) -> dict:
    factor, tea_fija = retorno_objetivo(fija, p_fija, cer, m)
    pago_obj = p_cer * factor
    f_cer = sumar_habiles(cer.vencimiento, -REZAGO_CER)
    base = cer_base(cer, m)

    def err(pi):
        s = proyectar_cer(m.cer, f_cer, {}, pi)
        return 100 * valor_en(s, f_cer) / base - pago_obj

    pi = _biseccion(err, -0.05, 0.30)
    c0 = valor_en(m.cer, sumar_habiles(m.liq, -REZAGO_CER))
    c_t = pago_obj * base / 100
    meses_desc = meses_ipc_entre(m.ultimo_cer, f_cer)
    meses_tot = meses_ipc_entre(sumar_habiles(m.liq, -REZAGO_CER), f_cer)
    acumulada = c_t / c0 - 1
    return {
        "valor": pi,
        "anual": (1 + pi) ** 12 - 1,
        "acumulada": acumulada,
        "tea_fija": tea_fija,
        "meses_desconocidos": meses_desc,
        "meses_totales": meses_tot,
        "fecha_cer": f_cer,
        "ultimo_cer": m.ultimo_cer,
    }


def be_tamar(fija: BonoPesos, p_fija: float, tam: BonoPesos, p_tam: float, m: Mercado) -> dict:
    factor, tea_fija = retorno_objetivo(fija, p_fija, tam, m)
    pago_obj = p_tam * factor
    vt = valor_tecnico(tam, m)

    def err(t):
        return vt * factor_tamar(m.tamar, m.liq, tam.vencimiento, tam.margen, t, REZAGO_TAMAR) - pago_obj

    t = _biseccion(err, -0.2, 2.0)
    ultimo = m.tamar.index.max().date()
    return {
        "valor": t,
        "tea": (1 + t * 32 / 365) ** (365 / 32) - 1,
        "tea_fija": tea_fija,
        "desde": max(sumar_habiles(m.liq, -REZAGO_TAMAR), ultimo + timedelta(days=1)),
        "hasta": sumar_habiles(tam.vencimiento - timedelta(days=1), -REZAGO_TAMAR),
        "ultimo_dato": ultimo,
    }


def be_dolar(fija: BonoPesos, p_fija: float, dl: BonoPesos, p_dl: float, m: Mercado) -> dict:
    factor, tea_fija = retorno_objetivo(fija, p_fija, dl, m)
    a_t = p_dl * factor / 100
    a0 = m.a3500_actual
    f_a = sumar_habiles(dl.vencimiento, -REZAGO_A3500)
    dias = (f_a - m.a3500.index.max().date()).days
    var = a_t / a0 - 1
    return {
        "valor": a_t,
        "a3500_hoy": a0,
        "variacion": var,
        "mensual": (1 + var) ** (30 / dias) - 1 if dias > 0 else float("nan"),
        "anual": (1 + var) ** (365 / dias) - 1 if dias > 0 else float("nan"),
        "fecha": f_a,
        "tea_fija": tea_fija,
    }


def tcr_esperado(cer: BonoPesos, p_cer: float, dl: BonoPesos, p_dl: float, m: Mercado) -> dict:
    """Variación real del A3500 (deflactado por CER) que iguala CER y dólar linked.

    Rinde igual si A_T/A_0 / (C_T/C_0) = [(pago_CER/P_CER) / (C_T/C_0)] / [(pago_DL/P_DL) / (A_T/A_0)]^-1
    -> la depreciación real implícita es el cociente de los rendimientos "reales"
    de cada bono medidos contra su propio índice.
    """
    dias_cer = (cer.vencimiento - m.liq).days
    dias_dl = (dl.vencimiento - m.liq).days
    # rendimiento sobre el índice (índice constante): VT/P para CER, 100*A0/P para DL
    r_cer = (pago_final(cer, m) / p_cer) ** (365 / dias_cer) - 1
    r_dl = (100 * m.a3500_actual / p_dl) ** (365 / dias_dl) - 1
    anual = (1 + r_cer) / (1 + r_dl) - 1
    return {
        "anual": anual,
        "acumulada": (1 + anual) ** (dias_dl / 365) - 1,
        "r_cer": r_cer,
        "r_dl": r_dl,
        "fecha_a3500": sumar_habiles(dl.vencimiento, -REZAGO_A3500),
        "fecha_cer": sumar_habiles(cer.vencimiento, -REZAGO_CER),
    }


def tasa_real_be(tam: BonoPesos, p_tam: float, cer: BonoPesos, p_cer: float, m: Mercado) -> dict:
    """Tasa real (TAMAR + margen, deflactada por CER) que iguala TAMAR y CER.

    Rinde igual si  (1+TEA_{TAMAR+m}) * k = (1+r_CER)(1+pi)  con k=(VT/P)^(1/t).
    => (1+TEA_{TAMAR+m})/(1+pi) = (1+r_CER)/k : no depende de la inflación.
    """
    dias_cer = (cer.vencimiento - m.liq).days
    dias_tam = (tam.vencimiento - m.liq).days
    r_cer = (pago_final(cer, m) / p_cer) ** (365 / dias_cer) - 1
    vt = valor_tecnico(tam, m)
    k = (vt / p_tam) ** (365 / dias_tam)
    real_con_margen = (1 + r_cer) / k - 1
    tea_margen = (1 + tam.margen * 32 / 365) ** (365 / 32) - 1
    real_sin_margen = (1 + real_con_margen) / (1 + tea_margen) - 1
    return {
        "con_margen": real_con_margen,
        "sin_margen": real_sin_margen,
        "r_cer": r_cer,
        "margen": tam.margen,
        "hasta_tamar": sumar_habiles(tam.vencimiento - timedelta(days=1), -REZAGO_TAMAR),
        "hasta_cer": sumar_habiles(cer.vencimiento, -REZAGO_CER),
    }
