"""
Dashboard de yucatan_dw (Streamlit).

Lee SOLO de la capa gold a través de la API (api/main.py): vistas kpi_municipio_anio y
cociente_localizacion, hechos y dimensiones, y las tablas resultado_* de src/gold_analytics.py.

Correr local:   streamlit run dashboard/main.py   (con la API corriendo en API_URL, por defecto localhost:8000)
Correr Docker:  docker compose up -d  ->  http://localhost:8501
"""

import os

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
API_URL = os.environ.get("API_URL", "http://localhost:8000")
ANIOS = list(range(2020, 2026))

# Paleta (series categóricas en orden fijo, rampa secuencial azul, divergente azul <-> rojo)
AZUL, NARANJA, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GRIS = "#8a8984"
SECUENCIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGENTE = [[0, "#184f95"], [0.25, "#6da7ec"], [0.5, "#f0efec"], [0.75, "#ec8c8b"], [1, "#b42f2f"]]
COLORES_LISA = {
    "HH": "#d03b3b",
    "HL": "#ec8c8b",
    "LH": "#86b6ef",
    "LL": "#256abf",
    "no significativo": "#e4e3df",
}
ETIQUETAS_LISA = {
    "HH": "HH · alto rodeado de altos",
    "HL": "HL · alto rodeado de bajos",
    "LH": "LH · bajo rodeado de altos",
    "LL": "LL · bajo rodeado de bajos",
    "no significativo": "No significativo",
}

# Indicadores de kpi_municipio_anio: columna -> (etiqueta, formato)
INDICADORES = {
    "poblacion_total": ("Población total (Censo 2020)", ",.0f"),
    "densidad_poblacional": ("Densidad poblacional (hab/km²)", ",.1f"),
    "tasa_pea": ("Tasa PEA (PEA / pob. 12+)", ".1%"),
    "prop_0_14": ("Proporción 0–14 años", ".1%"),
    "prop_15_64": ("Proporción 15–64 años", ".1%"),
    "prop_65_mas": ("Proporción 65+ años", ".1%"),
    "total_negocios": ("Total de negocios", ",.0f"),
    "densidad_negocios": ("Densidad de negocios (por km²)", ",.2f"),
    "negocios_por_1000_hab": ("Negocios por 1,000 hab", ",.1f"),
    "densidad_retail": ("Densidad retail (sector 46, por km²)", ",.2f"),
    "densidad_servicios": ("Densidad de servicios (51–81, por km²)", ",.2f"),
    "total_incidentes": ("Total de incidentes", ",.0f"),
    "tasa_delictiva_1000_hab": ("Tasa delictiva por 1,000 hab", ",.2f"),
    "incidentes_por_100_negocios": ("Incidentes por cada 100 negocios", ",.2f"),
}

st.set_page_config(page_title="Yucatán Urban Intelligence", page_icon="🗺️", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; }
    [data-testid="stMetric"] {
        background: #ffffff; border: 1px solid #e4e3df; border-radius: 10px; padding: 14px 16px;
    }
    [data-testid="stMetricLabel"] { color: #52514e; }
    .nota { color: #52514e; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Datos (todo desde gold)
# --------------------------------------------------------------------------
@st.cache_data(ttl=600)
def api(ruta: str):
    r = requests.get(f"{API_URL}{ruta}", timeout=60)
    r.raise_for_status()
    return r.json()


def consulta(ruta: str) -> pd.DataFrame:
    return pd.DataFrame(api(ruta))


def geojson() -> dict:
    return api("/municipios/geojson")


def kpi() -> pd.DataFrame:
    return consulta("/kpi")


def incidencia_mensual() -> pd.DataFrame:
    return consulta("/incidencia-mensual")


def lq() -> pd.DataFrame:
    return consulta("/cociente-localizacion")


# --------------------------------------------------------------------------
# Gráficas
# --------------------------------------------------------------------------
def estilo(fig: go.Figure, alto: int = 380) -> go.Figure:
    fig.update_layout(
        height=alto,
        margin=dict(l=10, r=10, t=40, b=10),
        font=dict(family="Inter, system-ui, sans-serif", size=13, color="#0b0b0b"),
        title_font=dict(size=15),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
        hoverlabel=dict(bgcolor="white", font_size=13),
    )
    fig.update_xaxes(showgrid=False, linecolor="#c3c2b7", ticks="outside", tickcolor="#c3c2b7")
    fig.update_yaxes(gridcolor="#ecebe7", zeroline=False, linecolor="rgba(0,0,0,0)")
    return fig


def mapa(df: pd.DataFrame, columna: str, titulo: str, formato: str, escala=None, punto_medio=None,
         hover_extra: dict | None = None, alto: int = 520) -> go.Figure:
    fig = px.choropleth(
        df, geojson=geojson(), locations="cvegeo", featureidkey="properties.cvegeo",
        color=columna, color_continuous_scale=escala or SECUENCIAL,
        color_continuous_midpoint=punto_medio,
        hover_name="nombre", hover_data={"cvegeo": False, columna: f":{formato}", **(hover_extra or {})},
        labels={columna: titulo},
    )
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_traces(marker_line_color="white", marker_line_width=0.6)
    fig.update_layout(coloraxis_colorbar=dict(title=None, thickness=12, len=0.7, tickformat=formato))
    return estilo(fig, alto).update_layout(margin=dict(l=0, r=0, t=10, b=0))


def mapa_lisa(df: pd.DataFrame, alto: int = 520) -> go.Figure:
    df = df.assign(cluster=df["cuadrante"].map(ETIQUETAS_LISA))
    fig = px.choropleth(
        df, geojson=geojson(), locations="cvegeo", featureidkey="properties.cvegeo",
        color="cluster", hover_name="nombre",
        hover_data={"cvegeo": False, "cluster": True, "valor": ":,.2f", "p_sim": ":.3f"},
        color_discrete_map={ETIQUETAS_LISA[k]: v for k, v in COLORES_LISA.items()},
        category_orders={"cluster": list(ETIQUETAS_LISA.values())},
    )
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_traces(marker_line_color="white", marker_line_width=0.6)
    return estilo(fig, alto).update_layout(margin=dict(l=0, r=0, t=30, b=0))


def fmt(valor, formato: str) -> str:
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return "—"
    return format(valor, formato)


# --------------------------------------------------------------------------
# Encabezado y filtros
# --------------------------------------------------------------------------
datos = kpi()
municipios = datos[["cvegeo", "nombre"]].drop_duplicates().sort_values("nombre")

st.title("Yucatán Urban Intelligence")
st.markdown(
    '<p class="nota">Panel municipal 2020–2025 · 106 municipios · Censo 2020 (INEGI), DENUE (INEGI), '
    "incidencia delictiva del fuero común (SESNSP). Fuente: capa <code>gold</code> de <code>yucatan_dw</code>.</p>",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Filtros")
    anio = st.select_slider("Año", options=ANIOS, value=ANIOS[-1])
    nombre_mun = st.selectbox("Municipio", municipios["nombre"], index=int(np.where(municipios["nombre"] == "Mérida")[0][0]))
    cvegeo_mun = municipios.loc[municipios["nombre"] == nombre_mun, "cvegeo"].iloc[0]
    st.divider()
    st.caption(
        "**Supuestos.** La población es la del Censo 2020 para todos los años. "
        "DENUE: edición de noviembre de cada año; 2025 usa la de mayo."
    )
    st.caption(
        "**Cautelas.** Mérida es un outlier; las tasas son inestables en municipios pequeños; "
        "asociación espacial no implica causalidad."
    )

del_anio = datos[datos["anio"] == anio]

tabs = st.tabs(["Panorama estatal", "Mapa municipal", "Municipio", "Crimen",
                "Actividad económica", "Análisis espacial", "Calidad de datos"])


# --------------------------------------------------------------------------
# 1. Panorama estatal
# --------------------------------------------------------------------------
with tabs[0]:
    estatal = datos.groupby("anio").agg(
        poblacion=("poblacion_total", "sum"),
        negocios=("total_negocios", "sum"),
        incidentes=("total_incidentes", "sum"),
    ).reset_index()
    estatal["tasa"] = 1000 * estatal["incidentes"] / estatal["poblacion"]
    estatal["neg_1000"] = 1000 * estatal["negocios"] / estatal["poblacion"]
    actual = estatal[estatal["anio"] == anio].iloc[0]
    previo = estatal[estatal["anio"] == anio - 1]

    def delta(col, formato):
        if previo.empty:
            return None
        anterior = previo.iloc[0][col]
        return f"{format(actual[col] - anterior, formato)} vs {anio - 1} ({(actual[col] / anterior - 1):+.1%})"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Población (Censo 2020)", fmt(actual["poblacion"], ",.0f"))
    c2.metric(f"Negocios DENUE {anio}", fmt(actual["negocios"], ",.0f"), delta("negocios", "+,.0f"))
    c3.metric(f"Incidentes {anio}", fmt(actual["incidentes"], ",.0f"), delta("incidentes", "+,.0f"),
              delta_color="inverse")
    c4.metric("Tasa delictiva estatal", f"{actual['tasa']:.2f} por 1,000", delta("tasa", "+.2f"),
              delta_color="inverse")

    izq, der = st.columns(2)
    with izq:
        fig = px.line(estatal, x="anio", y="negocios", markers=True, title="Negocios en el estado por año",
                      labels={"anio": "", "negocios": "Negocios"}, color_discrete_sequence=[AZUL])
        fig.update_traces(line_width=2, marker_size=8, hovertemplate="%{x}: %{y:,.0f} negocios<extra></extra>")
        fig.update_xaxes(dtick=1)
        st.plotly_chart(estilo(fig), width="stretch")
        st.caption("2020–2023 con SCIAN 2018; 2024–2025 con SCIAN 2023. Ver «Calidad de datos».")
    with der:
        fig = px.line(estatal, x="anio", y="incidentes", markers=True, title="Incidentes en el estado por año",
                      labels={"anio": "", "incidentes": "Incidentes"}, color_discrete_sequence=[NARANJA])
        fig.update_traces(line_width=2, marker_size=8, hovertemplate="%{x}: %{y:,.0f} incidentes<extra></extra>")
        fig.update_xaxes(dtick=1)
        st.plotly_chart(estilo(fig), width="stretch")
        st.caption("La caída de 2022 parece un cambio de registro de la fuente. Ver «Calidad de datos».")

    st.subheader(f"Municipios destacados en {anio}")
    izq, der = st.columns(2)
    for contenedor, columna, color in [(izq, "tasa_delictiva_1000_hab", NARANJA), (der, "negocios_por_1000_hab", AZUL)]:
        etiqueta, formato = INDICADORES[columna]
        top = del_anio.nlargest(10, columna).sort_values(columna)
        fig = px.bar(top, x=columna, y="nombre", orientation="h", title=f"Top 10 · {etiqueta}",
                     labels={columna: "", "nombre": ""}, color_discrete_sequence=[color], text=columna)
        fig.update_traces(texttemplate=f"%{{x:{formato}}}", textposition="outside", cliponaxis=False,
                          hovertemplate=f"%{{y}}: %{{x:{formato}}}<extra></extra>")
        fig.update_xaxes(showticklabels=False)
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        contenedor.plotly_chart(estilo(fig, 420), width="stretch")

    dominante = del_anio.groupby(["sector_dominante", "desc_sector_dominante"]).size().reset_index(name="municipios")
    st.markdown(f"**Actividad dominante en {anio}** (sector SCIAN con más negocios en cada municipio)")
    st.dataframe(
        dominante.sort_values("municipios", ascending=False).rename(columns={
            "sector_dominante": "Sector", "desc_sector_dominante": "Descripción", "municipios": "Municipios"}),
        hide_index=True, width="stretch",
    )


# --------------------------------------------------------------------------
# 2. Mapa municipal
# --------------------------------------------------------------------------
with tabs[1]:
    columna = st.selectbox("Indicador", list(INDICADORES), index=list(INDICADORES).index("tasa_delictiva_1000_hab"),
                           format_func=lambda c: INDICADORES[c][0])
    etiqueta, formato = INDICADORES[columna]
    escala_log = st.toggle("Escala logarítmica de color (útil por el peso de Mérida)", value=False)

    vista = del_anio.copy()
    color_col = columna
    if escala_log:
        color_col = f"log10 · {etiqueta}"
        vista[color_col] = np.log10(vista[columna].where(vista[columna] > 0))

    izq, der = st.columns([3, 2])
    with izq:
        st.markdown(f"**{etiqueta} · {anio}**")
        fig = mapa(vista, color_col, etiqueta, ".2f" if escala_log else formato,
                   hover_extra={columna: f":{formato}"} if escala_log else None)
        st.plotly_chart(fig, width="stretch")
    with der:
        st.markdown("**Ranking**")
        ranking = vista[["nombre", columna]].sort_values(columna, ascending=False).reset_index(drop=True)
        ranking.index += 1
        st.dataframe(ranking.rename(columns={"nombre": "Municipio", columna: etiqueta}),
                     width="stretch", height=520,
                     column_config={etiqueta: st.column_config.NumberColumn(format="%.2f")})


# --------------------------------------------------------------------------
# 3. Municipio
# --------------------------------------------------------------------------
with tabs[2]:
    mun = datos[datos["cvegeo"] == cvegeo_mun].sort_values("anio")
    fila = mun[mun["anio"] == anio].iloc[0]
    mediana = del_anio[list(INDICADORES)].median()

    st.subheader(f"{nombre_mun} · {anio}")
    st.caption(f"cvegeo {cvegeo_mun} · Actividad dominante: {fila['sector_dominante']} – {fila['desc_sector_dominante']}")

    tiles = ["poblacion_total", "densidad_poblacional", "tasa_pea", "total_negocios",
             "negocios_por_1000_hab", "total_incidentes", "tasa_delictiva_1000_hab", "incidentes_por_100_negocios"]
    for fila_tiles in (tiles[:4], tiles[4:]):
        for col, c in zip(st.columns(4), fila_tiles):
            etiqueta, formato = INDICADORES[c]
            col.metric(etiqueta, fmt(fila[c], formato), f"mediana estatal: {fmt(mediana[c], formato)}",
                       delta_color="off")

    estructura = pd.DataFrame({
        "grupo": ["0–14", "15–64", "65+"],
        "proporción": [fila["prop_0_14"], fila["prop_15_64"], fila["prop_65_mas"]],
    })

    izq, centro, der = st.columns(3)
    for contenedor, c in [(izq, "tasa_delictiva_1000_hab"), (centro, "negocios_por_1000_hab")]:
        etiqueta, formato = INDICADORES[c]
        serie = pd.concat([
            mun[["anio", c]].assign(serie=nombre_mun),
            datos.groupby("anio")[c].median().reset_index().assign(serie="Mediana estatal"),
        ])
        fig = px.line(serie, x="anio", y=c, color="serie", markers=True, title=etiqueta,
                      labels={"anio": "", c: ""}, color_discrete_map={nombre_mun: AZUL, "Mediana estatal": GRIS})
        fig.update_traces(line_width=2, marker_size=8, hovertemplate=f"%{{x}}: %{{y:{formato}}}<extra></extra>")
        fig.update_traces(selector=dict(name="Mediana estatal"), line_dash="dot")
        fig.update_xaxes(dtick=1)
        contenedor.plotly_chart(estilo(fig, 340), width="stretch")
    with der:
        fig = px.bar(estructura, x="grupo", y="proporción", title="Estructura de edad (Censo 2020)",
                     labels={"grupo": "", "proporción": ""}, color_discrete_sequence=[AQUA], text="proporción")
        fig.update_traces(texttemplate="%{y:.1%}", textposition="outside", cliponaxis=False,
                          hovertemplate="%{x}: %{y:.1%}<extra></extra>", marker_cornerradius=4)
        fig.update_yaxes(tickformat=".0%")
        der.plotly_chart(estilo(fig, 340), width="stretch")

    izq, der = st.columns(2)
    with izq:
        sectores = lq()
        sectores = sectores[(sectores["cvegeo"] == cvegeo_mun) & (sectores["anio"] == anio)].sort_values("lq")
        sectores["etiqueta"] = sectores["codigo_sector"] + " · " + sectores["desc_sector"].str.slice(0, 45)
        fig = px.bar(sectores, x="lq", y="etiqueta", orientation="h",
                     title="Cociente de localización por sector (LQ > 1 = especialización)",
                     labels={"lq": "LQ", "etiqueta": ""}, color_discrete_sequence=[AZUL],
                     custom_data=["negocios_sector", "negocios_municipio"])
        fig.update_traces(hovertemplate="%{y}<br>LQ %{x:.2f}<br>%{customdata[0]:,} de %{customdata[1]:,} negocios"
                                        "<extra></extra>", marker_cornerradius=4)
        fig.add_vline(x=1, line_dash="dot", line_color=GRIS)
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(estilo(fig, max(360, 26 * len(sectores))), width="stretch")
    with der:
        inc = incidencia_mensual()
        inc = inc[(inc["cvegeo"] == cvegeo_mun) & (inc["anio"] == anio)]
        por_tipo = inc.groupby("tipo_delito", as_index=False)["incidentes"].sum().nlargest(12, "incidentes")
        fig = px.bar(por_tipo.sort_values("incidentes"), x="incidentes", y="tipo_delito", orientation="h",
                     title=f"Incidentes por tipo de delito · {anio}", labels={"incidentes": "", "tipo_delito": ""},
                     color_discrete_sequence=[NARANJA], text="incidentes")
        fig.update_traces(textposition="outside", cliponaxis=False, marker_cornerradius=4,
                          hovertemplate="%{y}: %{x:,} incidentes<extra></extra>")
        fig.update_xaxes(showticklabels=False)
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(estilo(fig, max(360, 26 * len(por_tipo))), width="stretch")


# --------------------------------------------------------------------------
# 4. Crimen
# --------------------------------------------------------------------------
with tabs[3]:
    inc = incidencia_mensual()
    tipos = sorted(inc["tipo_delito"].unique())
    elegidos = st.multiselect("Tipos de delito (vacío = todos)", tipos, placeholder="Todos los tipos")
    if elegidos:
        inc = inc[inc["tipo_delito"].isin(elegidos)]

    mensual = inc.groupby(["id_tiempo", "anio", "mes", "nombre_mes"], as_index=False)["incidentes"].sum()
    mensual["fecha"] = pd.to_datetime(dict(year=mensual["anio"], month=mensual["mes"], day=1))
    fig = px.area(mensual.sort_values("fecha"), x="fecha", y="incidentes", title="Incidentes mensuales en el estado",
                  labels={"fecha": "", "incidentes": "Incidentes"}, color_discrete_sequence=[NARANJA])
    fig.update_traces(line_width=2, hovertemplate="%{x|%B %Y}: %{y:,} incidentes<extra></extra>")
    fig.update_layout(hovermode="x")
    st.plotly_chart(estilo(fig, 340), width="stretch")

    izq, der = st.columns(2)
    with izq:
        top = inc[inc["anio"] == anio].groupby("tipo_delito", as_index=False)["incidentes"].sum()
        top = top.nlargest(10, "incidentes").sort_values("incidentes")
        fig = px.bar(top, x="incidentes", y="tipo_delito", orientation="h", title=f"Top 10 tipos de delito · {anio}",
                     labels={"incidentes": "", "tipo_delito": ""}, color_discrete_sequence=[NARANJA], text="incidentes")
        fig.update_traces(textposition="outside", cliponaxis=False, marker_cornerradius=4,
                          hovertemplate="%{y}: %{x:,} incidentes<extra></extra>")
        fig.update_xaxes(showticklabels=False)
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(estilo(fig, 420), width="stretch")
    with der:
        por_mun = inc[inc["anio"] == anio].groupby("cvegeo", as_index=False)["incidentes"].sum()
        por_mun = del_anio[["cvegeo", "nombre", "poblacion_total"]].merge(por_mun, on="cvegeo", how="left").fillna(
            {"incidentes": 0})
        por_mun["tasa"] = 1000 * por_mun["incidentes"] / por_mun["poblacion_total"]
        st.markdown(f"**Tasa por 1,000 hab · {anio}**" + (" · tipos seleccionados" if elegidos else ""))
        st.plotly_chart(mapa(por_mun, "tasa", "Tasa por 1,000 hab", ".2f",
                             hover_extra={"incidentes": ":,.0f"}, alto=400), width="stretch")

    anual = inc.groupby(["tipo_delito", "anio"], as_index=False)["incidentes"].sum()
    principales = anual.groupby("tipo_delito")["incidentes"].sum().nlargest(12).index
    matriz = anual[anual["tipo_delito"].isin(principales)].pivot(index="tipo_delito", columns="anio",
                                                                  values="incidentes").fillna(0)
    matriz = matriz.loc[matriz.sum(axis=1).sort_values(ascending=False).index]
    fig = px.imshow(matriz, text_auto=",.0f", aspect="auto", color_continuous_scale=SECUENCIAL,
                    title="Incidentes por tipo de delito y año (12 tipos con más incidentes)",
                    labels={"x": "", "y": "", "color": "Incidentes"})
    fig.update_xaxes(dtick=1, side="top")
    fig.update_traces(xgap=2, ygap=2, hovertemplate="%{y} · %{x}: %{z:,.0f}<extra></extra>")
    fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(estilo(fig, 460), width="stretch")


# --------------------------------------------------------------------------
# 5. Actividad económica
# --------------------------------------------------------------------------
with tabs[4]:
    cl = lq()
    cl_anio = cl[cl["anio"] == anio]

    izq, der = st.columns([2, 3])
    with izq:
        por_sector = cl_anio.groupby(["codigo_sector", "desc_sector"], as_index=False)["negocios_sector"].sum()
        por_sector["participación"] = por_sector["negocios_sector"] / por_sector["negocios_sector"].sum()
        por_sector["etiqueta"] = por_sector["codigo_sector"] + " · " + por_sector["desc_sector"].str.slice(0, 38)
        fig = px.bar(por_sector.sort_values("negocios_sector"), x="negocios_sector", y="etiqueta", orientation="h",
                     title=f"Negocios por sector en el estado · {anio}", color_discrete_sequence=[AZUL],
                     labels={"negocios_sector": "", "etiqueta": ""}, custom_data=["participación"])
        fig.update_traces(marker_cornerradius=4,
                          hovertemplate="%{y}<br>%{x:,} negocios (%{customdata[0]:.1%})<extra></extra>")
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(estilo(fig, 620), width="stretch")
    with der:
        sectores = por_sector.sort_values("negocios_sector", ascending=False)
        sector = st.selectbox("Sector para el mapa de especialización", sectores["codigo_sector"],
                              format_func=lambda s: sectores.set_index("codigo_sector").loc[s, "etiqueta"])
        especial = municipios.merge(cl_anio[cl_anio["codigo_sector"] == sector], on="cvegeo", how="left")
        especial["lq"] = especial["lq"].fillna(0)
        especial["log2 LQ"] = np.log2(especial["lq"].where(especial["lq"] > 0)).clip(-3, 3)
        st.markdown(f"**Cociente de localización · sector {sector} · {anio}**")
        fig = mapa(especial, "log2 LQ", "log2 LQ", ".1f", escala=DIVERGENTE, punto_medio=0,
                   hover_extra={"lq": ":.2f", "negocios_sector": ":,.0f"}, alto=560)
        fig.update_layout(coloraxis=dict(cmin=-3, cmax=3, colorbar=dict(
            tickvals=[-3, -2, -1, 0, 1, 2, 3], ticktext=["≤ 0.125", "0.25", "0.5", "1", "2", "4", "≥ 8"])))
        st.plotly_chart(fig, width="stretch")
        st.caption("Rojo = el sector pesa más en el municipio que en el estado (LQ > 1); azul = menos. "
                   "Gris claro sin color = municipio sin negocios del sector.")

    st.markdown(f"**Mayores especializaciones · {anio}** (sectores con al menos 20 negocios en el municipio)")
    top_lq = cl_anio[cl_anio["negocios_sector"] >= 20].merge(municipios, on="cvegeo").nlargest(15, "lq")
    st.dataframe(
        top_lq[["nombre", "codigo_sector", "desc_sector", "negocios_sector", "negocios_municipio", "lq"]].rename(
            columns={"nombre": "Municipio", "codigo_sector": "Sector", "desc_sector": "Descripción",
                     "negocios_sector": "Negocios del sector", "negocios_municipio": "Negocios del municipio",
                     "lq": "LQ"}),
        hide_index=True, width="stretch", column_config={"LQ": st.column_config.NumberColumn(format="%.2f")},
    )


# --------------------------------------------------------------------------
# 6. Análisis espacial
# --------------------------------------------------------------------------
with tabs[5]:
    correlacion = consulta("/resultados/correlacion")
    moran = consulta("/resultados/moran-global")
    bivariado = consulta("/resultados/moran-bivariado")
    resultado_lisa = consulta("/resultados/lisa")

    st.markdown("Pesos Queen desde `gold.vecinos_municipio`, estandarizados por fila · 999 permutaciones · α = 0.05")

    st.subheader("Moran global")
    izq, der = st.columns([3, 2])
    with izq:
        m = moran.assign(indicador=moran["indicador"].map(lambda c: INDICADORES[c][0]))
        fig = px.line(m, x="anio", y="i", color="indicador", markers=True, title="I de Moran por año",
                      labels={"anio": "", "i": "I de Moran"},
                      color_discrete_sequence=[NARANJA, AZUL], custom_data=["z", "p_sim"])
        fig.update_traces(line_width=2, marker_size=8,
                          hovertemplate="%{x}: I = %{y:.3f}<br>z = %{customdata[0]:.2f} · p = %{customdata[1]:.3f}"
                                        "<extra></extra>")
        fig.add_hline(y=m["esperanza_i"].iloc[0], line_dash="dot", line_color=GRIS,
                      annotation_text="E[I] (sin autocorrelación)", annotation_position="bottom right")
        fig.update_xaxes(dtick=1)
        st.plotly_chart(estilo(fig, 360), width="stretch")
    with der:
        tabla = moran.pivot(index="anio", columns="indicador", values="p_sim")
        tabla.columns = [INDICADORES[c][0] for c in tabla.columns]
        st.markdown("**p_sim por año**")
        st.dataframe(tabla.style.format("{:.3f}"), width="stretch")
        st.caption("Ambos indicadores muestran autocorrelación espacial positiva y significativa en todos los años.")

    st.subheader("LISA (Moran local)")
    izq, der = st.columns([3, 2])
    with izq:
        indicador = st.radio("Indicador", moran["indicador"].unique(), horizontal=True,
                             format_func=lambda c: INDICADORES[c][0])
        l = resultado_lisa[(resultado_lisa["anio"] == anio) & (resultado_lisa["indicador"] == indicador)]
        l = l.merge(del_anio[["cvegeo", "nombre", indicador]].rename(columns={indicador: "valor"}), on="cvegeo")
        st.markdown(f"**Clusters LISA · {INDICADORES[indicador][0]} · {anio}**")
        st.plotly_chart(mapa_lisa(l), width="stretch")
    with der:
        conteo = l["cuadrante"].value_counts().reindex(list(ETIQUETAS_LISA), fill_value=0)
        st.markdown("**Municipios por cluster**")
        st.dataframe(pd.DataFrame({"Cluster": [ETIQUETAS_LISA[k] for k in conteo.index], "Municipios": conteo.values}),
                     hide_index=True, width="stretch")
        significativos = l[l["cuadrante"] != "no significativo"].sort_values(["cuadrante", "nombre"])
        st.markdown("**Municipios significativos**")
        st.dataframe(significativos[["nombre", "cuadrante", "valor", "p_sim"]].rename(columns={
            "nombre": "Municipio", "cuadrante": "Cluster", "valor": "Valor", "p_sim": "p_sim"}),
            hide_index=True, width="stretch", height=300,
            column_config={"Valor": st.column_config.NumberColumn(format="%.2f"),
                           "p_sim": st.column_config.NumberColumn(format="%.3f")})

    st.subheader("Correlaciones")
    pares = correlacion[["variable_x", "variable_y"]].drop_duplicates()
    par = st.selectbox("Par de variables", list(pares.itertuples(index=False)),
                       format_func=lambda p: f"{INDICADORES[p[0]][0]}  vs  {INDICADORES[p[1]][0]}")
    x, y = par
    izq, der = st.columns([3, 2])
    with izq:
        log = st.toggle("Ejes logarítmicos", value=x.startswith("densidad"))
        puntos = del_anio.assign(es_merida=np.where(del_anio["nombre"] == "Mérida", "Mérida", "Resto de municipios"))
        fig = px.scatter(puntos, x=x, y=y, color="es_merida", hover_name="nombre", log_x=log, log_y=log,
                         labels={x: INDICADORES[x][0], y: INDICADORES[y][0]},
                         color_discrete_map={"Mérida": NARANJA, "Resto de municipios": AZUL},
                         title=f"Dispersión · {anio}")
        fig.update_traces(marker=dict(size=10, opacity=0.8, line=dict(width=1.5, color="white")))
        st.plotly_chart(estilo(fig, 420), width="stretch")
    with der:
        c = correlacion[(correlacion["variable_x"] == x) & (correlacion["variable_y"] == y)]
        actual = c[c["anio"] == anio].set_index("metodo")
        m1, m2 = st.columns(2)
        m1.metric("Pearson", f"{actual.loc['pearson', 'coeficiente']:.3f}", f"p = {actual.loc['pearson', 'p_value']:.3f}",
                  delta_color="off")
        m2.metric("Spearman", f"{actual.loc['spearman', 'coeficiente']:.3f}",
                  f"p = {actual.loc['spearman', 'p_value']:.3f}", delta_color="off")
        fig = px.line(c, x="anio", y="coeficiente", color="metodo", markers=True, title="Coeficiente por año",
                      labels={"anio": "", "coeficiente": "", "metodo": ""},
                      color_discrete_map={"pearson": AZUL, "spearman": AQUA}, custom_data=["p_value"])
        fig.update_traces(line_width=2, marker_size=8,
                          hovertemplate="%{x}: %{y:.3f} (p = %{customdata[0]:.3f})<extra></extra>")
        fig.add_hline(y=0, line_color="#c3c2b7")
        fig.update_xaxes(dtick=1)
        fig.update_yaxes(range=[-1, 1])
        st.plotly_chart(estilo(fig, 300), width="stretch")

    st.subheader("Moran bivariado")
    b = bivariado.copy()
    b["par"] = b["variable_x"].map(lambda c: INDICADORES[c][0]) + " vs rezago de " + b["variable_y"].map(
        lambda c: INDICADORES[c][0])
    b["significativo"] = np.where(b["p_sim"] < 0.05, "Sí", "No")
    st.dataframe(b[["anio", "par", "i", "z", "p_sim", "significativo"]].rename(columns={
        "anio": "Año", "par": "Par", "i": "I", "z": "z (sim)", "p_sim": "p_sim", "significativo": "¿Significativo?"}),
        hide_index=True, width="stretch",
        column_config={c: st.column_config.NumberColumn(format="%.3f") for c in ["I", "z (sim)", "p_sim"]})


# --------------------------------------------------------------------------
# 7. Calidad de datos
# --------------------------------------------------------------------------
with tabs[6]:
    st.subheader("Avisos sobre la fuente")
    st.warning(
        "**Caída abrupta de incidencia en 2022.** Los incidentes del estado pasan de 8,565 (2021) a 4,209 (2022); "
        "en Mérida de 6,522 a 1,820. Un cambio de esta magnitud parece de registro de la fuente (SESNSP), "
        "no de criminalidad real.", icon="⚠️",
    )
    st.warning(
        "**Salto del DENUE en 2024.** El total pasa de ~130 mil a ~142 mil establecimientos, justo cuando la "
        "clasificación cambia de SCIAN 2018 a SCIAN 2023. Puede ser un efecto de la fuente.", icon="⚠️",
    )
    st.info(
        "**`fact_establecimiento` es un snapshot por edición: no se suma entre ediciones.** "
        "Cada año del panel usa una sola edición (noviembre; 2025 usa mayo).", icon="ℹ️",
    )

    st.subheader("Última carga de gold")
    log = consulta("/etl-log")
    log["fecha_carga"] = pd.to_datetime(log["fecha_carga"])
    st.dataframe(log, hide_index=True, width="stretch")
