"""Motor de cálculo: flujos, TIR, TNA, TEM, duration y proyecciones.

Replica las fórmulas de la planilla "Soberanos_por_asset_class.xlsx":
- TIR = XIRR de Excel: descuento (1 + TIR) ^ (días / 365) sobre fechas teóricas.
- Intereses de bonos en USD: tasa x YEARFRAC(base 0, 30/360 US).
- MD (como en el Excel) = duration de Macaulay: sum(VP x t) / sum(VP), t = act/365.
- Capitalizables (LECAP/BONCAP/TAMAR): 100 x (1+TEM)^(DAYS360(emisión, vto)/30).
- TEM = (1 + TIR) ^ (1/12) - 1.
Las TNA siguen las convenciones de 1816 (plazo remanente, 180/360 y 32/365).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from bonos import BonoUSD
from calendario import es_habil

# ---------------------------------------------------------------------------
# Conteo de días estilo Excel
# ---------------------------------------------------------------------------


def _ultimo_dia(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def days360(d0: date, d1: date) -> int:
    """DAYS360 de Excel (método US/NASD, sin el argumento 'method')."""
    a, b = d0.day, d1.day
    if _ultimo_dia(d0):
        a = 30
    if _ultimo_dia(d1) and b == 31:
        if a < 30:
            # Excel: el día 31 final con inicio < 30 pasa al 1 del mes siguiente
            return (d1.year - d0.year) * 360 + (d1.month - d0.month) * 30 + (b - a)
        b = 30
    return (d1.year - d0.year) * 360 + (d1.month - d0.month) * 30 + (b - a)


def yearfrac_30360(d0: date, d1: date) -> float:
    """YEARFRAC(d0, d1, 0) de Excel (30/360 US)."""
    y1, m1, a = d0.year, d0.month, d0.day
    y2, m2, b = d1.year, d1.month, d1.day
    a_orig = a
    feb_fin_0 = m1 == 2 and _ultimo_dia(d0)
    feb_fin_1 = m2 == 2 and _ultimo_dia(d1)
    if feb_fin_0 and feb_fin_1:
        b = 30
    if feb_fin_0:
        a = 30
    # Igual que Excel: el ajuste del 31 final mira el día de inicio original
    if b == 31 and a_orig >= 30:
        b = 30
    if a == 31:
        a = 30
    return ((y2 - y1) * 360 + (m2 - m1) * 30 + (b - a)) / 360


def yearfrac_365(d0: date, d1: date) -> float:
    """YEARFRAC(d0, d1, 3): act/365."""
    return (d1 - d0).days / 365


def habil_siguiente_o_igual(d: date) -> date:
    while not es_habil(d):
        d += timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Conversiones de tasas
# ---------------------------------------------------------------------------


def tea_desde_pago(precio: float, pago: float, dias: int) -> float:
    return (pago / precio) ** (365 / dias) - 1


def tna_plazo_rem(precio: float, pago: float, dias: int) -> float:
    return (pago / precio - 1) * 365 / dias


def tna_180_360(tea: float) -> float:
    return ((1 + tea) ** 0.5 - 1) * 2


def tna_32_365(tea: float) -> float:
    return ((1 + tea) ** (32 / 365) - 1) * 365 / 32


def tem_desde_tea(tea: float) -> float:
    return (1 + tea) ** (1 / 12) - 1


def tem_desde_tna_tamar(tna: float) -> float:
    """TEM de la TAMAR (convención del Excel): ((1+TNA/(365/32))^(365/32))^(1/12)-1."""
    return ((1 + tna / (365 / 32)) ** (365 / 32)) ** (1 / 12) - 1


def mensualizar(acumulada: float, dias: int) -> float:
    """Tasa mensual equivalente: (1+acum)^((365/12)/días) - 1 (Excel, hoja Breakeven)."""
    return (1 + acumulada) ** ((365 / 12) / dias) - 1


def anualizar(acumulada: float, dias: int) -> float:
    return (1 + acumulada) ** (365 / dias) - 1


# ---------------------------------------------------------------------------
# Series diarias (CER, TAMAR, A3500)
# ---------------------------------------------------------------------------


def serie_diaria(s: pd.Series) -> pd.Series:
    """Ordena y completa los días faltantes con el último dato (ffill)."""
    if s is None or s.empty:
        return s
    s = s.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(idx).ffill()


def valor_en(s: pd.Series, d: date) -> float | None:
    """Último valor disponible a la fecha d (inclusive), como VLOOKUP aproximado."""
    if s is None or s.empty:
        return None
    sub = s.loc[: pd.Timestamp(d)]
    if sub.empty:
        return None
    return float(sub.iloc[-1])


# ---------------------------------------------------------------------------
# CER proyectado (hoja CER del Excel)
# ---------------------------------------------------------------------------
# Del día 16 del mes m al 15 del mes m+1 el CER crece geométricamente a la
# inflación del mes m-1: CER_t = CER_{t-1} x (1+IPC)^(1/días del tramo).


def mes_ipc_que_aplica(d: date) -> tuple[int, int]:
    """Mes de IPC que determina la variación diaria del CER en la fecha d."""
    y, m = d.year, d.month
    m -= 1 if d.day >= 16 else 2
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def _dias_tramo(d: date) -> int:
    """Días del tramo 16/m - 15/m+1 que contiene a d (= días entre los dos 15)."""
    if d.day >= 16:
        ini = date(d.year, d.month, 15)
    else:
        pm = date(d.year, d.month, 1) - timedelta(days=1)
        ini = date(pm.year, pm.month, 15)
    ny, nm = (ini.year + (ini.month == 12), ini.month % 12 + 1)
    return (date(ny, nm, 15) - ini).days


def proyectar_cer(cer_hist: pd.Series, hasta: date, inflacion: dict[tuple[int, int], float],
                  inflacion_default: float) -> pd.Series:
    """Extiende la serie de CER hasta `hasta` con inflación mensual proyectada."""
    s = serie_diaria(cer_hist)
    ultimo = s.index.max().date()
    if ultimo >= hasta:
        return s
    valores = {}
    v = float(s.iloc[-1])
    d = ultimo
    while d < hasta:
        d += timedelta(days=1)
        pi = inflacion.get(mes_ipc_que_aplica(d), inflacion_default)
        v *= (1 + pi) ** (1 / _dias_tramo(d))
        valores[pd.Timestamp(d)] = v
    return pd.concat([s, pd.Series(valores)])


def meses_ipc_entre(d0: date, d1: date, minimo_dias: int = 15) -> list[tuple[int, int]]:
    """Meses de IPC que el CER incorpora entre d0 y d1 (al menos `minimo_dias` días de cada tramo)."""
    conteo: dict[tuple[int, int], int] = {}
    d = d0 + timedelta(days=1)
    while d <= d1:
        ym = mes_ipc_que_aplica(d)
        conteo[ym] = conteo.get(ym, 0) + 1
        d += timedelta(days=1)
    return [ym for ym, n in conteo.items() if n >= minimo_dias]


# ---------------------------------------------------------------------------
# Bonos en USD (hojas AL35D, AO28D, BPC7D, SJO35 del Excel)
# ---------------------------------------------------------------------------


def _tasa_cupon(b: BonoUSD, fin_periodo: date) -> float:
    tasa = 0.0
    for desde, t in b.cupones:
        if fin_periodo > desde:
            tasa = t
    return tasa


def flujos_usd(b: BonoUSD) -> pd.DataFrame:
    """Cronograma completo por 100 VN original."""
    amort = {d: a for d, a in b.amortizaciones}
    residual = 100.0
    prev = b.emision
    filas = []
    for d in b.fechas:
        a = amort.get(d, 0.0)
        tasa = _tasa_cupon(b, d)
        interes = residual * tasa * yearfrac_30360(prev, d)
        filas.append({"fecha": d, "inicio": prev, "residual": residual, "tasa": tasa,
                      "interes": interes, "amortizacion": a, "total": interes + a})
        residual -= a
        prev = d
    return pd.DataFrame(filas)


def xirr(flujos: list[tuple[date, float]]) -> float:
    """XIRR de Excel: sum(F_i / (1+r)^((d_i - d_0)/365)) = 0, por bisección."""
    d0 = flujos[0][0]
    tiempos = [((d - d0).days / 365, v) for d, v in flujos]

    def f(r):
        return sum(v / (1 + r) ** t for t, v in tiempos)

    lo, hi = -0.99, 10.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return float("nan")
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-12:
            break
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass
class ResultadoUSD:
    tir: float
    tna: float
    duration: float        # "MD" del Excel (Macaulay)
    duration_mod: float
    residual: float
    interes_corrido: float
    valor_tecnico: float
    paridad: float
    current_yield: float
    proximo_pago: date | None
    flujo_a_cobrar: float


def analizar_usd(b: BonoUSD, precio: float, liq: date) -> ResultadoUSD:
    cf = flujos_usd(b)
    futuros = cf[cf["fecha"] > liq]
    if futuros.empty:
        nan = float("nan")
        return ResultadoUSD(nan, nan, nan, nan, 0, 0, 0, nan, nan, None, 0)
    primero = futuros.iloc[0]
    residual = float(primero["residual"])                      # VR
    corrido = residual * primero["tasa"] * yearfrac_30360(primero["inicio"], liq)
    vt = residual + corrido
    flujos = [(liq, -precio)] + list(zip(futuros["fecha"], futuros["total"]))
    tir = xirr(flujos)
    pv = [(yearfrac_365(liq, d), v / (1 + tir) ** yearfrac_365(liq, d)) for d, v in flujos[1:]]
    dur = sum(t * x for t, x in pv) / sum(x for _, x in pv)
    limite = date(liq.year + 1, liq.month, min(liq.day, 28))
    cup12 = futuros[futuros["fecha"] <= limite]["interes"].sum()
    precio_limpio_res = (precio - corrido) / residual * 100
    cy = (cup12 / residual * 100) / precio_limpio_res
    return ResultadoUSD(
        tir=tir, tna=tna_180_360(tir), duration=dur, duration_mod=dur / (1 + tir),
        residual=residual, interes_corrido=corrido, valor_tecnico=vt,
        paridad=precio / vt, current_yield=cy, proximo_pago=primero["fecha"],
        flujo_a_cobrar=float(futuros["total"].sum()),
    )
