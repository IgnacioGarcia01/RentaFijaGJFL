"""Valuación de bonos en pesos, escenarios y breakevens (fórmulas del Excel).

`Mercado` reúne las series históricas (CER, TAMAR, A3500). Si el BCRA no
responde se usan las series de respaldo tomadas del Excel (data/respaldo.json).
`Supuestos` guarda las proyecciones: inflación mensual (mueve el CER), TAMAR,
A3500 y dólar MEP.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from bonos import BONOS_PESOS, TAMAR_REF, BonoPesos
from calc import (
    anualizar, days360, mensualizar, meses_ipc_entre, proyectar_cer, serie_diaria,
    tem_desde_tea, tem_desde_tna_tamar, tna_180_360, tna_32_365, tna_plazo_rem, valor_en,
)
from calendario import es_habil, sumar_habiles

REZAGO_CER = 10      # hábiles
REZAGO_TAMAR = 10    # hábiles
REZAGO_A3500 = 3     # hábiles

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
RESPALDO = Path(__file__).parent / "data" / "respaldo.json"


def nombre_mes(ym: tuple[int, int]) -> str:
    return f"{MESES[ym[1] - 1]}-{str(ym[0])[2:]}"


def fmt_fecha(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def series_respaldo() -> dict[str, pd.Series]:
    """CER, IPC y TAMAR del Excel (para cuando el BCRA no responde)."""
    js = json.loads(RESPALDO.read_text())
    out = {}
    for k in ("cer", "tamar", "ipc"):
        d = js.get(k, {})
        s = pd.Series({pd.Timestamp(f): float(v) for f, v in d.items()}).sort_index()
        out[k] = s
    out["tamar"] = out["tamar"] / 100
    return out


# ---------------------------------------------------------------------------
# Mercado y supuestos
# ---------------------------------------------------------------------------


@dataclass
class Mercado:
    liq: date
    cer: pd.Series             # diaria (publicada)
    tamar: pd.Series           # días hábiles, decimal TNA
    a3500: pd.Series           # diaria
    ipc: pd.Series             # mensual, decimal
    fuentes: dict = field(default_factory=dict)

    @property
    def ultimo_cer(self) -> date:
        return self.cer.index.max().date()

    @property
    def ultimo_tamar(self) -> date:
        return self.tamar.index.max().date()

    @property
    def tamar_actual(self) -> float:
        return float(self.tamar.iloc[-1])

    @property
    def tamar_prom5(self) -> float:
        return float(self.tamar.iloc[-5:].mean())

    @property
    def a3500_actual(self) -> float:
        return float(self.a3500.iloc[-1])


def armar_mercado(liq: date, series: dict[str, pd.Series]) -> Mercado:
    fuentes = {}
    resp = None

    def respaldo():
        nonlocal resp
        if resp is None:
            resp = series_respaldo()
        return resp

    ipc = series.get("inflacion")
    if ipc is not None and not ipc.empty:
        fuentes["IPC"] = "BCRA"
    else:
        ipc = respaldo()["ipc"]
        fuentes["IPC"] = f"respaldo Excel (hasta {ipc.index.max():%m/%Y})"

    cer = series.get("cer")
    if cer is not None and not cer.empty:
        cer = serie_diaria(cer)
        fuentes["CER"] = "BCRA"
    else:
        cer = serie_diaria(respaldo()["cer"])
        # Completar con el IPC conocido (el CER se publica con ~1,5 meses de rezago)
        conocidos = {(t.year, t.month): float(v) for t, v in ipc.items()}
        ult = cer.index.max().date()
        hasta = ult
        while True:
            sig = hasta + timedelta(days=1)
            from calc import mes_ipc_que_aplica
            if mes_ipc_que_aplica(sig) not in conocidos:
                break
            hasta = sig
        if hasta > ult:
            cer = proyectar_cer(cer, hasta, conocidos, 0.0)
        fuentes["CER"] = f"respaldo Excel (hasta {fmt_fecha(cer.index.max().date())})"

    tamar = series.get("tamar")
    if tamar is not None and not tamar.empty:
        tamar = tamar.sort_index()
        fuentes["TAMAR"] = "BCRA"
    else:
        tamar = respaldo()["tamar"]
        if tamar.empty:
            tamar = pd.Series([TAMAR_REF], index=[pd.Timestamp(liq)])
        fuentes["TAMAR"] = f"respaldo Excel (hasta {fmt_fecha(tamar.index.max().date())})"

    a3500 = series.get("a3500")
    if a3500 is not None and not a3500.empty:
        a3500 = serie_diaria(a3500)
        fuentes["A3500"] = "BCRA"
    else:
        ref = next(b.a3500_ref for b in BONOS_PESOS if b.a3500_ref)
        idx = pd.date_range(liq - timedelta(days=60), liq, freq="D")
        a3500 = pd.Series(ref, index=idx)
        fuentes["A3500"] = f"respaldo ({ref:,.2f}, implícito en TZV27 al 30/10/2026)"
    return Mercado(liq=liq, cer=cer, tamar=tamar, a3500=a3500, ipc=ipc, fuentes=fuentes)


@dataclass
class Supuestos:
    """Proyecciones. Las claves de los dict son (año, mes)."""
    inflacion: dict[tuple[int, int], float] = field(default_factory=dict)   # IPC mensual
    inflacion_default: float = 0.02
    tamar: float = TAMAR_REF                                                 # TNA proyectada
    tamar_mensual: dict[tuple[int, int], float] = field(default_factory=dict)
    a3500_mensual: dict[tuple[int, int], float] = field(default_factory=dict)
    a3500_default: float = 0.0
    mep_mensual: dict[tuple[int, int], float] = field(default_factory=dict)
    mep_default: float = 0.0


def proyectar_mensual(valor0: float, d0: date, d1: date, tasas: dict, default: float) -> float:
    """Valor en d1 partiendo de valor0 en d0 con variaciones mensuales (capitalización diaria)."""
    v = valor0
    d = d0
    while d < d1:
        d += timedelta(days=1)
        r = tasas.get((d.year, d.month), default)
        dias_mes = (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - date(d.year, d.month, 1)).days
        v *= (1 + r) ** (1 / dias_mes)
    return v


def cer_proyectado(m: Mercado, hasta: date, sup: Supuestos) -> pd.Series:
    return proyectar_cer(m.cer, hasta, sup.inflacion, sup.inflacion_default)


def tamar_proyectada(m: Mercado, hasta: date, sup: Supuestos) -> pd.Series:
    """Serie TAMAR (hábiles) extendida con la proyección (hoja 'Serie TAMAR')."""
    s = m.tamar
    ult = s.index.max().date()
    vals = {}
    d = ult
    while d < hasta:
        d += timedelta(days=1)
        if es_habil(d):
            vals[pd.Timestamp(d)] = sup.tamar_mensual.get((d.year, d.month), sup.tamar)
    return pd.concat([s, pd.Series(vals, dtype=float)]) if vals else s


def a3500_en(m: Mercado, d: date, sup: Supuestos) -> float:
    d0 = m.a3500.index.max().date()
    if d <= d0:
        return valor_en(m.a3500, d)
    return proyectar_mensual(m.a3500_actual, d0, d, sup.a3500_mensual, sup.a3500_default)


# ---------------------------------------------------------------------------
# Valuación de bonos en pesos
# ---------------------------------------------------------------------------


def promedio_tamar(serie: pd.Series, desde: date, hasta: date) -> float:
    """AVERAGEIFS de la TAMAR entre dos fechas (inclusive)."""
    sub = serie.loc[pd.Timestamp(desde): pd.Timestamp(hasta)]
    return float(sub.mean())


def pago_tamar(b: BonoPesos, serie: pd.Series) -> tuple[float, float, float]:
    """Pago final, TAMAR promedio y TEM (hoja TML27 del Excel)."""
    prom = promedio_tamar(serie, sumar_habiles(b.emision, -REZAGO_TAMAR), sumar_habiles(b.vencimiento, -REZAGO_TAMAR))
    tem = tem_desde_tna_tamar(prom + b.margen)
    return 100 * (1 + tem) ** (days360(b.emision, b.vencimiento) / 30), prom, tem


def valor_tecnico(b: BonoPesos, m: Mercado, sup: Supuestos | None = None) -> float | None:
    if b.tipo == "CER":
        # Si el CER de t-10 todavía no se publicó se toma el proyectado (como la hoja CER).
        f = sumar_habiles(m.liq, -REZAGO_CER)
        return 100 * valor_en(cer_proyectado(m, f, sup or Supuestos()), f) / b.cer_base
    if b.tipo == "TAMAR":
        hasta = min(sumar_habiles(m.liq, -REZAGO_TAMAR), m.ultimo_tamar)
        prom = promedio_tamar(m.tamar, sumar_habiles(b.emision, -REZAGO_TAMAR), hasta)
        return 100 * (1 + tem_desde_tna_tamar(prom + b.margen)) ** (days360(b.emision, m.liq) / 30)
    if b.tipo == "Dólar linked":
        return 100.0 * m.a3500_actual
    return None


def pago_final(b: BonoPesos, m: Mercado, sup: Supuestos) -> float:
    if b.tipo == "Tasa fija":
        return b.pago_final
    if b.tipo == "CER":
        f = sumar_habiles(b.vencimiento, -REZAGO_CER)
        return 100 * valor_en(cer_proyectado(m, f, sup), f) / b.cer_base
    if b.tipo == "TAMAR":
        return pago_tamar(b, tamar_proyectada(m, b.vencimiento, sup))[0]
    if b.tipo == "Dólar linked":
        return 100 * a3500_en(m, sumar_habiles(b.vencimiento, -REZAGO_A3500), sup)
    raise ValueError(b.tipo)


def tir_real_o_usd(b: BonoPesos, precio: float, m: Mercado, sup: Supuestos | None = None) -> float | None:
    """CER: TIR real (CER t-10 constante). DL: TIR en USD (A3500 constante)."""
    dias = (b.vencimiento - m.liq).days
    if b.tipo == "CER":
        return (valor_tecnico(b, m, sup) / precio) ** (365 / dias) - 1
    if b.tipo == "Dólar linked":
        return (100 * m.a3500_actual / precio) ** (365 / dias) - 1
    return None


def indicadores_pesos(b: BonoPesos, precio: float, m: Mercado, sup: Supuestos) -> dict:
    dias = (b.vencimiento - m.liq).days
    pago = pago_final(b, m, sup)
    tir = (pago / precio) ** (365 / dias) - 1          # = XIRR con un único flujo
    if b.tipo == "Tasa fija":
        tna, conv = tna_plazo_rem(precio, pago, dias), "plazo rem."
    elif b.tipo == "TAMAR":
        tna, conv = tna_32_365(tir), "32/365"
    else:
        tna, conv = tna_180_360(tir), "180/360"
    vt = valor_tecnico(b, m, sup)
    return {
        "Ticker": b.ticker,
        "Tipo": b.tipo,
        "Vencimiento": b.vencimiento,
        "Días": dias,
        "Precio": precio,
        "VT": vt,
        "Paridad": precio / vt if vt else None,
        "Pago final": pago,
        "Retorno directo": pago / precio - 1,
        "TNA": tna,
        "Conv. TNA": conv,
        "TEM": tem_desde_tea(tir),
        "TIR (TEA)": tir,
        "TIR real / USD": tir_real_o_usd(b, precio, m, sup),
        "MD": dias / 365,
    }


# ---------------------------------------------------------------------------
# Breakevens (hoja Breakeven del Excel, extendida al resto de los pares)
# ---------------------------------------------------------------------------


def _biseccion(f, lo, hi, it=150):
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


def factor_objetivo(ref: BonoPesos, p_ref: float, obj: BonoPesos, m: Mercado, sup: Supuestos) -> float:
    """Retorno total que debe dar `obj` para igualar a `ref`.

    Mismo vencimiento: el retorno directo de `ref` (Excel: C6/C5). Si no, la TEA
    de `ref` llevada al plazo de `obj` (breakeven aproximado).
    """
    pago = pago_final(ref, m, sup)
    if ref.vencimiento == obj.vencimiento:
        return pago / p_ref
    tea = (pago / p_ref) ** (365 / (ref.vencimiento - m.liq).days) - 1
    return (1 + tea) ** ((obj.vencimiento - m.liq).days / 365)


def be_inflacion(fija: BonoPesos, p_fija: float, cer: BonoPesos, p_cer: float, m: Mercado,
                 sup: Supuestos, shock_fija: float = 0.0, shock_cer: float = 0.0) -> dict:
    p_f, p_c = p_fija * (1 + shock_fija), p_cer * (1 + shock_cer)
    f0 = sumar_habiles(m.liq, -REZAGO_CER)
    f1 = sumar_habiles(cer.vencimiento, -REZAGO_CER)
    dias = (f1 - f0).days
    cer_ini = valor_en(cer_proyectado(m, f0, sup), f0)
    cer_be = cer.cer_base * p_c / 100 * factor_objetivo(fija, p_f, cer, m, sup)
    acum = cer_be / cer_ini - 1
    cer_proy = valor_en(cer_proyectado(m, f1, sup), f1)
    proy_acum = cer_proy / cer_ini - 1
    return {
        "valor": mensualizar(acum, dias),
        "anual": anualizar(acum, dias),
        "acumulada": acum,
        "cer_be": cer_be,
        "cer_ini": cer_ini,
        "proyectada": mensualizar(proy_acum, dias),
        "fecha_ini": f0,
        "fecha_fin": f1,
        "dias": dias,
        "mismo_vto": fija.vencimiento == cer.vencimiento,
        "meses": meses_ipc_entre(f0, f1),
        "meses_desconocidos": meses_ipc_entre(max(f0, m.ultimo_cer), f1),
        "ultimo_cer": m.ultimo_cer,
    }


def be_tamar(fija: BonoPesos, p_fija: float, tam: BonoPesos, p_tam: float, m: Mercado, sup: Supuestos) -> dict:
    pago_obj = p_tam * factor_objetivo(fija, p_fija, tam, m, sup)

    def err(t):
        s = Supuestos(**{**sup.__dict__, "tamar": t, "tamar_mensual": {}})
        return pago_tamar(tam, tamar_proyectada(m, tam.vencimiento, s))[0] - pago_obj

    t = _biseccion(err, -0.2, 2.0)
    return {
        "valor": t,
        "desde": m.ultimo_tamar + timedelta(days=1),
        "hasta": sumar_habiles(tam.vencimiento, -REZAGO_TAMAR),
        "ultimo_dato": m.ultimo_tamar,
        "inicio_promedio": sumar_habiles(tam.emision, -REZAGO_TAMAR),
    }


def be_dolar(fija: BonoPesos, p_fija: float, dl: BonoPesos, p_dl: float, m: Mercado, sup: Supuestos) -> dict:
    a_t = p_dl * factor_objetivo(fija, p_fija, dl, m, sup) / 100
    f_a = sumar_habiles(dl.vencimiento, -REZAGO_A3500)
    d0 = m.a3500.index.max().date()
    dias = (f_a - d0).days
    var = a_t / m.a3500_actual - 1
    return {
        "valor": a_t, "a3500_hoy": m.a3500_actual, "fecha_a3500_hoy": d0, "variacion": var,
        "mensual": mensualizar(var, dias), "anual": anualizar(var, dias), "fecha": f_a,
    }


def tcr_esperado(cer: BonoPesos, p_cer: float, dl: BonoPesos, p_dl: float, m: Mercado,
                 sup: Supuestos | None = None) -> dict:
    """Variación real del A3500 (deflactado por CER) que iguala CER y dólar linked.

    Rinden igual si (A_T/A_0) / (C_T/C_0) = (1 + r_CER)^t / (1 + r_DL)^t, con
    r_CER = tasa real del CER y r_DL = tasa en USD del dólar linked.
    """
    r_cer = tir_real_o_usd(cer, p_cer, m, sup)
    r_dl = tir_real_o_usd(dl, p_dl, m)
    anual = (1 + r_cer) / (1 + r_dl) - 1
    dias = (dl.vencimiento - m.liq).days
    return {
        "anual": anual, "acumulada": (1 + anual) ** (dias / 365) - 1, "r_cer": r_cer, "r_dl": r_dl,
        "fecha_a3500": sumar_habiles(dl.vencimiento, -REZAGO_A3500),
        "fecha_cer": sumar_habiles(cer.vencimiento, -REZAGO_CER),
    }


def tasa_real_be(tam: BonoPesos, p_tam: float, cer: BonoPesos, p_cer: float, m: Mercado, sup: Supuestos) -> dict:
    """TAMAR real que iguala TAMAR y CER.

    Para cada inflación, la TAMAR que iguala ambos bonos es T(pi); la tasa
    real implícita es (1 + TEA(T))/(1 + pi) - 1. Se resuelve con la inflación
    del escenario (y es casi insensible a ella).
    """
    dias_tam = (tam.vencimiento - m.liq).days
    dias_cer = (cer.vencimiento - m.liq).days
    f1 = sumar_habiles(cer.vencimiento, -REZAGO_CER)
    f0 = sumar_habiles(m.liq, -REZAGO_CER)
    r_cer = tir_real_o_usd(cer, p_cer, m, sup)
    infl_cer = valor_en(cer_proyectado(m, f1, sup), f1) / valor_en(cer_proyectado(m, f0, sup), f0)
    infl_anual = infl_cer ** (365 / (f1 - f0).days) - 1
    # Retorno nominal del CER con la inflación del escenario, anualizado y llevado al plazo del TAMAR
    tea_cer_nom = (1 + r_cer) * (1 + infl_anual) - 1
    pago_obj = p_tam * (1 + tea_cer_nom) ** (dias_tam / 365)

    def err(t):
        s = Supuestos(**{**sup.__dict__, "tamar": t, "tamar_mensual": {}})
        return pago_tamar(tam, tamar_proyectada(m, tam.vencimiento, s))[0] - pago_obj

    t = _biseccion(err, -0.2, 2.0)
    tea_t = (1 + t * 32 / 365) ** (365 / 32) - 1
    tea_tm = (1 + (t + tam.margen) * 32 / 365) ** (365 / 32) - 1
    return {
        "sin_margen": (1 + tea_t) / (1 + infl_anual) - 1,
        "con_margen": (1 + tea_tm) / (1 + infl_anual) - 1,
        "tamar_be": t, "inflacion_supuesta": infl_anual, "r_cer": r_cer, "margen": tam.margen,
        "hasta_tamar": sumar_habiles(tam.vencimiento, -REZAGO_TAMAR),
        "hasta_cer": f1,
        "mismo_vto": tam.vencimiento == cer.vencimiento,
        "dias_cer": dias_cer,
    }
