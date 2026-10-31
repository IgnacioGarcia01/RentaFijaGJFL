"""Datos de mercado: precios (data912) y variables del BCRA.

Todas las funciones devuelven datos "limpios" y nunca levantan excepción: si
una fuente falla se devuelve vacío y la app cae al valor de respaldo, avisando
en pantalla de dónde salió cada dato.
"""

from __future__ import annotations

import re
import warnings
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

TIMEOUT = 15
UA = {"User-Agent": "Mozilla/5.0 (bonos-soberanos streamlit)"}

# ---------------------------------------------------------------------------
# data912
# ---------------------------------------------------------------------------

DATA912 = "https://data912.com"
DATA912_LIVE = [
    "/live/arg_notes",         # LECAP / BONCAP
    "/live/arg_bonds",         # Soberanos (ARS y USD: AL30 / AL30D)
    "/live/arg_cer",           # Bonos CER
    "/live/arg_dollar_bonds",  # Dólar linked / duales
    "/live/arg_corp",          # ON / provinciales
]


def _get_json(url: str, **kw) -> Any:
    r = requests.get(url, timeout=TIMEOUT, headers=UA, **kw)
    r.raise_for_status()
    return r.json()


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def precios_data912() -> tuple[dict[str, dict], list[str]]:
    """Cotizaciones en vivo de data912.

    Devuelve ({SIMBOLO: {"precio", "var", "bid", "ask", "volumen", "endpoint"}}, errores).
    `precio` es el último operado ('c'); fuera de rueda data912 deja el cierre.
    """
    out: dict[str, dict] = {}
    errores: list[str] = []
    for ep in DATA912_LIVE:
        try:
            filas = _get_json(DATA912 + ep)
        except Exception as e:  # noqa: BLE001
            errores.append(f"{ep}: {type(e).__name__}")
            continue
        if isinstance(filas, dict):
            filas = filas.get("data", [])
        for f in filas or []:
            if not isinstance(f, dict):
                continue
            sim = str(f.get("symbol") or f.get("ticker") or "").upper().strip()
            if not sim or sim in out:
                continue
            bid, ask = _num(f.get("px_bid")), _num(f.get("px_ask"))
            precio = _num(f.get("c")) or _num(f.get("close")) or _num(f.get("last"))
            if precio is None and bid and ask:
                precio = (bid + ask) / 2
            if precio is None:
                continue
            out[sim] = {
                "precio": precio,
                "var": f.get("pct_change"),
                "bid": bid,
                "ask": ask,
                "volumen": f.get("v"),
                "endpoint": ep,
            }
    return out, errores


def cierre_historico_data912(simbolo: str) -> tuple[float, date] | None:
    """Último cierre disponible en el histórico de data912 (respaldo)."""
    for ruta in ("/historical/bonds/", "/historical/notes/", "/historical/corp/"):
        try:
            filas = _get_json(f"{DATA912}{ruta}{simbolo}")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(filas, list) or not filas:
            continue
        ult = filas[-1]
        precio = _num(ult.get("c"))
        try:
            fecha = pd.to_datetime(ult.get("date")).date()
        except Exception:  # noqa: BLE001
            fecha = None
        if precio:
            return precio, fecha
    return None


def mep_data912() -> tuple[float | None, str]:
    """Dólar MEP. Prioriza AL30 del endpoint /live/mep."""
    try:
        filas = _get_json(DATA912 + "/live/mep")
    except Exception as e:  # noqa: BLE001
        return None, f"/live/mep: {type(e).__name__}"
    if isinstance(filas, dict):
        filas = filas.get("data", [])
    candidatos = {}
    for f in filas or []:
        t = str(f.get("ticker") or f.get("symbol") or "").upper()
        v = _num(f.get("mark")) or _num(f.get("close"))
        if t and v:
            candidatos[t] = v
    for pref in ("AL30", "GD30", "AL29", "GD29"):
        if pref in candidatos:
            return candidatos[pref], f"data912 /live/mep ({pref})"
    if candidatos:
        s = pd.Series(candidatos)
        return float(s.median()), "data912 /live/mep (mediana)"
    return None, "/live/mep vacío"


# ---------------------------------------------------------------------------
# BCRA - API de Estadísticas Monetarias
# ---------------------------------------------------------------------------

BCRA = "https://api.bcra.gob.ar/estadisticas"
VERSIONES = ("v4.0", "v3.0")

# Identificación por descripción (más robusto que IDs fijos) y IDs de respaldo.
VARIABLES = {
    "cer": (r"\bCER\b|estabilizaci[oó]n", 30),
    "a3500": (r"3500|mayorista", 5),
    "tamar": (r"TAMAR.*privados.*n\.?\s?a", 44),
    "inflacion": (r"inflaci[oó]n mensual", 27),
    "inflacion_rem": (r"inflaci[oó]n esperada", 29),
}


def _bcra_get(url: str, params: dict | None = None) -> Any:
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT, headers=UA)
    except requests.exceptions.SSLError:
        # La API del BCRA suele publicar una cadena de certificados incompleta.
        # Son datos públicos de solo lectura: se reintenta sin verificar.
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        r = requests.get(url, params=params, timeout=TIMEOUT, headers=UA, verify=False)
    r.raise_for_status()
    return r.json()


def bcra_catalogo() -> tuple[pd.DataFrame, str]:
    for v in VERSIONES:
        try:
            js = _bcra_get(f"{BCRA}/{v}/monetarias", {"limit": 3000})
            df = pd.DataFrame(js.get("results", []))
            if not df.empty:
                return df, v
        except Exception:  # noqa: BLE001
            continue
    return pd.DataFrame(), ""


def bcra_ids() -> tuple[dict[str, int], dict[str, str], str]:
    """IDs y descripciones de las variables que usa la app."""
    cat, version = bcra_catalogo()
    ids, desc = {}, {}
    for clave, (patron, id_def) in VARIABLES.items():
        elegido = None
        if not cat.empty and "descripcion" in cat:
            m = cat[cat["descripcion"].astype(str).str.contains(patron, case=False, regex=True)]
            if clave == "tamar" and len(m) > 1:
                # preferir la TAMAR en % n.a. (no la e.a.)
                m2 = m[~m["descripcion"].str.contains(r"e\.\s?a", case=False, regex=True)]
                m = m2 if not m2.empty else m
            if clave == "a3500" and len(m) > 1:
                m2 = m[m["descripcion"].str.contains("3500")]
                m = m2 if not m2.empty else m
            if not m.empty:
                elegido = int(m.iloc[0]["idVariable"])
                desc[clave] = str(m.iloc[0]["descripcion"])
        ids[clave] = elegido or id_def
        desc.setdefault(clave, f"idVariable {ids[clave]}")
    return ids, desc, version


def _parse_serie(js: Any) -> pd.Series:
    res = js.get("results", []) if isinstance(js, dict) else []
    filas = []
    for r in res:
        if isinstance(r, dict) and "detalle" in r:          # v4.0
            filas.extend(r["detalle"])
        elif isinstance(r, dict) and "fecha" in r:          # v3.0
            filas.append(r)
    if not filas:
        return pd.Series(dtype=float)
    df = pd.DataFrame(filas)
    s = pd.Series(df["valor"].astype(float).values, index=pd.to_datetime(df["fecha"]))
    return s[~s.index.duplicated()].sort_index()


def bcra_serie(id_variable: int, desde: date, hasta: date | None = None) -> pd.Series:
    hasta = hasta or (date.today() + timedelta(days=60))  # el CER se publica por adelantado
    partes = []
    for v in VERSIONES:
        try:
            # La API pagina: se piden tramos de 1 año.
            ini = desde
            while ini <= hasta:
                fin = min(hasta, ini + timedelta(days=365))
                js = _bcra_get(
                    f"{BCRA}/{v}/monetarias/{id_variable}",
                    {"desde": ini.isoformat(), "hasta": fin.isoformat(), "limit": 3000},
                )
                partes.append(_parse_serie(js))
                ini = fin + timedelta(days=1)
            s = pd.concat(partes) if partes else pd.Series(dtype=float)
            if not s.empty:
                return s[~s.index.duplicated()].sort_index()
        except Exception:  # noqa: BLE001
            partes = []
            continue
    return pd.Series(dtype=float)


def variables_bcra(desde: date) -> tuple[dict[str, pd.Series], dict[str, str], list[str]]:
    """Trae CER, A3500, TAMAR (decimal), inflación mensual y REM."""
    ids, desc, version = bcra_ids()
    series: dict[str, pd.Series] = {}
    errores: list[str] = []
    for clave, idv in ids.items():
        s = bcra_serie(idv, desde)
        if s.empty:
            errores.append(f"{clave} (id {idv})")
            continue
        if clave in ("tamar", "inflacion", "inflacion_rem"):
            s = s / 100.0  # vienen en %
        series[clave] = s
    if version:
        desc["_version"] = version
    return series, desc, errores
