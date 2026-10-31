"""Universo de bonos y sus condiciones de emisión.

Todo lo que es "dato del bono" vive acá: vencimientos, pagos finales,
márgenes, cronogramas de cupón y amortización, y los precios de ejemplo que
se usan cuando data912 no devuelve cotización.

Calibración: los valores marcados con [1816] se verificaron contra la API de
1816 con precios de BYMA del 30/10/2026 (liquidación 02/11/2026). Ver
`tests/test_calibracion.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Fecha de referencia de la calibración (liquidación T+1 del 30/10/2026).
FECHA_REF = date(2026, 11, 2)


@dataclass(frozen=True)
class BonoPesos:
    ticker: str
    tipo: str              # "Tasa fija" | "CER" | "TAMAR" | "Dólar linked"
    nombre: str
    vencimiento: date
    precio_ejemplo: float  # cierre 30/10/2026 (BYMA, vía 1816)
    # Tasa fija: pago final por 100 VN (capital + intereses capitalizados)
    pago_final: float | None = None
    # CER: valor técnico (100 x CER[t-10]/CER base) a FECHA_REF  [1816]
    vt_ref: float | None = None
    # TAMAR: margen anual (TNA, conv. 32/365) sobre TAMAR bancos privados
    margen: float | None = None
    # Dólar linked: A3500 implícito en el precio de referencia (t-3 hábiles)
    a3500_ref: float | None = None


@dataclass(frozen=True)
class BonoUSD:
    ticker: str            # ticker en pesos (el de USD MEP es ticker + "D")
    familia: str           # "Bonares" | "Globales" | "BOPREAL" | "Provinciales"
    nombre: str
    vencimiento: date
    # Fechas de pago teóricas (mes, día) y primer/último año
    meses_pago: tuple[int, int]
    dia_pago: int
    # Cupón: lista de (desde_inclusive, tasa anual) — tasa vigente para el
    # período que TERMINA después de "desde".
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
        if t.startswith("BPO"):            # BOPREAL: BPOA7 -> BPA7D
            cands.append(f"BP{t[3:]}D")
        if len(t) == 5:                    # SJO35 -> SJ35D
            cands.append(f"{t[:2]}{t[3:]}D")
        return [c for i, c in enumerate(cands) if c and c not in cands[:i]]


# ---------------------------------------------------------------------------
# Bonos en pesos
# ---------------------------------------------------------------------------

BONOS_PESOS: list[BonoPesos] = [
    # Tasa fija (LECAP / BONCAP): pago final por 100 VN [1816]
    BonoPesos("S26F7", "Tasa fija", "LECAP Feb-27", date(2027, 2, 26),
              precio_ejemplo=100.19, pago_final=108.368),
    BonoPesos("T31Y7", "Tasa fija", "BONCAP May-27", date(2027, 5, 31),
              precio_ejemplo=130.75, pago_final=151.563),
    BonoPesos("T30J7", "Tasa fija", "BONCAP Jun-27", date(2027, 6, 30),
              precio_ejemplo=131.60, pago_final=156.037),
    # CER (BONCER cupón cero): VT = 100 x CER[t-10 hábiles] / CER base [1816]
    BonoPesos("TZXY7", "CER", "BONCER May-27", date(2027, 5, 31),
              precio_ejemplo=127.40, vt_ref=129.84),
    BonoPesos("TZX27", "CER", "BONCER Jun-27", date(2027, 6, 30),
              precio_ejemplo=417.00, vt_ref=427.44),
    # TAMAR: capitaliza TAMAR bancos privados (t-10 hábiles) + margen.
    # vt_ref = valor técnico a FECHA_REF [1816]. Márgenes: TML27 3,63% (licitación);
    # TMF27 calibrado para replicar la TIR de 1816 con la misma TAMAR.
    BonoPesos("TMF27", "TAMAR", "Bono TAMAR Feb-27", date(2027, 2, 26),
              precio_ejemplo=127.20, vt_ref=125.01934706624738, margen=0.0457),
    BonoPesos("TML27", "TAMAR", "Bono TAMAR Jul-27", date(2027, 7, 30),
              precio_ejemplo=112.60, vt_ref=110.17144261033829, margen=0.0363),
    # Dólar linked: paga 100 x A3500 (t-3 hábiles antes del vencimiento).
    # Cotiza en pesos por 100 VN en USD.
    BonoPesos("TZV27", "Dólar linked", "Bono DL Jun-27", date(2027, 6, 30),
              precio_ejemplo=151000.0, a3500_ref=1537.3487),
]

# TAMAR implícita en las TIR de 1816 del 30/10/2026 (TNA, conv. 32/365). Se usa
# solo si la API del BCRA no responde.
TAMAR_REF = 0.2614
# Para el CER, si el BCRA no responde se normaliza CER[t-10 de FECHA_REF] = 1.
CER_REF_FALLBACK = 1.0


# ---------------------------------------------------------------------------
# Bonos en dólares
# ---------------------------------------------------------------------------

def _amort_iguales(desde: date, cuotas: int, meses: int = 6) -> tuple[tuple[date, float], ...]:
    out = []
    y, m = desde.year, desde.month
    for _ in range(cuotas):
        out.append((date(y, m, desde.day), 100.0 / cuotas))
        m += meses
        while m > 12:
            m -= 12
            y += 1
    return tuple(out)


# Step-ups de la reestructuración 2020 (iguales para Bonares y Globales).
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

_AM_29 = _amort_iguales(date(2025, 1, 9), 10)
_AM_30 = ((date(2024, 7, 9), 4.0),) + tuple((d, 8.0) for d, _ in _amort_iguales(date(2025, 1, 9), 12))
_AM_35 = _amort_iguales(date(2031, 1, 9), 10)
_AM_38 = _amort_iguales(date(2027, 7, 9), 22)
_AM_41 = _amort_iguales(date(2028, 1, 9), 28)
_AM_46 = _amort_iguales(date(2025, 1, 9), 44)

_SOB = dict(meses_pago=(1, 7), dia_pago=9)

BONOS_USD: list[BonoUSD] = [
    BonoUSD("AL29", "Bonares", "Bonar 2029", date(2029, 7, 9), cupones=_CUP_29, amortizaciones=_AM_29, precio_ejemplo=53.85, **_SOB),
    BonoUSD("AL30", "Bonares", "Bonar 2030", date(2030, 7, 9), cupones=_CUP_30, amortizaciones=_AM_30, precio_ejemplo=53.45, **_SOB),
    BonoUSD("AL35", "Bonares", "Bonar 2035", date(2035, 7, 9), cupones=_CUP_35, amortizaciones=_AM_35, precio_ejemplo=67.45, **_SOB),
    BonoUSD("AE38", "Bonares", "Bonar 2038", date(2038, 1, 9), cupones=_CUP_38, amortizaciones=_AM_38, precio_ejemplo=70.85, **_SOB),
    BonoUSD("AL41", "Bonares", "Bonar 2041", date(2041, 7, 9), cupones=_CUP_41, amortizaciones=_AM_41, precio_ejemplo=62.99, **_SOB),
    BonoUSD("GD29", "Globales", "Global 2029", date(2029, 7, 9), cupones=_CUP_29, amortizaciones=_AM_29, precio_ejemplo=55.04, **_SOB),
    BonoUSD("GD30", "Globales", "Global 2030", date(2030, 7, 9), cupones=_CUP_30, amortizaciones=_AM_30, precio_ejemplo=55.53, **_SOB),
    BonoUSD("GD35", "Globales", "Global 2035", date(2035, 7, 9), cupones=_CUP_35, amortizaciones=_AM_35, precio_ejemplo=71.73, **_SOB),
    BonoUSD("GD38", "Globales", "Global 2038", date(2038, 1, 9), cupones=_CUP_38, amortizaciones=_AM_38, precio_ejemplo=77.90, **_SOB),
    BonoUSD("GD41", "Globales", "Global 2041", date(2041, 7, 9), cupones=_CUP_41, amortizaciones=_AM_41, precio_ejemplo=67.28, **_SOB),
    BonoUSD("GD46", "Globales", "Global 2046", date(2046, 7, 9), cupones=_CUP_46, amortizaciones=_AM_46, precio_ejemplo=61.07, **_SOB),
    # BOPREAL Serie 1 (A-D): 5% semestral (abr/oct), amortiza 50% el 30/04/2027
    # y 50% el 31/10/2027 [1816: paridad y duration]. Las
    # series A-C admiten rescate anticipado para pago de impuestos; se valúan a vencimiento.
    *[
        BonoUSD(t, "BOPREAL", f"BOPREAL S1{t[-2]}", date(2027, 10, 31), meses_pago=(4, 10), dia_pago=30,
                cupones=((date(2024, 1, 5), 0.05),),
                amortizaciones=((date(2027, 4, 30), 50.0), (date(2027, 10, 31), 50.0)),
                precio_ejemplo=p)
        for t, p in (("BPOA7", 102.10), ("BPOB7", 102.40), ("BPOC7", 102.40), ("BPOD7", 102.65))
    ],
    # BOPREAL Serie 4: 3% semestral, bullet 31/10/2028. 1816 no tenía precio
    # el 30/10/2026: el precio de ejemplo es ilustrativo.
    BonoUSD("BPO28", "BOPREAL", "BOPREAL S4", date(2028, 10, 31), meses_pago=(4, 10), dia_pago=30,
            cupones=((date(2025, 6, 24), 0.03),), amortizaciones=((date(2028, 10, 31), 100.0),),
            precio_ejemplo=96.00,
            nota="Precio de ejemplo ilustrativo (sin cotización de referencia)."),
    # Provincia de San Juan 2035: cupón 9,55% semestral (1/4 y 1/10), amortiza
    # en 3 cuotas anuales iguales (oct-33/34/35). Condiciones inferidas del
    # cupón corrido, current yield y duration que informa 1816.
    BonoUSD("SJO35", "Provinciales", "San Juan 2035", date(2035, 10, 1), meses_pago=(4, 10), dia_pago=1,
            cupones=((date(2026, 10, 1), 0.0955),),
            amortizaciones=((date(2033, 10, 1), 100 / 3), (date(2034, 10, 1), 100 / 3), (date(2035, 10, 1), 100 / 3)),
            precio_ejemplo=102.35,
            nota="Condiciones inferidas; precio fijo de ejemplo si data912 no lo trae."),
]

# Para BOPREAL el día de pago es el último del mes (30/04 y 31/10).
DIA_FIN_DE_MES = {"BOPREAL"}
