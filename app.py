"""Monitor de Bonos Soberanos - Argentina.

Solapas:
1. Resumen: cotizaciones, TNA, TEM, TIR, duration de bonos en pesos y en dólares.
2. Escenarios: inflación (CER), TAMAR, A3500 y dólar MEP proyectados por el usuario.
3. Expectativas de mercado: breakevens entre pares de bonos.

Precios: data912.com. Variables: API de Estadísticas Monetarias del BCRA.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from bonos import BONOS_PESOS, BONOS_USD, FECHA_REF, BonoPesos, BonoUSD
from calc import analizar_usd, flujos_usd, habil_siguiente_o_igual, mes_ipc_que_aplica, proyectar_cer, valor_en
from calendario import ahora_ar, fecha_liquidacion, fecha_operacion, mercado_abierto, sumar_habiles
from datos import cierre_historico_data912, mep_data912, precios_data912, variables_bcra
from valuacion import (
    REZAGO_A3500, REZAGO_CER, REZAGO_TAMAR, Supuestos, armar_mercado, be_dolar, be_inflacion,
    be_tamar, fmt_fecha, indicadores_pesos, nombre_mes, pago_final,
    par_mas_cercano, proyectar_mensual, serie_tamar_proyectada, tasa_real_be, tcr_esperado,
)

st.set_page_config(page_title="Bonos Soberanos", page_icon="📈", layout="wide")

MEP_RESPALDO = 1573.6  # AL30 $ / AL30D (1816, 30/10/2026)
PCT = "%.2f%%"


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


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Bonos Soberanos")
    if st.button("🔄 Actualizar", width="stretch", type="primary"):
        st.cache_data.clear()
        st.rerun()
    ahora = ahora_ar()
    abierto = mercado_abierto(ahora)
    st.caption(
        ("🟢 Mercado abierto: precios en vivo (último operado)." if abierto
         else "🔴 Mercado cerrado: precios de cierre de la última rueda.")
    )
    plazo = st.radio("Liquidación", [1, 0], format_func=lambda x: "24 hs (T+1)" if x else "Contado inmediato",
                     horizontal=True)

f_op = fecha_operacion(ahora)
liq = fecha_liquidacion(f_op, plazo)

with st.spinner("Consultando data912 y BCRA…"):
    precios, err_912, mep_api, fuente_mep, hora_precios = cargar_precios()
    series, desc_bcra, err_bcra = cargar_bcra(liq - timedelta(days=500))

mercado = armar_mercado(liq, series)

# Dólar MEP: data912 -> AL30/AL30D -> respaldo
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
            st.json({k: v for k, v in desc_bcra.items()}, expanded=False)

# ---------------------------------------------------------------------------
# Encabezado: variables de referencia
# ---------------------------------------------------------------------------

st.title("📈 Monitor de Bonos Soberanos")

infl = series.get("inflacion")
rem = series.get("inflacion_rem")
cer_hoy = valor_en(mercado.cer, f_op)
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Dólar MEP", f"$ {mep:,.2f}")
c2.metric("A3500", f"$ {mercado.a3500_actual:,.2f}", help=f"Último dato: {fmt_fecha(mercado.a3500.index.max().date())}")
c3.metric("Brecha MEP / A3500", f"{mep / mercado.a3500_actual - 1:.2%}")
c4.metric("TAMAR (TNA)", f"{mercado.tamar_actual:.2%}", help=f"Bancos privados. Último dato: {fmt_fecha(mercado.tamar.index.max().date())}")
c5.metric("CER", f"{cer_hoy:,.4f}" if mercado.fuentes["CER"] == "BCRA" else "s/d",
          help=f"Publicado hasta {fmt_fecha(mercado.ultimo_cer)}")
if infl is not None and not infl.empty:
    c6.metric("Inflación último mes", f"{infl.iloc[-1]:.1%}", help=f"IPC {infl.index[-1]:%m/%Y}")
else:
    c6.metric("Inflación último mes", "s/d")
st.caption(
    f"Operación {fmt_fecha(f_op)} · liquidación {fmt_fecha(liq)} · "
    f"{'precios en vivo' if abierto else 'precios de cierre'} · MEP: {fuente_mep_final}"
)

tab1, tab2, tab3 = st.tabs(["📋 Resumen", "🔮 Escenarios", "🎯 Expectativas de mercado"])

# ---------------------------------------------------------------------------
# Tabla de bonos en pesos y en dólares (convención de mercado)
# ---------------------------------------------------------------------------

filas_ars, precios_ars = [], {}
for b in BONOS_PESOS:
    p, var, fuente = precio_pesos(b, precios)
    precios_ars[b.ticker] = p
    r = indicadores_pesos(b, p, mercado)
    r["Var. %"] = var
    r["Fuente"] = fuente
    filas_ars.append(r)
df_ars = pd.DataFrame(filas_ars)
for _c in ("Var. %", "VT", "Paridad"):
    df_ars[_c] = pd.to_numeric(df_ars[_c], errors="coerce")

filas_usd, precios_usd = [], {}
for b in BONOS_USD:
    p, var, fuente = precio_usd(b, precios, mep)
    precios_usd[b.ticker] = p
    r = analizar_usd(b, p, liq)
    filas_usd.append({
        "Ticker": b.ticker, "Familia": b.familia, "Vencimiento": b.vencimiento,
        "Precio USD": p, "Var. %": var, "TIR": r.tir, "TNA (180/360)": r.tna,
        "Duration": r.duration, "Dur. mod.": r.duration_mod, "Paridad": r.paridad,
        "Valor técnico": r.valor_tecnico, "Residual %": r.residual, "Int. corrido": r.interes_corrido,
        "Current yield": r.current_yield, "Próximo pago": r.proximo_pago, "Fuente": fuente,
    })
df_usd = pd.DataFrame(filas_usd)
df_usd["Var. %"] = pd.to_numeric(df_usd["Var. %"], errors="coerce")


def _pct(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        df[c] = df[c].astype(float) * 100
    return df


with tab1:
    st.subheader("Bonos en pesos")
    vista = _pct(df_ars, ["TNA", "TEM", "TIR (TEA)", "Paridad"])
    st.dataframe(
        vista[["Ticker", "Tipo", "Vencimiento", "Días", "Precio", "Var. %", "VT", "Paridad", "Pago final",
               "TNA", "Conv. TNA", "TEM", "TIR (TEA)", "Duration", "Dur. mod.", "Fuente"]],
        hide_index=True, width="stretch",
        column_config={
            "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Precio": st.column_config.NumberColumn(format="%.2f"),
            "Var. %": st.column_config.NumberColumn(format=PCT),
            "VT": st.column_config.NumberColumn("Valor técnico", format="%.2f"),
            "Paridad": st.column_config.NumberColumn(format=PCT),
            "Pago final": st.column_config.NumberColumn(format="%.2f", help="Por 100 VN. CER, TAMAR y A3500 constantes."),
            "TNA": st.column_config.NumberColumn(format=PCT),
            "TEM": st.column_config.NumberColumn(format=PCT),
            "TIR (TEA)": st.column_config.NumberColumn(format=PCT),
            "Duration": st.column_config.NumberColumn(format="%.2f"),
            "Dur. mod.": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "TIR de CER y dólar linked: tasa real / en dólares (CER t-10 hábiles y A3500 constantes). "
        "TAMAR: se proyecta la última TAMAR publicada + margen. "
        "TNA tasa fija: plazo remanente; CER/DL: 180/360; TAMAR: 32/365. TEM = (1+TIR)^(1/12)−1."
    )
    g1, g2 = st.columns(2)
    nominal = df_ars[df_ars["Tipo"].isin(["Tasa fija", "TAMAR"])]
    fig = px.scatter(nominal, x="Duration", y=nominal["TIR (TEA)"] * 100, color="Tipo", text="Ticker",
                     labels={"y": "TIR % (TEA)"}, title="Curva en pesos: tasa fija vs TAMAR")
    fig.update_traces(textposition="top center", marker_size=10)
    g1.plotly_chart(fig, width="stretch")
    real = df_ars[df_ars["Tipo"].isin(["CER", "Dólar linked"])]
    fig = px.scatter(real, x="Duration", y=real["TIR (TEA)"] * 100, color="Tipo", text="Ticker",
                     labels={"y": "TIR % (real / USD)"}, title="CER y dólar linked")
    fig.update_traces(textposition="top center", marker_size=10)
    g2.plotly_chart(fig, width="stretch")

    st.subheader("Bonos en dólares (USD MEP)")
    vista = _pct(df_usd, ["TIR", "TNA (180/360)", "Paridad", "Current yield"])
    st.dataframe(
        vista, hide_index=True, width="stretch",
        column_config={
            "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Próximo pago": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Precio USD": st.column_config.NumberColumn(format="%.2f", help="USD por 100 VN original (precio sucio)."),
            "Var. %": st.column_config.NumberColumn(format=PCT),
            "TIR": st.column_config.NumberColumn(format=PCT),
            "TNA (180/360)": st.column_config.NumberColumn(format=PCT),
            "Paridad": st.column_config.NumberColumn(format=PCT),
            "Current yield": st.column_config.NumberColumn(format=PCT),
            "Duration": st.column_config.NumberColumn(format="%.2f"),
            "Dur. mod.": st.column_config.NumberColumn(format="%.2f"),
            "Valor técnico": st.column_config.NumberColumn(format="%.2f"),
            "Residual %": st.column_config.NumberColumn(format="%.2f"),
            "Int. corrido": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    notas = [f"**{b.ticker}**: {b.nota}" for b in BONOS_USD if b.nota]
    if notas:
        st.caption(" · ".join(notas))
    fig = px.scatter(df_usd, x="Duration", y=df_usd["TIR"] * 100, color="Familia", text="Ticker",
                     labels={"y": "TIR %"}, title="Curva en dólares")
    fig.update_traces(textposition="top center", marker_size=10)
    st.plotly_chart(fig, width="stretch")

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
                                    "residual": st.column_config.NumberColumn("Residual", format="%.2f"),
                                    "interes": st.column_config.NumberColumn("Interés", format="%.4f"),
                                    "amortizacion": st.column_config.NumberColumn("Amortización", format="%.4f"),
                                    "total": st.column_config.NumberColumn("Total", format="%.4f")})

# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------


def _var_mensual_reciente(s: pd.Series, dias: int = 30) -> float:
    if s is None or len(s) < dias + 1:
        return 0.0
    fin = s.index.max()
    ini = valor_en(s, (fin - pd.Timedelta(days=dias)).date())
    return (float(s.iloc[-1]) / ini) ** (30 / dias) - 1 if ini else 0.0


with tab2:
    st.subheader("Escenarios")
    st.caption(
        "Proyectá inflación (mueve el CER), TAMAR, A3500 y dólar MEP. Cada bono en pesos se lleva a su "
        "vencimiento; los bonos en dólares, al horizonte elegido saliendo a la misma TIR."
    )
    vto_max = max(b.vencimiento for b in BONOS_PESOS)
    infl_def = float(infl.iloc[-1]) if infl is not None and not infl.empty else 0.02
    a3500_def = _var_mensual_reciente(series.get("a3500")) if mercado.fuentes["A3500"] == "BCRA" else 0.01

    b1, b2, b3, b4 = st.columns(4)
    base_infl = b1.number_input("Inflación mensual base (%)", value=round(infl_def * 100, 2), step=0.1, format="%.2f")
    base_tamar = b2.number_input("TAMAR base (TNA %)", value=round(mercado.tamar_actual * 100, 2), step=0.25, format="%.2f")
    base_a3500 = b3.number_input("Devaluación A3500 mensual (%)", value=round(a3500_def * 100, 2), step=0.1, format="%.2f")
    base_mep = b4.number_input("Variación MEP mensual (%)", value=round(a3500_def * 100, 2), step=0.1, format="%.2f")

    # Meses a editar: desde el primer IPC que todavía no está en el CER publicado
    primer_ipc = mes_ipc_que_aplica(mercado.ultimo_cer + timedelta(days=1))
    meses = []
    y, m_ = min(primer_ipc, (liq.year, liq.month))
    while (y, m_) <= (vto_max.year, vto_max.month):
        meses.append((y, m_))
        m_ += 1
        if m_ > 12:
            y, m_ = y + 1, 1
    tabla = pd.DataFrame({
        "Mes": [nombre_mes(x) for x in meses],
        "Inflación % m.": [base_infl if x >= primer_ipc else None for x in meses],
        "TAMAR % TNA": base_tamar,
        "A3500 % m.": base_a3500,
        "MEP % m.": base_mep,
    })
    st.markdown("**Senda mensual** (editá cualquier celda). La inflación es la del IPC de cada mes: el CER la "
                "incorpora entre el 16 del mes siguiente y el 15 del subsiguiente.")
    editada = st.data_editor(
        tabla, hide_index=True, width="stretch", disabled=["Mes"],
        key=f"senda_{base_infl}_{base_tamar}_{base_a3500}_{base_mep}_{primer_ipc}",
    )
    d_infl = {x: float(v) / 100 for x, v in zip(meses, editada["Inflación % m."]) if pd.notna(v)}
    d_tamar = {x: float(v) / 100 for x, v in zip(meses, editada["TAMAR % TNA"]) if pd.notna(v)}
    d_a3500 = {x: float(v) / 100 for x, v in zip(meses, editada["A3500 % m."]) if pd.notna(v)}
    d_mep = {x: float(v) / 100 for x, v in zip(meses, editada["MEP % m."]) if pd.notna(v)}
    sup = Supuestos(
        inflacion=d_infl, inflacion_default=base_infl / 100, cer_plano=False,
        tamar=base_tamar / 100, tamar_mensual=d_tamar,
        a3500_mensual=d_a3500, a3500_default=base_a3500 / 100,
        mep_mensual=d_mep, mep_default=base_mep / 100,
    )

    def mep_en(d: date) -> float:
        return proyectar_mensual(mep, liq, d, sup.mep_mensual, sup.mep_default) if d > liq else mep

    filas = []
    for b in BONOS_PESOS:
        p = precios_ars[b.ticker]
        pago = pago_final(b, mercado, sup)
        dias = (b.vencimiento - liq).days
        ret = pago / p - 1
        mep_t = mep_en(b.vencimiento)
        ret_usd = (pago / mep_t) / (p / mep) - 1
        filas.append({
            "Ticker": b.ticker, "Tipo": b.tipo, "Vencimiento": b.vencimiento, "Precio": p,
            "Pago proyectado": pago, "Retorno directo": ret * 100,
            "TEA $": ((1 + ret) ** (365 / dias) - 1) * 100,
            "TEM $": ((1 + ret) ** (365 / dias / 12) - 1) * 100,
            "MEP al vto": mep_t, "Retorno USD": ret_usd * 100,
            "TEA USD": ((1 + ret_usd) ** (365 / dias) - 1) * 100,
        })
    df_esc = pd.DataFrame(filas)
    st.markdown("**Bonos en pesos: retorno a vencimiento bajo el escenario**")
    st.dataframe(
        df_esc, hide_index=True, width="stretch",
        column_config={
            "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Precio": st.column_config.NumberColumn(format="%.2f"),
            "Pago proyectado": st.column_config.NumberColumn(format="%.2f"),
            "MEP al vto": st.column_config.NumberColumn(format="%.2f"),
            **{c: st.column_config.NumberColumn(format=PCT)
               for c in ["Retorno directo", "TEA $", "TEM $", "Retorno USD", "TEA USD"]},
        },
    )

    horizonte = st.date_input("Horizonte para bonos en dólares", value=date(2027, 6, 30),
                              min_value=liq + timedelta(days=7), format="DD/MM/YYYY")
    mep_h = mep_en(horizonte)
    filas = []
    for b in BONOS_USD:
        p = precios_usd[b.ticker]
        r = analizar_usd(b, p, liq)
        cf = flujos_usd(b)
        cf = cf[cf["fecha"] > liq]
        cobrado = cf[cf["fecha"] <= horizonte]["total"].sum()
        resto = cf[cf["fecha"] > horizonte]
        salida = sum(v / (1 + r.tir) ** ((habil_siguiente_o_igual(d) - horizonte).days / 365)
                     for d, v in zip(resto["fecha"], resto["total"]))
        dias = (horizonte - liq).days
        ret_usd = (cobrado + salida) / p - 1
        ret_ars = (1 + ret_usd) * mep_h / mep - 1
        filas.append({
            "Ticker": b.ticker, "Familia": b.familia, "Precio USD": p, "TIR": r.tir * 100,
            "Retorno USD": ret_usd * 100, "TEA USD": ((1 + ret_usd) ** (365 / dias) - 1) * 100,
            "Retorno $": ret_ars * 100, "TEA $": ((1 + ret_ars) ** (365 / dias) - 1) * 100,
        })
    df_esc_usd = pd.DataFrame(filas)
    st.markdown(f"**Bonos en dólares al {fmt_fecha(horizonte)}** (salida a TIR constante; MEP proyectado "
                f"$ {mep_h:,.2f})")
    st.dataframe(df_esc_usd, hide_index=True, width="stretch",
                 column_config={"Precio USD": st.column_config.NumberColumn(format="%.2f"),
                                **{c: st.column_config.NumberColumn(format=PCT)
                                   for c in ["TIR", "Retorno USD", "TEA USD", "Retorno $", "TEA $"]}})

    comp = pd.concat([
        df_esc[["Ticker", "TEA $", "TEA USD"]].assign(Grupo="Pesos (a vto.)"),
        df_esc_usd[["Ticker", "TEA $", "TEA USD"]].assign(Grupo=f"Dólares (al {fmt_fecha(horizonte)})"),
    ])
    moneda = st.radio("Comparar TEA en", ["TEA $", "TEA USD"], horizontal=True)
    fig = px.bar(comp.sort_values(moneda), x="Ticker", y=moneda, color="Grupo",
                 title=f"{moneda} bajo el escenario")
    st.plotly_chart(fig, width="stretch")

    # Sendas proyectadas
    st.markdown("**Sendas proyectadas**")
    fechas = pd.date_range(liq, vto_max, freq="D")
    cer_proj = proyectar_cer(mercado.cer, vto_max, sup.inflacion, sup.inflacion_default)
    tam_proj = serie_tamar_proyectada(mercado, vto_max, sup)
    a0, d0 = mercado.a3500_actual, mercado.a3500.index.max().date()
    sendas = pd.DataFrame({
        "CER": [valor_en(cer_proj, d.date()) for d in fechas],
        "TAMAR % TNA": [valor_en(tam_proj, d.date()) * 100 for d in fechas],
        "A3500": [proyectar_mensual(a0, d0, d.date(), sup.a3500_mensual, sup.a3500_default)
                  if d.date() > d0 else valor_en(mercado.a3500, d.date()) for d in fechas],
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
# Expectativas de mercado (breakevens)
# ---------------------------------------------------------------------------

POR_TIPO = {t: [b for b in BONOS_PESOS if b.tipo == t] for t in ("Tasa fija", "CER", "TAMAR", "Dólar linked")}


def _par_txt(a: BonoPesos, b: BonoPesos) -> str:
    if a.vencimiento == b.vencimiento:
        return f"{a.ticker} vs {b.ticker} (vencen el {fmt_fecha(a.vencimiento)})"
    return (f"{a.ticker} ({fmt_fecha(a.vencimiento)}) vs {b.ticker} ({fmt_fecha(b.vencimiento)}) — "
            f"no hay par con igual vencimiento: se usa el más cercano y la TEA de {a.ticker} se lleva al plazo de {b.ticker}")


def _meses_txt(meses: list[tuple[int, int]]) -> str:
    if not meses:
        return "—"
    return nombre_mes(meses[0]) if len(meses) == 1 else f"{nombre_mes(meses[0])} a {nombre_mes(meses[-1])}"


with tab3:
    st.subheader("Expectativas implícitas en los precios")
    st.caption("Cada indicador iguala el rendimiento de dos bonos que vencen en la misma fecha (o en la más "
               "cercana). Debajo de cada valor se aclara a qué período corresponde, contando los rezagos de "
               "cada índice (CER: 10 hábiles; TAMAR: 10 hábiles; A3500: 3 hábiles).")
    resumen = []

    st.markdown("### Inflación breakeven (tasa fija vs CER)")
    cols = st.columns(len(POR_TIPO["CER"]))
    for col, cer in zip(cols, POR_TIPO["CER"]):
        fija = par_mas_cercano(cer, POR_TIPO["Tasa fija"])
        r = be_inflacion(fija, precios_ars[fija.ticker], cer, precios_ars[cer.ticker], mercado)
        meses_txt = _meses_txt(r["meses_desconocidos"])
        col.metric(f"{fija.ticker} / {cer.ticker}", f"{r['valor']:.2%} mensual", f"{r['anual']:.1%} anualizada",
                   delta_color="off")
        col.caption(
            f"Inflación mensual promedio implícita para el IPC de **{meses_txt}**. {cer.ticker} cobra el CER del "
            f"{fmt_fecha(r['fecha_cer'])} (vto − 10 hábiles), que incorpora el IPC hasta "
            f"{nombre_mes(r['meses_totales'][-1]) if r['meses_totales'] else '—'}; el CER publicado llega al "
            f"{fmt_fecha(r['ultimo_cer'])}. Variación acumulada del CER implícita: {r['acumulada']:.2%}. "
            f"Par: {_par_txt(fija, cer)}."
        )
        resumen.append({"Indicador": "Inflación BE (mensual)", "Par": f"{fija.ticker}/{cer.ticker}",
                        "Valor": f"{r['valor']:.2%}", "Período": f"IPC {meses_txt}"})
    if rem is not None and not rem.empty:
        st.caption(f"Referencia REM (BCRA): {desc_bcra.get('inflacion_rem', '')} = {rem.iloc[-1]:.1%} "
                   f"({rem.index[-1]:%m/%Y}).")

    st.markdown("### TAMAR breakeven (tasa fija vs TAMAR)")
    cols = st.columns(len(POR_TIPO["TAMAR"]))
    for col, tam in zip(cols, POR_TIPO["TAMAR"]):
        fija = par_mas_cercano(tam, POR_TIPO["Tasa fija"])
        r = be_tamar(fija, precios_ars[fija.ticker], tam, precios_ars[tam.ticker], mercado)
        col.metric(f"{fija.ticker} / {tam.ticker}", f"{r['valor']:.2%} TNA",
                   f"{r['valor'] - mercado.tamar_actual:+.2%} vs TAMAR actual", delta_color="off")
        col.caption(
            f"TAMAR promedio (TNA, bancos privados) implícita para las fechas **{fmt_fecha(r['desde'])} a "
            f"{fmt_fecha(r['hasta'])}** (el bono capitaliza la TAMAR de 10 hábiles antes; último dato publicado: "
            f"{fmt_fecha(r['ultimo_dato'])}). Margen de {tam.ticker}: {tam.margen:.2%}. TEA equivalente: "
            f"{r['tea']:.2%}. Par: {_par_txt(fija, tam)}."
        )
        resumen.append({"Indicador": "TAMAR BE (TNA)", "Par": f"{fija.ticker}/{tam.ticker}",
                        "Valor": f"{r['valor']:.2%}", "Período": f"{fmt_fecha(r['desde'])} – {fmt_fecha(r['hasta'])}"})

    st.markdown("### Dólar breakeven (tasa fija vs dólar linked)")
    cols = st.columns(max(2, len(POR_TIPO["Dólar linked"])))
    for col, dl in zip(cols, POR_TIPO["Dólar linked"]):
        fija = par_mas_cercano(dl, POR_TIPO["Tasa fija"])
        r = be_dolar(fija, precios_ars[fija.ticker], dl, precios_ars[dl.ticker], mercado)
        col.metric(f"{fija.ticker} / {dl.ticker}", f"$ {r['valor']:,.2f}",
                   f"{r['variacion']:+.2%} vs A3500 actual", delta_color="off")
        col.caption(
            f"A3500 implícito al **{fmt_fecha(r['fecha'])}** (vto − 3 hábiles). Devaluación implícita "
            f"{r['mensual']:.2%} mensual ({r['anual']:.1%} anual) desde $ {r['a3500_hoy']:,.2f}. "
            f"Par: {_par_txt(fija, dl)}."
        )
        resumen.append({"Indicador": "Dólar BE (A3500)", "Par": f"{fija.ticker}/{dl.ticker}",
                        "Valor": f"$ {r['valor']:,.2f}", "Período": f"al {fmt_fecha(r['fecha'])}"})

    st.markdown("### Tipo de cambio real esperado (CER vs dólar linked)")
    cols = st.columns(max(2, len(POR_TIPO["Dólar linked"])))
    for col, dl in zip(cols, POR_TIPO["Dólar linked"]):
        cer = par_mas_cercano(dl, POR_TIPO["CER"])
        r = tcr_esperado(cer, precios_ars[cer.ticker], dl, precios_ars[dl.ticker], mercado)
        signo = "depreciación real" if r["anual"] >= 0 else "apreciación real"
        col.metric(f"{cer.ticker} / {dl.ticker}", f"{r['anual']:+.2%} anual", signo, delta_color="off")
        col.caption(
            f"Variación del A3500 deflactado por CER que iguala ambos bonos, entre hoy y el **"
            f"{fmt_fecha(r['fecha_a3500'])}** (A3500 vto − 3 hábiles; CER al {fmt_fecha(r['fecha_cer'])}). "
            f"Positivo = el peso se deprecia en términos reales (sube el TCR). Acumulado: {r['acumulada']:+.2%}. "
            f"Tasa real CER {r['r_cer']:.2%} vs tasa en USD del DL {r['r_dl']:.2%}. Par: {_par_txt(cer, dl)}."
        )
        resumen.append({"Indicador": "TCR esperado (% anual)", "Par": f"{cer.ticker}/{dl.ticker}",
                        "Valor": f"{r['anual']:+.2%}", "Período": f"hasta {fmt_fecha(r['fecha_a3500'])}"})

    st.markdown("### Tasa real breakeven (TAMAR vs CER)")
    cols = st.columns(len(POR_TIPO["TAMAR"]))
    for col, tam in zip(cols, POR_TIPO["TAMAR"]):
        cer = par_mas_cercano(tam, POR_TIPO["CER"])
        r = tasa_real_be(tam, precios_ars[tam.ticker], cer, precios_ars[cer.ticker], mercado)
        col.metric(f"{tam.ticker} / {cer.ticker}", f"{r['sin_margen']:.2%} real anual",
                   f"{r['con_margen']:.2%} incluyendo margen", delta_color="off")
        col.caption(
            f"TAMAR real (deflactada por CER, TEA) que iguala ambos bonos hasta el **{fmt_fecha(r['hasta_tamar'])}** "
            f"(última TAMAR que capitaliza {tam.ticker}; CER de {cer.ticker} al {fmt_fecha(r['hasta_cer'])}). "
            f"Neto del margen de {r['margen']:.2%} (aprox.). Tasa real del CER: {r['r_cer']:.2%}. "
            f"Par: {_par_txt(cer, tam)}."
        )
        resumen.append({"Indicador": "Tasa real BE (TAMAR real)", "Par": f"{tam.ticker}/{cer.ticker}",
                        "Valor": f"{r['sin_margen']:.2%}", "Período": f"hasta {fmt_fecha(r['hasta_tamar'])}"})

    st.markdown("### Resumen")
    st.dataframe(pd.DataFrame(resumen), hide_index=True, width="stretch")

st.divider()
st.caption(
    "Precios: data912.com (si falta una especie se usa el último cierre o un precio fijo de ejemplo, "
    "indicado en la columna Fuente). Variables: API de Estadísticas Monetarias del BCRA. "
    f"Condiciones de emisión verificadas contra 1816 al {fmt_fecha(FECHA_REF)}. No es recomendación de inversión."
)
