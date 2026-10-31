"""Parseo de respuestas de data912 y BCRA con payloads simulados (sin red)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import datos


def test_data912(monkeypatch):
    filas = [{"symbol": "AL30D", "px_bid": 53.4, "px_ask": 53.5, "c": 53.45, "pct_change": 0.3, "v": 1},
             {"symbol": "TZX27", "px_bid": 0, "px_ask": 0, "c": 0},
             {"symbol": "S26F7", "px_bid": 100.1, "px_ask": 100.3, "c": None}]
    monkeypatch.setattr(datos, "_get_json", lambda url, **kw: filas)
    precios, errores = datos.precios_data912()
    assert precios["AL30D"]["precio"] == 53.45
    assert "TZX27" not in precios
    assert abs(precios["S26F7"]["precio"] - 100.2) < 1e-9


def test_mep(monkeypatch):
    monkeypatch.setattr(datos, "_get_json", lambda url, **kw: [
        {"ticker": "GD30", "mark": 1560.0}, {"ticker": "AL30", "mark": 1573.6}])
    assert datos.mep_data912()[0] == 1573.6


def test_bcra_v4_y_v3():
    v4 = {"results": [{"idVariable": 30, "detalle": [{"fecha": "2026-10-02", "valor": 850.1},
                                                       {"fecha": "2026-10-01", "valor": 849.0}]}]}
    v3 = {"results": [{"idVariable": 30, "fecha": "2026-10-01", "valor": 849.0}]}
    s4, s3 = datos._parse_serie(v4), datos._parse_serie(v3)
    assert list(s4.values) == [849.0, 850.1]
    assert s3.iloc[0] == 849.0


def test_bcra_ids(monkeypatch):
    import pandas as pd
    cat = pd.DataFrame([
        {"idVariable": 30, "descripcion": "CER (Base 2.2.2002=1)"},
        {"idVariable": 5, "descripcion": "Tipo de Cambio Mayorista ($ por US$) Comunicación A 3500 - Referencia"},
        {"idVariable": 44, "descripcion": "TAMAR en pesos de bancos privados (en % n.a.)"},
        {"idVariable": 45, "descripcion": "TAMAR en pesos de bancos privados (en % e.a.)"},
        {"idVariable": 27, "descripcion": "Inflación mensual (variación en %)"},
        {"idVariable": 29, "descripcion": "Inflación esperada - REM próximos 12 meses - MEDIANA (variación en % i.a)"},
    ])
    monkeypatch.setattr(datos, "bcra_catalogo", lambda: (cat, "v4.0"))
    ids, desc, _ = datos.bcra_ids()
    assert ids == {"cer": 30, "a3500": 5, "tamar": 44, "inflacion": 27, "inflacion_rem": 29}
