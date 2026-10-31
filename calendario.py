"""Calendario de días hábiles de Argentina (mercado local).

Los feriados están cargados a mano. Si cambia alguno (los "puente" se
definen año a año) basta con editar FERIADOS.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")

FERIADOS: set[date] = {
    # 2026
    date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 3, 23),
    date(2026, 3, 24), date(2026, 4, 2), date(2026, 4, 3), date(2026, 5, 1),
    date(2026, 5, 25), date(2026, 6, 15), date(2026, 7, 9), date(2026, 7, 10),
    date(2026, 8, 17), date(2026, 10, 12), date(2026, 11, 23), date(2026, 12, 7),
    date(2026, 12, 8), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 2, 8), date(2027, 2, 9), date(2027, 3, 24),
    date(2027, 3, 25), date(2027, 3, 26), date(2027, 4, 2), date(2027, 5, 1),
    date(2027, 5, 25), date(2027, 6, 14), date(2027, 6, 20), date(2027, 7, 9),
    date(2027, 8, 16), date(2027, 10, 11), date(2027, 11, 22), date(2027, 12, 8),
    date(2027, 12, 25),
    # 2028
    date(2028, 1, 1), date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 24),
    date(2028, 4, 13), date(2028, 4, 14), date(2028, 5, 1), date(2028, 5, 25),
    date(2028, 6, 17), date(2028, 6, 20), date(2028, 7, 9), date(2028, 8, 17),
    date(2028, 10, 12), date(2028, 11, 20), date(2028, 12, 8), date(2028, 12, 25),
}


def es_habil(d: date) -> bool:
    return d.weekday() < 5 and d not in FERIADOS


def sumar_habiles(d: date, n: int) -> date:
    """Suma (o resta, si n < 0) n días hábiles."""
    paso = 1 if n >= 0 else -1
    restantes = abs(n)
    while restantes:
        d += timedelta(days=paso)
        if es_habil(d):
            restantes -= 1
    return d


def habil_anterior_o_igual(d: date) -> date:
    while not es_habil(d):
        d -= timedelta(days=1)
    return d


def ahora_ar() -> datetime:
    return datetime.now(TZ_AR)


def hoy_ar() -> date:
    return ahora_ar().date()


def mercado_abierto(ahora: datetime | None = None) -> bool:
    """Rueda de BYMA: 11:00 a 17:00 en días hábiles."""
    ahora = ahora or ahora_ar()
    if not es_habil(ahora.date()):
        return False
    minutos = ahora.hour * 60 + ahora.minute
    return 11 * 60 <= minutos < 17 * 60


def fecha_operacion(ahora: datetime | None = None) -> date:
    """Última rueda con precios: hoy si es hábil, si no el hábil anterior."""
    ahora = ahora or ahora_ar()
    return habil_anterior_o_igual(ahora.date())


def fecha_liquidacion(fecha_op: date, plazo: int = 1) -> date:
    """Liquidación T+plazo hábiles (24 hs por defecto, como 1816)."""
    return sumar_habiles(fecha_op, plazo) if plazo else fecha_op
