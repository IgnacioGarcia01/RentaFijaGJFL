"""Universo de bonos y sus condiciones de emisión.

Todo lo que es "dato del bono" vive acá: vencimientos, pagos finales,
márgenes, CER base, cronogramas de cupón y amortización, y los precios de
ejemplo que se usan cuando data912 no devuelve cotización.

Fuentes de las condiciones:
- [Excel] planilla "Soberanos_por_asset_class.xlsx" (hojas de cada bono).
- [1816] indicadores de 1816 con precios BYMA del 30/10/2026 (liq. 02/11/2026).
Ver `tests/` para la verificación contra ambas.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

# Fecha de referencia de los precios de ejemplo (liquidación T+1 del 30/10/2026).
FECHA_REF = date(2026, 11, 2)


@dataclass(frozen=True)
class BonoPesos:
    ticker: str
    tipo: str              # "Tasa fija" | "CER" | "TAMAR" | "Dólar linked"
    nombre: str
    vencimiento: date
    precio_ejemplo: float  # cierre 30/10/2026 (BYMA, vía 1816)
    emision: date | None = None
    # Tasa fija: pago final por 100 VN = 100 x (1+TEM)^(DAYS360(emisión, vto)/30)
    pago_final: float | None = None
    # CER: CER de 10 hábiles antes de la emisión
    cer_base: float | None = None
    # TAMAR: margen sobre el promedio de TAMAR (TNA)
    margen: float | None = None
    # Dólar linked: A3500 implícito en el precio de ejemplo (respaldo)
    a3500_ref: float | None = None


@dataclass(frozen=True)
class BonoUSD:
    ticker: str            # ticker en pesos (el de USD MEP es ticker + "D")
    familia: str           # "Bonares" | "Globales" | "BOPREAL" | "Provinciales"
    nombre: str
    vencimiento: date
    emision: date
    # Fechas de pago explícitas (sin la emisión), en orden.
    fechas: tuple[date, ...]
    # Cupón: (desde, tasa anual): la tasa vale para los períodos que terminan después de "desde".
    cupones: tuple[tuple[date, float], ...]
    # Amortizaciones: (fecha, % del VN original)
    amortizaciones: tuple[tuple[date, float], ...]
    precio_ejemplo: float  # USD por 100 VN original (precio sucio, como cotiza)
    ticker_d: str | None = None
    nota: str = ""

    @property
    def simbolos_usd(self) -> list[str]:
        """Símbolos posibles de la especie en USD MEP en data912/BYMA."""
        t = self.ticker
        cands = [self.ticker_d, f"{t}D"]
        if t.startswith("BPO"):            # BOPREAL: BPOC7 -> BPC7D
            cands.append(f"BP{t[3:]}D")
        if len(t) == 5:                    # SJO35 -> SJ35D
            cands.append(f"{t[:2]}{t[3:]}D")
        return [c for i, c in enumerate(cands) if c and c not in cands[:i]]


# ---------------------------------------------------------------------------
# Bonos en pesos
# ---------------------------------------------------------------------------

BONOS_PESOS: list[BonoPesos] = [
    # Tasa fija (LECAP / BONCAP). Pago final [1816]; T31Y7 = 100 x 1,024^(DAYS360/30) [Excel].
    BonoPesos("S26F7", "Tasa fija", "LECAP Feb-27", date(2027, 2, 26), 100.19, pago_final=108.368),
    BonoPesos("T31Y7", "Tasa fija", "BONCAP May-27", date(2027, 5, 31), 130.75,
              emision=date(2025, 12, 15), pago_final=151.5628),
    BonoPesos("T30J7", "Tasa fija", "BONCAP Jun-27", date(2027, 6, 30), 131.60, pago_final=156.037),
    # CER: paga 100 x CER[vto - 10 hábiles] / CER base.
    # TZXY7: CER base del Excel. TZX27: CER del 18/01/2024 (consistente con el VT de 1816).
    BonoPesos("TZXY7", "CER", "BONCER May-27", date(2027, 5, 31), 127.40,
              emision=date(2025, 12, 15), cer_base=659.6789),
    BonoPesos("TZX27", "CER", "BONCER Jun-27", date(2027, 6, 30), 417.00, cer_base=200.388),
    # TAMAR: paga 100 x (1+TEM)^(DAYS360(emisión, vto)/30), con
    # TEM = ((1 + (prom. TAMAR[emisión-10 .. vto-10] + margen) x 32/365)^(365/32))^(1/12) - 1  [Excel]
    # TML27: margen 5,4% [Excel]. TMF27: 6,5% calibrado contra 1816 con la misma fórmula.
    BonoPesos("TMF27", "TAMAR", "Bono TAMAR Feb-27", date(2027, 2, 26), 127.20,
              emision=date(2026, 2, 13), margen=0.065),
    BonoPesos("TML27", "TAMAR", "Bono TAMAR Jul-27", date(2027, 7, 30), 112.60,
              emision=date(2026, 6, 30), margen=0.054),
    # Dólar linked: paga 100 x A3500 (3 hábiles antes del vencimiento). Cotiza en $ por 100 VN USD.
    BonoPesos("TZV27", "Dólar linked", "Bono DL Jun-27", date(2027, 6, 30), 151000.0, a3500_ref=1537.3487),
]

# Respaldo si la API del BCRA no responde: TAMAR implícita en 1816 (30/10/2026).
TAMAR_REF = 0.2422


# ---------------------------------------------------------------------------
# Bonos en dólares
# ---------------------------------------------------------------------------

def _semestrales(desde: tuple[int, int], hasta: date, meses: tuple[int, int], dia: int | None) -> tuple[date, ...]:
    """Fechas semestrales; dia=None -> último día del mes."""
    out = []
    y = desde[0]
    while y <= hasta.year:
        for m in meses:
            d = date(y, m, dia or calendar.monthrange(y, m)[1])
            if (y, m) >= desde and d <= hasta:
                out.append(d)
        y += 1
    return tuple(out)


def _amort_iguales(fechas: tuple[date, ...], desde: date, cuotas: int) -> tuple[tuple[date, float], ...]:
    sel = [d for d in fechas if d >= desde][:cuotas]
    return tuple((d, 100.0 / cuotas) for d in sel)


# Step-ups de la reestructuración 2020 (iguales para Bonares y Globales). [Excel AL35D / 1816]
_CUP_29 = ((date(2020, 9, 4), 0.01),)
_CUP_30 = ((date(2020, 9, 4), 0.00125), (date(2021, 7, 9), 0.005),
           (date(2023, 7, 9), 0.0075), (date(2027, 7, 9), 0.0175))
_CUP_35 = ((date(2020, 9, 4), 0.00125), (date(2021, 7, 9), 0.01125),
           (date(2022, 7, 9), 0.015), (date(2023, 7, 9), 0.03625),
           (date(2024, 7, 9), 0.04125), (date(2027, 7, 9), 0.0475),
           (date(2028, 7, 9), 0.05))
_CUP_38 = ((date(2020, 9, 4), 0.00125), (date(2021, 7, 9), 0.02),
           (date(2022, 7, 9), 0.03875), (date(2023, 7, 9), 0.0425),
           (date(2024, 7, 9), 0.05))
_CUP_41 = ((date(2020, 9, 4), 0.00125), (date(2021, 7, 9), 0.025),
           (date(2022, 7, 9), 0.035), (date(2029, 7, 9), 0.04875))
_CUP_46 = ((date(2020, 9, 4), 0.00125), (date(2021, 7, 9), 0.01125),
           (date(2022, 7, 9), 0.015), (date(2023, 7, 9), 0.03625),
           (date(2024, 7, 9), 0.04125), (date(2027, 7, 9), 0.04375),
           (date(2028, 7, 9), 0.05))

_EMI_20 = date(2020, 9, 4)


def _sob(ticker, familia, nombre, vto, cupones, amort_desde, cuotas, precio, extra=()):
    fechas = _semestrales((2021, 1), vto, (1, 7), 9)
    amort = tuple(extra) + _amort_iguales(fechas, amort_desde, cuotas)
    return BonoUSD(ticker, familia, nombre, vto, _EMI_20, fechas, cupones, amort, precio)


# AL30/GD30: 4% en jul-24 y 12 cuotas de 8% desde ene-25.
def _al30(ticker, familia, nombre, precio):
    vto = date(2030, 7, 9)
    fechas = _semestrales((2021, 1), vto, (1, 7), 9)
    amort = ((date(2024, 7, 9), 4.0),) + tuple((d, 8.0) for d in fechas if d >= date(2025, 1, 9))
    return BonoUSD(ticker, familia, nombre, vto, _EMI_20, fechas, _CUP_30, amort, precio)


# Bonar 2028 (AO28): cupón 6% mensual, bullet 31/10/2028. Fechas de pago del Excel.
_AO28_FECHAS = tuple(date.fromisoformat(d) for d in (
    "2026-04-30 2026-05-29 2026-06-30 2026-07-31 2026-08-31 2026-09-30 2026-10-30 2026-11-30 "
    "2026-12-30 2027-01-29 2027-02-26 2027-03-31 2027-04-30 2027-05-31 2027-06-30 2027-07-30 "
    "2027-08-31 2027-09-30 2027-10-29 2027-11-30 2027-12-30 2028-01-31 2028-02-29 2028-03-31 "
    "2028-04-28 2028-05-31 2028-06-30 2028-07-31 2028-08-31 2028-09-29 2028-10-31").split())

_BOP_S1 = _semestrales((2024, 10), date(2027, 10, 31), (4, 10), None)

BONOS_USD: list[BonoUSD] = [
    _sob("AL29", "Bonares", "Bonar 2029", date(2029, 7, 9), _CUP_29, date(2025, 1, 9), 10, 53.85),
    _al30("AL30", "Bonares", "Bonar 2030", 53.45),
    _sob("AL35", "Bonares", "Bonar 2035", date(2035, 7, 9), _CUP_35, date(2031, 1, 9), 10, 67.45),
    _sob("AE38", "Bonares", "Bonar 2038", date(2038, 1, 9), _CUP_38, date(2027, 7, 9), 22, 70.85),
    _sob("AL41", "Bonares", "Bonar 2041", date(2041, 7, 9), _CUP_41, date(2028, 1, 9), 28, 62.99),
    BonoUSD("AO28", "Bonares", "Bonar 2028", date(2028, 10, 31), date(2026, 3, 31), _AO28_FECHAS,
            ((date(2026, 3, 31), 0.06),), ((date(2028, 10, 31), 100.0),), 93.55,
            nota="Precio de ejemplo del Excel (sin precio en 1816)."),
    _sob("GD29", "Globales", "Global 2029", date(2029, 7, 9), _CUP_29, date(2025, 1, 9), 10, 55.04),
    _al30("GD30", "Globales", "Global 2030", 55.53),
    _sob("GD35", "Globales", "Global 2035", date(2035, 7, 9), _CUP_35, date(2031, 1, 9), 10, 71.73),
    _sob("GD38", "Globales", "Global 2038", date(2038, 1, 9), _CUP_38, date(2027, 7, 9), 22, 77.90),
    _sob("GD41", "Globales", "Global 2041", date(2041, 7, 9), _CUP_41, date(2028, 1, 9), 28, 67.28),
    _sob("GD46", "Globales", "Global 2046", date(2046, 7, 9), _CUP_46, date(2025, 1, 9), 44, 61.07),
    # BOPREAL Serie 1: 5% semestral (30/04 y 31/10). A, B y D amortizan 50% el
    # 30/04/2027 y 50% el 31/10/2027 [1816]. El strip C se valúa a su rescate
    # anticipado del 30/04/2027, como en el Excel.
    *[
        BonoUSD(t, "BOPREAL", f"BOPREAL S1 strip {t[3]}", date(2027, 10, 31), date(2024, 1, 5), _BOP_S1,
                ((date(2024, 1, 5), 0.05),), ((date(2027, 4, 30), 50.0), (date(2027, 10, 31), 50.0)), p)
        for t, p in (("BPOA7", 102.10), ("BPOB7", 102.40), ("BPOD7", 102.65))
    ],
    BonoUSD("BPOC7", "BOPREAL", "BOPREAL S1 strip C", date(2027, 4, 30), date(2024, 1, 5),
            tuple(d for d in _BOP_S1 if d <= date(2027, 4, 30)),
            ((date(2024, 1, 5), 0.05),), ((date(2027, 4, 30), 100.0),), 102.40,
            nota="Valuado al rescate anticipado 30/04/2027 (como el Excel)."),
    # BOPREAL Serie 4: 3% semestral, bullet 31/10/2028.
    BonoUSD("BPO28", "BOPREAL", "BOPREAL S4", date(2028, 10, 31), date(2025, 6, 24),
            _semestrales((2025, 10), date(2028, 10, 31), (4, 10), None),
            ((date(2025, 6, 24), 0.03),), ((date(2028, 10, 31), 100.0),), 96.00,
            nota="Precio de ejemplo ilustrativo (sin cotización de referencia)."),
    # Provincia de San Juan 2035: 9,55% semestral (1/4 y 1/10), amortiza 1/3 en oct-33, oct-34 y oct-35. [Excel]
    BonoUSD("SJO35", "Provinciales", "San Juan 2035", date(2035, 10, 1), date(2026, 10, 1),
            _semestrales((2027, 4), date(2035, 10, 1), (4, 10), 1),
            ((date(2026, 10, 1), 0.0955),),
            ((date(2033, 10, 1), 100 / 3), (date(2034, 10, 1), 100 / 3), (date(2035, 10, 1), 100 / 3)),
            102.35, nota="Si data912 no lo trae se usa el precio fijo de ejemplo (30/10/2026)."),
]
