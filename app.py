"""Monitor de Renta Fija Soberana - Argentina.

Solapas:
1. Resumen: precios, TIR, TNA, TEM, MD de bonos en pesos y en dólares, con los
   parámetros de la planilla (shock de precio, proyección de TAMAR e inflación)
   y la tabla de sensibilidad de TIR al precio.
2. Escenarios: inflación (CER), TAMAR, A3500 y dólar MEP proyectados.
3. Expectativas de mercado: breakevens entre pares de bonos.

Precios: data912.com. Variables: API de Estadísticas Monetarias del BCRA.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from bonos import BONOS_PESOS, BONOS_USD, BonoPesos, BonoUSD
from calc import analizar_usd, flujos_usd, mes_ipc_que_aplica, valor_en, yearfrac_365
from calendario import ahora_ar, fecha_liquidacion, fecha_operacion, mercado_abierto
from datos import cierre_historico_data912, mep_data912, precios_data912, variables_bcra
from valuacion import (
    Supuestos, a3500_en, armar_mercado, be_dolar, be_inflacion, be_tamar, cer_proyectado, fmt_fecha,
    indicadores_pesos, nombre_mes, pago_final, par_mas_cercano, proyectar_mensual, tamar_proyectada,
    tasa_real_be, tcr_esperado,
)

st.set_page_config(page_title="Renta Fija Soberana", page_icon="📈", layout="wide")

MEP_RESPALDO = 1573.6  # AL30 $ / AL30D (1816, 30/10/2026)
PCT = "%.2f%%"
SHOCKS = [-0.05, -0.02, -0.01, -0.005, 0.0, 0.005, 0.01, 0.02, 0.05]


# ---------------------------------------------------------------------------
# Carga de datos (cacheada; el botón "Actualizar" limpia el cache)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def cargar_precios():
    precios, errores = precios_data912()
    mep, fuente_mep = mep_data912()
    return precios, errores, mep, fuente_mep, ahora_ar()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cargar_historico(simbolo: str):
    return cierre_historico_data912(simbolo)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cargar_bcra(desde: date):
    return variables_bcra(desde)


def _normalizar_escala(precio: float, referencia: float) -> float:
    """data912 a veces cotiza por 1 VN en lugar de por 100 VN (o al revés)."""
    r = precio / referencia
    if 30 < r < 300:
        return precio / 100
    if 1 / 300 < r < 1 / 30:
        return precio * 100
    return precio


def precio_pesos(b: BonoPesos, precios: dict) -> tuple[float, float | None, str]:
    p = precios.get(b.ticker)
    if p:
        return _normalizar_escala(p["precio"], b.precio_ejemplo), p.get("var"), "data912"
    h = cargar_historico(b.ticker)
    if h:
        return _normalizar_escala(h[0], b.precio_ejemplo), None, f"data912 cierre {h[1] or ''}"
    return b.precio_ejemplo, None, "ejemplo fijo (30/10/2026)"


def precio_usd(b: BonoUSD, precios: dict, mep: float) -> tuple[float, float | None, str]:
    for s in b.simbolos_usd:
        p = precios.get(s)
        if p:
            return _normalizar_escala(p["precio"], b.precio_ejemplo), p.get("var"), f"data912 ({s})"
    p = precios.get(b.ticker)  # especie en pesos / MEP
    if p and mep:
        return _normalizar_escala(p["precio"] / mep, b.precio_ejemplo), p.get("var"), f"data912 ({b.ticker} $ / MEP)"
    for s in b.simbolos_usd:
        h = cargar_historico(s)
        if h:
            return _normalizar_escala(h[0], b.precio_ejemplo), None, f"data912 cierre {h[1] or ''}"
    return b.precio_ejemplo, None, "ejemplo fijo"


def _meses(desde: tuple[int, int], hasta: tuple[int, int]) -> list[tuple[int, int]]:
    out, (y, m) = [], desde
    while (y, m) <= hasta:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ---------------------------------------------------------------------------
# Sidebar y datos
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Renta Fija Soberana")
    if st.button("🔄 Actualizar", width="stretch", type="primary"):
        st.cache_data.clear()
        st.rerun()
    ahora = ahora_ar()
    abierto = mercado_abierto(ahora)
    st.caption("🟢 Mercado abierto: precios en vivo (último operado)." if abierto
               else "🔴 Mercado cerrado: precios de cierre de la última rueda.")
    plazo = st.radio("Liquidación", [1, 0], format_func=lambda x: "24 hs (T+1)" if x else "Contado inmediato",
                     horizontal=True)

f_op = fecha_operacion(ahora)
liq = fecha_liquidacion(f_op, plazo)

with st.spinner("Consultando data912 y BCRA…"):
    precios, err_912, mep_api, fuente_mep, hora_precios = cargar_precios()
    series, desc_bcra, err_bcra = cargar_bcra(date(2024, 1, 1))

mercado = armar_mercado(liq, series)

mep, fuente_mep_final = mep_api, fuente_mep
if not mep:
    al30, al30d = precios.get("AL30"), precios.get("AL30D")
    if al30 and al30d:
        mep, fuente_mep_final = al30["precio"] / al30d["precio"], "AL30 / AL30D (data912)"
    else:
        mep, fuente_mep_final = MEP_RESPALDO, "respaldo (AL30 $/USD 30/10/2026)"

with st.sidebar:
    st.caption(f"Precios consultados: {hora_precios:%d/%m/%Y %H:%M} (hora AR)")
    st.caption(f"Fecha de operación: {fmt_fecha(f_op)} · Liquidación: {fmt_fecha(liq)}")
    with st.expander("Fuentes y estado de las APIs"):
        st.write(f"**data912**: {len(precios)} especies" + (f" · errores: {', '.join(err_912)}" if err_912 else ""))
        st.write(f"**Dólar MEP**: {fuente_mep_final}")
        for k, v in mercado.fuentes.items():
            st.write(f"**{k}**: {v}")
        if err_bcra:
            st.write("BCRA sin datos para: " + ", ".join(err_bcra))
        if desc_bcra:
            st.json(desc_bcra, expanded=False)

# ---------------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------------

st.title("📈 Renta Fija Soberana")
ipc = mercado.ipc
rem = series.get("inflacion_rem")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Dólar MEP", f"$ {mep:,.2f}")
c2.metric("A3500", f"$ {mercado.a3500_actual:,.2f}", help=f"Último dato: {fmt_fecha(mercado.a3500.index.max().date())}")
c3.metric("Brecha MEP / A3500", f"{mep / mercado.a3500_actual - 1:.2%}")
c4.metric("TAMAR (TNA)", f"{mercado.tamar_actual:.2%}", help=f"Bancos privados. Último dato: {fmt_fecha(mercado.ultimo_tamar)}")
c5.metric("CER", f"{valor_en(mercado.cer, min(f_op, mercado.ultimo_cer)):,.4f}",
          help=f"Publicado hasta {fmt_fecha(mercado.ultimo_cer)}")
c6.metric("Inflación último mes", f"{ipc.iloc[-1]:.1%}", help=f"IPC {ipc.index[-1]:%m/%Y}")
st.caption(f"Operación {fmt_fecha(f_op)} · liquidación {fmt_fecha(liq)} · "
           f"{'precios en vivo' if abierto else 'precios de cierre'} · MEP: {fuente_mep_final}")

tab1, tab2, tab3 = st.tabs(["📋 Resumen", "🔮 Escenarios", "🎯 Expectativas de mercado"])

# ---------------------------------------------------------------------------
# Solapa 1 - Resumen (hoja "Resumen" del Excel)
# ---------------------------------------------------------------------------

vto_max = max(b.vencimiento for b in BONOS_PESOS)
primer_ipc = mes_ipc_que_aplica(mercado.ultimo_cer + timedelta(days=1))
meses_ipc = _meses(primer_ipc, (vto_max.year, vto_max.month))
infl_def = float(ipc.iloc[-1]) if not ipc.empty else 0.02

with tab1:
    st.subheader("Parámetros")
    p1, p2, p3 = st.columns([1, 1.4, 2.2])
    with p1:
        shock = st.number_input("Shock de precio (%)", value=0.0, step=0.5, format="%.2f",
                                help="Se aplica a todos los precios: Precio usado = Precio base × (1 + shock).") / 100
    with p2:
        st.markdown("**Proyección TAMAR (TNA)**")
        modo = st.radio("Escenario", ["Manual", "Promedio 5 días", "Último dato"], index=2, horizontal=True)
        tamar_manual = st.number_input("TAMAR manual (%)", value=22.0, step=0.25, format="%.2f") / 100
        ult5 = mercado.tamar.index[-5:]
        st.caption(f"Promedio 5 días ({ult5[0]:%d/%m}-{ult5[-1]:%d/%m}): **{mercado.tamar_prom5:.2%}** · "
                   f"Último dato ({fmt_fecha(mercado.ultimo_tamar)}): **{mercado.tamar_actual:.2%}**")
        tamar_aplicada = {"Manual": tamar_manual, "Promedio 5 días": mercado.tamar_prom5,
                          "Último dato": mercado.tamar_actual}[modo]
        st.caption(f"TAMAR aplicada: **{tamar_aplicada:.2%}**")
    with p3:
        st.markdown("**Inflación mensual proyectada** (IPC de cada mes)")
        base_infl = st.number_input("Completar todos los meses con (%)", value=round(infl_def * 100, 2),
                                    step=0.1, format="%.2f")
        tabla_infl = pd.DataFrame({"Mes": [nombre_mes(x) for x in meses_ipc], "Inflación %": base_infl})
        tabla_infl = st.data_editor(tabla_infl, hide_index=True, disabled=["Mes"], height=200,
                                    key=f"infl_{base_infl}_{primer_ipc}", width="stretch")
        st.caption(f"El CER publicado llega al {fmt_fecha(mercado.ultimo_cer)}: incorpora el IPC hasta "
                   f"{nombre_mes(mes_ipc_que_aplica(mercado.ultimo_cer))}. Desde el 16 de cada mes el CER "
                   "crece con el IPC de dos meses antes.")
    inflacion_res = {x: float(v) / 100 for x, v in zip(meses_ipc, tabla_infl["Inflación %"]) if pd.notna(v)}
    sup_res = Supuestos(inflacion=inflacion_res, inflacion_default=base_infl / 100, tamar=tamar_aplicada)

# Precios base
precios_ars, fuentes_ars, var_ars = {}, {}, {}
for b in BONOS_PESOS:
    precios_ars[b.ticker], var_ars[b.ticker], fuentes_ars[b.ticker] = precio_pesos(b, precios)
precios_usd, fuentes_usd, var_usd = {}, {}, {}
for b in BONOS_USD:
    precios_usd[b.ticker], var_usd[b.ticker], fuentes_usd[b.ticker] = precio_usd(b, precios, mep)


def tabla_pesos(sup: Supuestos, shock_: float) -> pd.DataFrame:
    filas = []
    for b in BONOS_PESOS:
        base = precios_ars[b.ticker]
        r = indicadores_pesos(b, base * (1 + shock_), mercado, sup)
        filas.append({"Ticker": b.ticker, "Asset class": b.tipo, "Vencimiento": b.vencimiento,
                      "Precio base": base, "Precio usado": base * (1 + shock_), "Var. %": var_ars[b.ticker],
                      **{k: r[k] for k in ("TIR (TEA)", "TIR real / USD", "TNA", "Conv. TNA", "TEM", "MD",
                                           "Días", "VT", "Paridad", "Pago final", "Retorno directo")},
                      "Liquidación": liq, "Moneda": "ARS", "Fuente": fuentes_ars[b.ticker]})
    df = pd.DataFrame(filas)
    for c in ("Var. %", "VT", "Paridad", "TIR real / USD"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def tabla_usd(shock_: float) -> pd.DataFrame:
    filas = []
    for b in BONOS_USD:
        base = precios_usd[b.ticker]
        r = analizar_usd(b, base * (1 + shock_), liq)
        filas.append({"Ticker": b.ticker, "Asset class": b.familia, "Vencimiento": b.vencimiento,
                      "Precio base": base, "Precio usado": base * (1 + shock_), "Var. %": var_usd[b.ticker],
                      "TIR (TEA)": r.tir, "TNA (180/360)": r.tna, "MD": r.duration, "Dur. mod.": r.duration_mod,
                      "Paridad": r.paridad, "VR": r.residual, "Int. corridos": r.interes_corrido,
                      "Flujo a cobrar": r.flujo_a_cobrar, "Current yield": r.current_yield,
                      "Cupón próximo": r.proximo_pago, "Liquidación": liq, "Moneda": "USD",
                      "Fuente": fuentes_usd[b.ticker]})
    df = pd.DataFrame(filas)
    df["Var. %"] = pd.to_numeric(df["Var. %"], errors="coerce")
    return df


def _pct(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce") * 100
    return df


df_ars = tabla_pesos(sup_res, shock)
df_usd = tabla_usd(shock)

FMT = {
    "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
    "Liquidación": st.column_config.DateColumn(format="DD/MM/YYYY"),
    "Cupón próximo": st.column_config.DateColumn(format="DD/MM/YYYY"),
    "Precio base": st.column_config.NumberColumn(format="%.2f"),
    "Precio usado": st.column_config.NumberColumn(format="%.2f"),
    "VT": st.column_config.NumberColumn("Valor técnico", format="%.2f"),
    "Pago final": st.column_config.NumberColumn(format="%.2f", help="Por 100 VN, con las proyecciones de arriba."),
    "MD": st.column_config.NumberColumn(format="%.2f", help="Como en el Excel: duration de Macaulay (años)."),
    "Dur. mod.": st.column_config.NumberColumn(format="%.2f"),
    "VR": st.column_config.NumberColumn(format="%.2f"),
    "Int. corridos": st.column_config.NumberColumn(format="%.3f"),
    "Flujo a cobrar": st.column_config.NumberColumn(format="%.2f"),
    **{c: st.column_config.NumberColumn(format=PCT) for c in
       ("Var. %", "TIR (TEA)", "TIR real / USD", "TNA", "TNA (180/360)", "TEM", "Paridad", "Retorno directo",
        "Current yield")},
}

with tab1:
    st.subheader("Bonos en pesos")
    st.dataframe(_pct(df_ars, ["TIR (TEA)", "TIR real / USD", "TNA", "TEM", "Paridad", "Retorno directo"]),
                 hide_index=True, width="stretch", column_config=FMT)
    st.caption(
        "TIR (TEA) = XIRR del flujo proyectado: CER con la inflación de arriba (CER de 10 hábiles antes del vto.), "
        "TAMAR con la proyección elegida (promedio de TAMAR entre emisión−10 y vto−10 hábiles + margen; "
        "TEM = ((1+TNA·32/365)^(365/32))^(1/12)−1; pago = 100·(1+TEM)^(DAYS360/30)). "
        "TIR real / USD: CER constante (t−10) o A3500 constante. TZV27: la TIR (TEA) usa el A3500 actual."
    )
    g1, g2 = st.columns(2)
    fig = px.scatter(df_ars, x="MD", y=df_ars["TIR (TEA)"] * 100, color="Asset class", text="Ticker",
                     labels={"y": "TIR % (TEA)"}, title="Curva en pesos (TIR nominal)")
    fig.update_traces(textposition="top center", marker_size=10)
    g1.plotly_chart(fig, width="stretch")
    real = df_ars.dropna(subset=["TIR real / USD"])
    fig = px.scatter(real, x="MD", y=real["TIR real / USD"] * 100, color="Asset class", text="Ticker",
                     labels={"y": "TIR % real / USD"}, title="CER (real) y dólar linked (USD)")
    fig.update_traces(textposition="top center", marker_size=10)
    g2.plotly_chart(fig, width="stretch")

    st.subheader("Bonos en dólares")
    st.dataframe(_pct(df_usd, ["TIR (TEA)", "TNA (180/360)", "Paridad", "Current yield"]),
                 hide_index=True, width="stretch", column_config=FMT)
    notas = [f"**{b.ticker}**: {b.nota}" for b in BONOS_USD if b.nota]
    st.caption("Precio por 100 VN original (precio sucio). Intereses 30/360 (YEARFRAC base 0), TIR = XIRR, "
               "MD = Macaulay. " + " · ".join(notas))
    fig = px.scatter(df_usd, x="MD", y=df_usd["TIR (TEA)"] * 100, color="Asset class", text="Ticker",
                     labels={"y": "TIR %"}, title="Curva en dólares")
    fig.update_traces(textposition="top center", marker_size=10)
    st.plotly_chart(fig, width="stretch")

    st.subheader("Sensibilidad: TIR según shock de precio")
    filas = []
    for s_ in SHOCKS:
        fila = {"Shock": f"{s_:+.1%}"}
        ta = tabla_pesos(sup_res, shock + s_)
        tu = tabla_usd(shock + s_)
        fila.update({t: v * 100 for t, v in zip(ta["Ticker"], ta["TIR (TEA)"])})
        fila.update({t: v * 100 for t, v in zip(tu["Ticker"], tu["TIR (TEA)"])})
        filas.append(fila)
    sens = pd.DataFrame(filas)
    st.dataframe(sens, hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format=PCT) for c in sens.columns if c != "Shock"})
    st.caption("Cada fila suma ese shock al shock general de arriba (equivale a la tabla de datos del Excel sobre C3).")

    with st.expander("Flujo de fondos de un bono en dólares"):
        elegido = st.selectbox("Bono", [b.ticker for b in BONOS_USD])
        bono = next(b for b in BONOS_USD if b.ticker == elegido)
        cf = flujos_usd(bono)
        cf = cf[cf["fecha"] > liq].copy()
        cf["tasa"] *= 100
        st.dataframe(cf, hide_index=True, width="stretch",
                     column_config={"fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                                    "inicio": st.column_config.DateColumn("Inicio período", format="DD/MM/YYYY"),
                                    "tasa": st.column_config.NumberColumn("Cupón", format=PCT),
                                    "residual": st.column_config.NumberColumn("VR", format="%.2f"),
                                    "interes": st.column_config.NumberColumn("Intereses", format="%.4f"),
                                    "amortizacion": st.column_config.NumberColumn("Amortiza", format="%.4f"),
                                    "total": st.column_config.NumberColumn("Flujo", format="%.4f")})

# ---------------------------------------------------------------------------
# Solapa 2 - Escenarios
# ---------------------------------------------------------------------------


def _var_mensual_reciente(s: pd.Series, dias: int = 30) -> float:
    if s is None or len(s) < dias + 1:
        return 0.0
    ini = valor_en(s, (s.index.max() - pd.Timedelta(days=dias)).date())
    return (float(s.iloc[-1]) / ini) ** (30 / dias) - 1 if ini else 0.0


with tab2:
    st.subheader("Escenarios")
    st.caption("La senda arranca con la inflación y la TAMAR de la solapa Resumen. Cada bono en pesos se lleva "
               "a su vencimiento; los bonos en dólares, al horizonte elegido saliendo a la misma TIR.")
    a3500_def = _var_mensual_reciente(series.get("a3500")) if mercado.fuentes["A3500"] == "BCRA" else 0.01
    b1, b2 = st.columns(2)
    base_a3500 = b1.number_input("Devaluación A3500 mensual (%)", value=round(a3500_def * 100, 2), step=0.1, format="%.2f")
    base_mep = b2.number_input("Variación MEP mensual (%)", value=round(a3500_def * 100, 2), step=0.1, format="%.2f")

    meses_esc = _meses(min(primer_ipc, (liq.year, liq.month)), (vto_max.year, vto_max.month))
    tabla = pd.DataFrame({
        "Mes": [nombre_mes(x) for x in meses_esc],
        "Inflación % m.": [inflacion_res.get(x, base_infl) * (100 if x in inflacion_res else 1)
                           if x >= primer_ipc else None for x in meses_esc],
        "TAMAR % TNA": tamar_aplicada * 100,
        "A3500 % m.": base_a3500,
        "MEP % m.": base_mep,
    })
    st.markdown("**Senda mensual** (editá cualquier celda). La inflación es la del IPC de cada mes.")
    clave = f"senda_{hash(tuple(sorted(inflacion_res.items())))}_{tamar_aplicada}_{base_a3500}_{base_mep}"
    editada = st.data_editor(tabla, hide_index=True, width="stretch", disabled=["Mes"], key=clave)

    def _dict(col):
        return {x: float(v) / 100 for x, v in zip(meses_esc, editada[col]) if pd.notna(v)}

    sup = Supuestos(
        inflacion=_dict("Inflación % m."), inflacion_default=base_infl / 100,
        tamar=tamar_aplicada, tamar_mensual=_dict("TAMAR % TNA"),
        a3500_mensual=_dict("A3500 % m."), a3500_default=base_a3500 / 100,
        mep_mensual=_dict("MEP % m."), mep_default=base_mep / 100,
    )

    def mep_en(d: date) -> float:
        return proyectar_mensual(mep, liq, d, sup.mep_mensual, sup.mep_default) if d > liq else mep

    filas = []
    for b in BONOS_PESOS:
        p = precios_ars[b.ticker] * (1 + shock)
        pago = pago_final(b, mercado, sup)
        dias = (b.vencimiento - liq).days
        ret = pago / p - 1
        mep_t = mep_en(b.vencimiento)
        ret_usd = (pago / mep_t) / (p / mep) - 1
        filas.append({
            "Ticker": b.ticker, "Asset class": b.tipo, "Vencimiento": b.vencimiento, "Precio": p,
            "Pago proyectado": pago, "Retorno directo": ret * 100,
            "TEA $": ((1 + ret) ** (365 / dias) - 1) * 100,
            "TEM $": ((1 + ret) ** (365 / dias / 12) - 1) * 100,
            "MEP al vto": mep_t, "Retorno USD": ret_usd * 100,
            "TEA USD": ((1 + ret_usd) ** (365 / dias) - 1) * 100,
        })
    df_esc = pd.DataFrame(filas)
    st.markdown("**Bonos en pesos: retorno a vencimiento bajo el escenario**")
    st.dataframe(df_esc, hide_index=True, width="stretch",
                 column_config={"Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                "Precio": st.column_config.NumberColumn(format="%.2f"),
                                "Pago proyectado": st.column_config.NumberColumn(format="%.2f"),
                                "MEP al vto": st.column_config.NumberColumn(format="%.2f"),
                                **{c: st.column_config.NumberColumn(format=PCT)
                                   for c in ["Retorno directo", "TEA $", "TEM $", "Retorno USD", "TEA USD"]}})

    horizonte = st.date_input("Horizonte para bonos en dólares", value=date(2027, 6, 30),
                              min_value=liq + timedelta(days=7), format="DD/MM/YYYY")
    mep_h = mep_en(horizonte)
    filas = []
    for b in BONOS_USD:
        p = precios_usd[b.ticker] * (1 + shock)
        r = analizar_usd(b, p, liq)
        cf = flujos_usd(b)
        cf = cf[cf["fecha"] > liq]
        cobrado = cf[cf["fecha"] <= horizonte]["total"].sum()
        resto = cf[cf["fecha"] > horizonte]
        salida = sum(v / (1 + r.tir) ** yearfrac_365(horizonte, d) for d, v in zip(resto["fecha"], resto["total"]))
        dias = (horizonte - liq).days
        ret_usd = (cobrado + salida) / p - 1
        ret_ars = (1 + ret_usd) * mep_h / mep - 1
        filas.append({"Ticker": b.ticker, "Asset class": b.familia, "Precio USD": p, "TIR": r.tir * 100,
                      "Retorno USD": ret_usd * 100, "TEA USD": ((1 + ret_usd) ** (365 / dias) - 1) * 100,
                      "Retorno $": ret_ars * 100, "TEA $": ((1 + ret_ars) ** (365 / dias) - 1) * 100})
    df_esc_usd = pd.DataFrame(filas)
    st.markdown(f"**Bonos en dólares al {fmt_fecha(horizonte)}** (salida a TIR constante; MEP proyectado $ {mep_h:,.2f})")
    st.dataframe(df_esc_usd, hide_index=True, width="stretch",
                 column_config={"Precio USD": st.column_config.NumberColumn(format="%.2f"),
                                **{c: st.column_config.NumberColumn(format=PCT)
                                   for c in ["TIR", "Retorno USD", "TEA USD", "Retorno $", "TEA $"]}})

    comp = pd.concat([
        df_esc[["Ticker", "TEA $", "TEA USD"]].assign(Grupo="Pesos (a vto.)"),
        df_esc_usd[["Ticker", "TEA $", "TEA USD"]].assign(Grupo=f"Dólares (al {fmt_fecha(horizonte)})"),
    ])
    moneda = st.radio("Comparar TEA en", ["TEA $", "TEA USD"], horizontal=True)
    st.plotly_chart(px.bar(comp.sort_values(moneda), x="Ticker", y=moneda, color="Grupo",
                           title=f"{moneda} bajo el escenario"), width="stretch")

    st.markdown("**Sendas proyectadas**")
    fechas = pd.date_range(liq, vto_max, freq="D")
    cer_p = cer_proyectado(mercado, vto_max, sup)
    tam_p = tamar_proyectada(mercado, vto_max, sup)
    sendas = pd.DataFrame({
        "CER": [valor_en(cer_p, d.date()) for d in fechas],
        "TAMAR % TNA": [valor_en(tam_p, d.date()) * 100 for d in fechas],
        "A3500": [a3500_en(mercado, d.date(), sup) for d in fechas],
        "Dólar MEP": [mep_en(d.date()) for d in fechas],
    }, index=fechas)
    s1, s2 = st.columns(2)
    s1.plotly_chart(px.line(sendas, y="CER", title="CER"), width="stretch")
    s2.plotly_chart(px.line(sendas, y="TAMAR % TNA", title="TAMAR (TNA %)"), width="stretch")
    fig = go.Figure()
    fig.add_scatter(x=sendas.index, y=sendas["A3500"], name="A3500")
    fig.add_scatter(x=sendas.index, y=sendas["Dólar MEP"], name="Dólar MEP")
    fig.update_layout(title="Tipo de cambio", yaxis_title="$ por USD")
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# Solapa 3 - Expectativas de mercado (hoja "Breakeven" del Excel)
# ---------------------------------------------------------------------------

POR_TIPO = {t: [b for b in BONOS_PESOS if b.tipo == t] for t in ("Tasa fija", "CER", "TAMAR", "Dólar linked")}
P = {t: p * (1 + shock) for t, p in precios_ars.items()}


def _par_txt(a: BonoPesos, b: BonoPesos) -> str:
    if a.vencimiento == b.vencimiento:
        return f"{a.ticker} vs {b.ticker} (vencen el {fmt_fecha(a.vencimiento)})"
    return (f"{a.ticker} ({fmt_fecha(a.vencimiento)}) vs {b.ticker} ({fmt_fecha(b.vencimiento)}): no hay par con "
            f"igual vencimiento, se usa el más cercano y la TEA de {a.ticker} se lleva al plazo de {b.ticker} "
            "(breakeven aproximado)")


def _meses_txt(meses: list[tuple[int, int]]) -> str:
    if not meses:
        return "—"
    return nombre_mes(meses[0]) if len(meses) == 1 else f"{nombre_mes(meses[0])} a {nombre_mes(meses[-1])}"


with tab3:
    st.subheader("Expectativas implícitas en los precios")
    st.caption("Cada indicador iguala el rendimiento de dos bonos que vencen el mismo día (o el más cercano). "
               "Debajo de cada valor se aclara a qué período corresponde, contando los rezagos "
               "(CER: 10 hábiles; TAMAR: 10 hábiles; A3500: 3 hábiles). Usa los precios de la solapa Resumen "
               "(con el shock) y su proyección de inflación y TAMAR.")
    resumen = []

    st.markdown("### Inflación breakeven (tasa fija vs CER)")
    cols = st.columns(len(POR_TIPO["CER"]))
    res_infl = {}
    for col, cer in zip(cols, POR_TIPO["CER"]):
        fija = par_mas_cercano(cer, POR_TIPO["Tasa fija"])
        r = be_inflacion(fija, P[fija.ticker], cer, P[cer.ticker], mercado, sup_res)
        res_infl[cer.ticker] = (fija, r)
        col.metric(f"{fija.ticker} / {cer.ticker}", f"{r['valor']:.2%} mensual",
                   f"{r['anual']:.1%} anual · {r['valor'] - r['proyectada']:+.2%} vs proyección", delta_color="off")
        col.caption(
            f"Inflación mensual promedio implícita entre el CER del **{fmt_fecha(r['fecha_ini'])}** (liq. − 10 hábiles) "
            f"y el del **{fmt_fecha(r['fecha_fin'])}** (vto − 10 hábiles), {r['dias']} días: corresponde al IPC de "
            f"**{_meses_txt(r['meses'])}** (aún no publicado: {_meses_txt(r['meses_desconocidos'])}). "
            f"Acumulada: {r['acumulada']:.2%}. CER que iguala ambos: {r['cer_be']:,.2f}. "
            f"Proyección de la solapa Resumen: {r['proyectada']:.2%} mensual. Par: {_par_txt(fija, cer)}."
        )
        resumen.append({"Indicador": "Inflación BE (mensual)", "Par": f"{fija.ticker}/{cer.ticker}",
                        "Valor": f"{r['valor']:.2%}", "Período": f"IPC {_meses_txt(r['meses'])}"})
    with st.expander("Inflación breakeven mensual según cambio de precio (filas: tasa fija, columnas: CER)"):
        elegido = st.selectbox("Par", list(res_infl), format_func=lambda t: f"{res_infl[t][0].ticker} / {t}")
        fija, _ = res_infl[elegido]
        cer = next(b for b in POR_TIPO["CER"] if b.ticker == elegido)
        grid_shocks = [-0.02, -0.01, -0.005, 0.0, 0.005, 0.01, 0.02]
        grilla = pd.DataFrame(
            [[be_inflacion(fija, P[fija.ticker], cer, P[cer.ticker], mercado, sup_res, sf, sc)["valor"] * 100
              for sc in grid_shocks] for sf in grid_shocks],
            index=[f"{fija.ticker} {s:+.1%}" for s in grid_shocks], columns=[f"{cer.ticker} {s:+.1%}" for s in grid_shocks])
        st.dataframe(grilla, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format=PCT) for c in grilla.columns})
        st.caption("Lectura: si la inflación mensual promedio entre las fechas CER supera el breakeven, rinde más "
                   "el CER; si queda por debajo, la tasa fija.")
    if rem is not None and not rem.empty:
        st.caption(f"Referencia REM (BCRA): {rem.iloc[-1]:.1%} ({rem.index[-1]:%m/%Y}).")

    st.markdown("### TAMAR breakeven (tasa fija vs TAMAR)")
    cols = st.columns(len(POR_TIPO["TAMAR"]))
    for col, tam in zip(cols, POR_TIPO["TAMAR"]):
        fija = par_mas_cercano(tam, POR_TIPO["Tasa fija"])
        r = be_tamar(fija, P[fija.ticker], tam, P[tam.ticker], mercado, sup_res)
        col.metric(f"{fija.ticker} / {tam.ticker}", f"{r['valor']:.2%} TNA",
                   f"{r['valor'] - mercado.tamar_actual:+.2%} vs TAMAR actual", delta_color="off")
        col.caption(
            f"TAMAR promedio (TNA, bancos privados) implícita para los días hábiles del **{fmt_fecha(r['desde'])} al "
            f"{fmt_fecha(r['hasta'])}** (vto − 10 hábiles). Hasta el {fmt_fecha(r['ultimo_dato'])} se usa la TAMAR "
            f"publicada; el bono promedia desde el {fmt_fecha(r['inicio_promedio'])}. Margen {tam.margen:.2%}. "
            f"Par: {_par_txt(fija, tam)}."
        )
        resumen.append({"Indicador": "TAMAR BE (TNA)", "Par": f"{fija.ticker}/{tam.ticker}",
                        "Valor": f"{r['valor']:.2%}", "Período": f"{fmt_fecha(r['desde'])} – {fmt_fecha(r['hasta'])}"})

    st.markdown("### Dólar breakeven (tasa fija vs dólar linked)")
    cols = st.columns(2)
    for col, dl in zip(cols, POR_TIPO["Dólar linked"]):
        fija = par_mas_cercano(dl, POR_TIPO["Tasa fija"])
        r = be_dolar(fija, P[fija.ticker], dl, P[dl.ticker], mercado, sup_res)
        col.metric(f"{fija.ticker} / {dl.ticker}", f"$ {r['valor']:,.2f}",
                   f"{r['variacion']:+.2%} vs A3500 actual", delta_color="off")
        col.caption(
            f"A3500 implícito al **{fmt_fecha(r['fecha'])}** (vto − 3 hábiles). Devaluación implícita "
            f"{r['mensual']:.2%} mensual ({r['anual']:.1%} anual) desde $ {r['a3500_hoy']:,.2f} "
            f"({fmt_fecha(r['fecha_a3500_hoy'])}). Par: {_par_txt(fija, dl)}."
        )
        resumen.append({"Indicador": "Dólar BE (A3500)", "Par": f"{fija.ticker}/{dl.ticker}",
                        "Valor": f"$ {r['valor']:,.2f}", "Período": f"al {fmt_fecha(r['fecha'])}"})

    st.markdown("### Tipo de cambio real esperado (CER vs dólar linked)")
    cols = st.columns(2)
    for col, dl in zip(cols, POR_TIPO["Dólar linked"]):
        cer = par_mas_cercano(dl, POR_TIPO["CER"])
        r = tcr_esperado(cer, P[cer.ticker], dl, P[dl.ticker], mercado, sup_res)
        col.metric(f"{cer.ticker} / {dl.ticker}", f"{r['anual']:+.2%} anual",
                   "depreciación real" if r["anual"] >= 0 else "apreciación real", delta_color="off")
        col.caption(
            f"Variación anual del A3500 deflactado por CER que iguala ambos bonos, desde hoy hasta el "
            f"**{fmt_fecha(r['fecha_a3500'])}** (A3500 vto − 3 hábiles; CER al {fmt_fecha(r['fecha_cer'])}). "
            f"Positivo = el peso se deprecia en términos reales (sube el TCR). Acumulado: {r['acumulada']:+.2%}. "
            f"Tasa real CER {r['r_cer']:.2%} vs tasa USD del DL {r['r_dl']:.2%}. Par: {_par_txt(cer, dl)}."
        )
        resumen.append({"Indicador": "TCR esperado (% anual)", "Par": f"{cer.ticker}/{dl.ticker}",
                        "Valor": f"{r['anual']:+.2%}", "Período": f"hasta {fmt_fecha(r['fecha_a3500'])}"})

    st.markdown("### Tasa real breakeven (TAMAR vs CER)")
    cols = st.columns(len(POR_TIPO["TAMAR"]))
    for col, tam in zip(cols, POR_TIPO["TAMAR"]):
        cer = par_mas_cercano(tam, POR_TIPO["CER"])
        r = tasa_real_be(tam, P[tam.ticker], cer, P[cer.ticker], mercado, sup_res)
        col.metric(f"{tam.ticker} / {cer.ticker}", f"{r['sin_margen']:.2%} real anual",
                   f"{r['con_margen']:.2%} incluyendo margen", delta_color="off")
        col.caption(
            f"TAMAR real (TEA de la TAMAR deflactada por inflación) que iguala ambos bonos hasta el "
            f"**{fmt_fecha(r['hasta_tamar'])}** (última TAMAR que promedia {tam.ticker}; CER de {cer.ticker} al "
            f"{fmt_fecha(r['hasta_cer'])}). TAMAR nominal implícita {r['tamar_be']:.2%} TNA con inflación de "
            f"{r['inflacion_supuesta']:.1%} anual (proyección de Resumen). Tasa real del CER: {r['r_cer']:.2%}. "
            f"Par: {_par_txt(cer, tam)}."
        )
        resumen.append({"Indicador": "Tasa real BE (TAMAR real)", "Par": f"{tam.ticker}/{cer.ticker}",
                        "Valor": f"{r['sin_margen']:.2%}", "Período": f"hasta {fmt_fecha(r['hasta_tamar'])}"})

    st.markdown("### Resumen")
    st.dataframe(pd.DataFrame(resumen), hide_index=True, width="stretch")

st.divider()
st.caption(
    "Precios: data912.com (si falta una especie se usa el último cierre o un precio fijo de ejemplo, indicado en "
    "la columna Fuente). Variables: API de Estadísticas Monetarias del BCRA; si no responde, series del Excel. "
    "Fórmulas verificadas contra la planilla Soberanos_por_asset_class.xlsx y contra 1816. No es recomendación de inversión."
)
