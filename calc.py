"""Motor de cálculo: flujos, TIR, TNA, TEM, duration y proyecciones.

Convenciones (las mismas que usa 1816):
- TIR / TEA: descuento compuesto (1 + TIR) ^ (días / 365).
- TNA tasa fija: "plazo remanente" -> (pago / precio - 1) * 365 / días.
- TNA CER, dólar linked y bonos en USD: 180/360 -> ((1 + TEA) ^ 0,5 - 1) * 2.
- TNA TAMAR: 32/365 -> ((1 + TEA) ^ (32/365) - 1) * 365 / 32.
- TEM: (1 + TEA) ^ (1/12) - 1.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from bonos import BonoUSD, DIA_FIN_DE_MES
from calendario import es_habil, sumar_habiles


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


def tea_desde_tna_32_365(tna: float) -> float:
    return (1 + tna * 32 / 365) ** (365 / 32) - 1


def tea_desde_tem(tem: float) -> float:
    return (1 + tem) ** 12 - 1


def anualizar(factor: float, dias: int) -> float:
    return factor ** (365 / dias) - 1


# ---------------------------------------------------------------------------
# Series diarias (CER, TAMAR, A3500)
# ---------------------------------------------------------------------------


def serie_diaria(s: pd.Series) -> pd.Series:
    """Ordena, completa los días faltantes con el último dato (ffill)."""
    if s is None or s.empty:
        return s
    s = s.sort_index()
    idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(idx).ffill()


def valor_en(s: pd.Series, d: date) -> float | None:
    """Último valor disponible a la fecha d (inclusive)."""
    if s is None or s.empty:
        return None
    ts = pd.Timestamp(d)
    sub = s.loc[:ts]
    if sub.empty:
        return None
    return float(sub.iloc[-1])


# ---------------------------------------------------------------------------
# CER proyectado
# ---------------------------------------------------------------------------
# El CER del día 16 del mes m al 15 del mes m+1 crece geométricamente a la
# inflación del mes m-1 (rezago de ~1,5 meses respecto del IPC).


def mes_ipc_que_aplica(d: date) -> tuple[int, int]:
    """Mes de IPC que determina la variación diaria del CER en la fecha d."""
    y, m = d.year, d.month
    # Del 16/m al 15/m+1 aplica IPC(m-1). Un día <= 15 pertenece al tramo que
    # empezó el 16 del mes anterior -> IPC(m-2).
    shift = 1 if d.day >= 16 else 2
    m -= shift
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def _dias_tramo(d: date) -> int:
    """Cantidad de días del tramo 16/m - 15/m+1 que contiene a d."""
    if d.day >= 16:
        ini = date(d.year, d.month, 16)
    else:
        pm = date(d.year, d.month, 1) - timedelta(days=1)
        ini = date(pm.year, pm.month, 16)
    ny, nm = (ini.year + (ini.month == 12), ini.month % 12 + 1)
    fin = date(ny, nm, 16)
    return (fin - ini).days


def proyectar_cer(cer_hist: pd.Series, hasta: date, inflacion: dict[tuple[int, int], float],
                  inflacion_default: float) -> pd.Series:
    """Extiende la serie de CER hasta `hasta` usando inflación mensual proyectada.

    inflacion: {(año, mes): inflación mensual decimal}. Si falta un mes se usa
    inflacion_default.
    """
    s = serie_diaria(cer_hist)
    ultimo = s.index.max().date()
    if ultimo >= hasta:
        return s
    valores = {}
    v = float(s.iloc[-1])
    d = ultimo
    while d < hasta:
        d += timedelta(days=1)
        ym = mes_ipc_que_aplica(d)
        pi = inflacion.get(ym, inflacion_default)
        v *= (1 + pi) ** (1 / _dias_tramo(d))
        valores[pd.Timestamp(d)] = v
    return pd.concat([s, pd.Series(valores)])


def meses_ipc_entre(d0: date, d1: date, minimo_dias: int = 15) -> list[tuple[int, int]]:
    """Meses de IPC que el CER incorpora entre las fechas d0 y d1.

    Solo se cuentan los meses de los que entran al menos `minimo_dias` días del
    tramo (p. ej. el CER del 16/06 trae un solo día del IPC de mayo: no cuenta).
    """
    conteo: dict[tuple[int, int], int] = {}
    d = d0 + timedelta(days=1)
    while d <= d1:
        ym = mes_ipc_que_aplica(d)
        conteo[ym] = conteo.get(ym, 0) + 1
        d += timedelta(days=1)
    return [ym for ym, n in conteo.items() if n >= minimo_dias]


# ---------------------------------------------------------------------------
# TAMAR: capitalización diaria
# ---------------------------------------------------------------------------


def factor_tamar(tamar_diaria: pd.Series, desde: date, hasta: date, margen: float,
                 tamar_proy: float, rezago_habiles: int = 10) -> float:
    """Factor de capitalización TAMAR + margen entre `desde` y `hasta`.

    Cada día corrido aplica la TAMAR vigente `rezago_habiles` hábiles antes,
    convertida a tasa diaria con la convención 32/365. Si ese dato todavía no
    existe se usa `tamar_proy`.
    """
    f = 1.0
    d = desde
    ultimo = tamar_diaria.index.max().date() if tamar_diaria is not None and not tamar_diaria.empty else None
    while d < hasta:
        ref = sumar_habiles(d, -rezago_habiles)
        t = valor_en(tamar_diaria, ref) if (ultimo and ref <= ultimo) else None
        if t is None:
            t = tamar_proy
        f *= (1 + (t + margen) * 32 / 365) ** (1 / 32)
        d += timedelta(days=1)
    return f


# ---------------------------------------------------------------------------
# Bonos en USD
# ---------------------------------------------------------------------------


def _dia_pago(b: BonoUSD, y: int, m: int) -> date:
    if b.familia in DIA_FIN_DE_MES:
        return date(y, m, calendar.monthrange(y, m)[1])
    return date(y, m, b.dia_pago)


def fechas_pago(b: BonoUSD) -> list[date]:
    out = []
    y = 2020
    while y <= b.vencimiento.year:
        for m in b.meses_pago:
            d = _dia_pago(b, y, m)
            if d <= b.vencimiento:
                out.append(d)
        y += 1
    if b.vencimiento not in out:
        out.append(b.vencimiento)
    return sorted(out)


def _tasa_cupon(b: BonoUSD, fin_periodo: date) -> float:
    tasa = 0.0
    for desde, t in b.cupones:
        if fin_periodo > desde:
            tasa = t
    return tasa


def dias_30_360(d0: date, d1: date) -> int:
    dd0 = min(d0.day, 30)
    dd1 = min(d1.day, 30) if dd0 == 30 or d1.day < 31 else 30
    if d1.day == 31 and dd0 == 30:
        dd1 = 30
    return (d1.year - d0.year) * 360 + (d1.month - d0.month) * 30 + (dd1 - dd0)


def flujos_usd(b: BonoUSD) -> pd.DataFrame:
    """Cronograma completo por 100 VN original."""
    amort = {d: a for d, a in b.amortizaciones}
    fechas = fechas_pago(b)
    residual = 100.0
    filas = []
    prev = None
    for d in fechas:
        a = amort.get(d, 0.0)
        tasa = _tasa_cupon(b, d)
        if prev is None:
            prev = d
            continue
        interes = residual * tasa * dias_30_360(prev, d) / 360
        filas.append({"fecha": d, "inicio": prev, "residual": residual, "tasa": tasa,
                      "interes": interes, "amortizacion": a, "total": interes + a})
        residual -= a
        prev = d
    return pd.DataFrame(filas)


@dataclass
class ResultadoUSD:
    tir: float
    tna: float
    duration: float
    duration_mod: float
    residual: float
    interes_corrido: float
    valor_tecnico: float
    paridad: float
    current_yield: float
    proximo_pago: date | None


def tir_flujos(precio: float, flujos: list[tuple[date, float]], liq: date) -> float:
    """TIR anual efectiva (act/365) por bisección + Newton."""
    tiempos = [((d - liq).days / 365, v) for d, v in flujos if d > liq]

    def f(y):
        return sum(v / (1 + y) ** t for t, v in tiempos) - precio

    lo, hi = -0.9, 5.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-10:
            break
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def analizar_usd(b: BonoUSD, precio: float, liq: date) -> ResultadoUSD:
    cf = flujos_usd(b)
    futuros = cf[cf["fecha"] > liq]
    if futuros.empty:
        nan = float("nan")
        return ResultadoUSD(nan, nan, nan, nan, 0, 0, 0, nan, nan, None)
    primero = futuros.iloc[0]
    residual = float(primero["residual"])
    corrido = residual * primero["tasa"] * dias_30_360(primero["inicio"], liq) / 360
    vt = residual + corrido
    flujos = [(habil_siguiente_o_igual(d), v) for d, v in zip(futuros["fecha"], futuros["total"])]
    tir = tir_flujos(precio, flujos, liq)
    pv = [(((d - liq).days / 365), v / (1 + tir) ** ((d - liq).days / 365)) for d, v in flujos]
    dur = sum(t * x for t, x in pv) / sum(x for _, x in pv)
    # Current yield: cupones de los próximos 12 meses / precio limpio por residual
    limite = date(liq.year + 1, liq.month, min(liq.day, 28))
    cup12 = futuros[futuros["fecha"] <= limite]["interes"].sum()
    precio_limpio_res = (precio - corrido) / residual * 100 if residual else float("nan")
    cy = (cup12 / residual * 100) / precio_limpio_res if residual else float("nan")
    return ResultadoUSD(
        tir=tir, tna=tna_180_360(tir), duration=dur, duration_mod=dur / (1 + tir),
        residual=residual, interes_corrido=corrido, valor_tecnico=vt,
        paridad=precio / vt, current_yield=cy, proximo_pago=primero["fecha"],
    )
