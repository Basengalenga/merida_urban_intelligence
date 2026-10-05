"""
Mapas y figuras estáticos desde la capa gold de yucatan_dw.

Lee SOLO de gold (kpi_municipio_anio, resultado_*, fact_incidencia, dim_*,
vecinos_municipio) y escribe PNG en outputs/:

    outputs/maps/<anio>/         coropletas de KPIs, mapas LISA y sector dominante
    outputs/figures/<anio>/      dispersión de correlaciones, diagramas de Moran,
                                 incidentes por tipo de delito
    outputs/figures/serie/       series 2020-2025 (Moran global, incidentes, negocios)

No forma parte del DAG: se corre a mano cuando gold ya está cargado, en un
contenedor desechable con la imagen de Airflow (ver README). Cada ejecución
sobrescribe los PNG.

Las coropletas de un mismo KPI usan los mismos cortes en todos los años
(quintiles del panel 2020-2025), para que los mapas de distintos años sean comparables.
"""

import os
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from sqlalchemy import create_engine

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
DB_URL = os.environ.get("DB_URL", "postgresql+psycopg2://admin:admin123@db:5432/yucatan_dw")
SALIDA = Path(__file__).resolve().parent.parent / "outputs"

ANIOS_PANEL = range(2020, 2026)
DPI = 200
ALFA = 0.05

# Coropletas: columna de kpi_municipio_anio -> (título, unidad, decimales)
INDICADORES_MAPA = {
    "poblacion_total": ("Población total", "habitantes", 0),
    "densidad_poblacional": ("Densidad poblacional", "hab/km²", 1),
    "densidad_negocios": ("Densidad de negocios", "negocios/km²", 2),
    "negocios_por_1000_hab": ("Negocios por 1,000 habitantes", "negocios por 1,000 hab", 1),
    "tasa_delictiva_1000_hab": ("Tasa delictiva", "incidentes por 1,000 hab", 2),
    "incidentes_por_100_negocios": ("Crimen relativo a la actividad económica", "incidentes por 100 negocios", 2),
}
ETIQUETAS = {c: t for c, (t, _, _) in INDICADORES_MAPA.items()} | {"tasa_pea": "Tasa de PEA"}

# Paleta (validada con el validador de la skill dataviz: CVD y contraste)
SUPERFICIE = "#fcfcfb"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
TINTA_TENUE = "#898781"
REJILLA = "#e1e0d9"
SERIE_1 = "#2a78d6"
SERIE_2 = "#eb6834"
SIN_DATO = "#f0efec"
SECUENCIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]  # azul, claro -> oscuro
COLORES_LISA = {"HH": "#b8312f", "HL": "#ec7c78", "LH": "#5598e7", "LL": "#1c5cab", "no significativo": REJILLA}
ETIQUETAS_LISA = {
    "HH": "Alto-Alto (cluster)", "HL": "Alto-Bajo (atípico)", "LH": "Bajo-Alto (atípico)",
    "LL": "Bajo-Bajo (cluster)", "no significativo": "No significativo",
}
# Color fijo por sector (sigue a la entidad, no al ranking)
COLORES_SECTOR = {"46": "#2a78d6", "31-33": "#eb6834", "11": "#1baf7a"}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": "#c3c2b7",
    "axes.labelcolor": TINTA_2,
    "axes.titlecolor": TINTA,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": False,
    "xtick.color": TINTA_TENUE,
    "ytick.color": TINTA_TENUE,
    "figure.facecolor": SUPERFICIE,
    "axes.facecolor": SUPERFICIE,
    "savefig.facecolor": SUPERFICIE,
    "legend.frameon": False,
})


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def guardar(fig, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(ruta, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"OK   {ruta.relative_to(SALIDA.parent)}")


def pie(fig, texto: str) -> None:
    fig.text(0.01, 0.005, texto, fontsize=7, color=TINTA_TENUE, ha="left", va="bottom")


def fmt(valor: float, decimales: int) -> str:
    return f"{valor:,.{decimales}f}"


def rejilla_y(ax) -> None:
    ax.yaxis.grid(True, color=REJILLA, linewidth=0.6)
    ax.set_axisbelow(True)


def mapa_base():
    fig, ax = plt.subplots(figsize=(8, 6.2))
    ax.set_axis_off()
    return fig, ax


def encuadrar(ax, municipios: gpd.GeoDataFrame) -> None:
    """Encuadre al territorio continental: se usa la parte más grande de cada municipio, así
    los cayos lejanos (p. ej. Cayo Arenas, en Progreso) se dibujan, pero no amplían el marco.
    Se llama después de dibujar."""
    partes = municipios.geometry.explode(index_parts=True)
    principales = partes.loc[partes.area.groupby(level=0).idxmax()]
    xmin, ymin, xmax, ymax = principales.total_bounds
    margen = 0.03 * (xmax - xmin)
    ax.set_xlim(xmin - margen, xmax + margen)
    ax.set_ylim(ymin - margen, ymax + margen)
    ax.set_aspect("equal", adjustable="box")  # geopandas usa "datalim", que vuelve a ampliar el marco


def rotular_merida(ax, gdf: gpd.GeoDataFrame) -> None:
    """Rótulo de orientación: solo Mérida."""
    merida = gdf[gdf["nombre"] == "Mérida"]
    if not merida.empty:
        p = merida.geometry.iloc[0].representative_point()
        ax.annotate("Mérida", (p.x, p.y), xytext=(-55, 30), textcoords="offset points", fontsize=8,
                    color=TINTA, arrowprops=dict(arrowstyle="-", color=TINTA_2, linewidth=0.6))


def cortes_quintiles(valores: pd.Series) -> np.ndarray:
    """Quintiles del panel completo (todos los años), sin cortes repetidos."""
    v = valores.dropna().to_numpy(dtype=float)
    return np.unique(np.quantile(v, [0, 0.2, 0.4, 0.6, 0.8, 1.0]))


# --------------------------------------------------------------------------
# Lectura desde gold
# --------------------------------------------------------------------------
def leer_gold(engine) -> dict:
    kpi = gpd.read_postgis("SELECT * FROM gold.kpi_municipio_anio", engine, geom_col="geom")
    kpi["cvegeo"] = kpi["cvegeo"].str.strip()
    datos = {
        "kpi": kpi,
        "correlacion": pd.read_sql("SELECT * FROM gold.resultado_correlacion", engine),
        "moran": pd.read_sql("SELECT * FROM gold.resultado_moran_global", engine),
        "lisa": pd.read_sql("SELECT * FROM gold.resultado_lisa", engine),
        "vecinos": pd.read_sql("SELECT cvegeo, cvegeo_vecino FROM gold.vecinos_municipio", engine),
        "incidencia_tipo": pd.read_sql("""
            SELECT t.anio, d.tipo_delito, sum(i.incidentes) AS incidentes
            FROM gold.fact_incidencia i
            JOIN gold.dim_tiempo t USING (id_tiempo)
            JOIN gold.dim_delito d USING (id_delito)
            GROUP BY t.anio, d.tipo_delito
        """, engine),
    }
    for nombre in ("lisa", "vecinos"):
        for col in ("cvegeo", "cvegeo_vecino"):
            if col in datos[nombre]:
                datos[nombre][col] = datos[nombre][col].str.strip()
    return datos


# --------------------------------------------------------------------------
# Mapas
# --------------------------------------------------------------------------
def mapa_coropleta(kpi: gpd.GeoDataFrame, columna: str, anio: int, cortes: np.ndarray) -> None:
    titulo, unidad, dec = INDICADORES_MAPA[columna]
    gdf = kpi[kpi["anio"] == anio]
    n_clases = len(cortes) - 1
    colores = SECUENCIAL[-n_clases:] if n_clases < len(SECUENCIAL) else SECUENCIAL
    cmap, norm = ListedColormap(colores), BoundaryNorm(cortes, n_clases)

    fig, ax = mapa_base()
    con_dato = gdf[gdf[columna].notna()]
    con_dato.plot(ax=ax, column=columna, cmap=cmap, norm=norm, edgecolor="white", linewidth=0.4)
    sin_dato = gdf[gdf[columna].isna()]
    if not sin_dato.empty:
        sin_dato.plot(ax=ax, color=SIN_DATO, edgecolor="white", linewidth=0.4)
    rotular_merida(ax, gdf)
    encuadrar(ax, gdf)

    leyenda = []
    for i in range(n_clases):
        n = int(((con_dato[columna] >= cortes[i]) & (
            (con_dato[columna] < cortes[i + 1]) if i < n_clases - 1 else (con_dato[columna] <= cortes[i + 1]))).sum())
        leyenda.append(Patch(facecolor=colores[i], edgecolor="none",
                             label=f"{fmt(cortes[i], dec)} – {fmt(cortes[i + 1], dec)}  ({n})"))
    if not sin_dato.empty:
        leyenda.append(Patch(facecolor=SIN_DATO, edgecolor="none", label=f"Sin dato  ({len(sin_dato)})"))
    ax.legend(handles=leyenda, title=f"{unidad} (municipios)", loc="lower left", fontsize=8,
              title_fontsize=8, alignment="left")
    ax.set_title(f"{titulo} · {anio}")
    pie(fig, "Fuente: gold.kpi_municipio_anio (yucatan_dw). Clases: quintiles del panel 2020–2025, "
             "iguales en todos los años. EPSG:6372.")
    guardar(fig, SALIDA / "maps" / str(anio) / f"{columna}.png")


def mapa_lisa(kpi: gpd.GeoDataFrame, lisa: pd.DataFrame, indicador: str, anio: int) -> None:
    gdf = kpi[kpi["anio"] == anio][["cvegeo", "nombre", "geom"]].merge(
        lisa[(lisa["anio"] == anio) & (lisa["indicador"] == indicador)][["cvegeo", "cuadrante"]], on="cvegeo")
    fig, ax = mapa_base()
    gdf.plot(ax=ax, color=gdf["cuadrante"].map(COLORES_LISA), edgecolor="white", linewidth=0.4)
    rotular_merida(ax, gdf)
    encuadrar(ax, gdf)
    conteo = gdf["cuadrante"].value_counts()
    leyenda = [Patch(facecolor=COLORES_LISA[c], edgecolor="none", label=f"{ETIQUETAS_LISA[c]}  ({conteo.get(c, 0)})")
               for c in ["HH", "HL", "LH", "LL", "no significativo"]]
    ax.legend(handles=leyenda, title="LISA (municipios)", loc="lower left", fontsize=8, title_fontsize=8,
              alignment="left")
    ax.set_title(f"LISA · {ETIQUETAS[indicador]} · {anio}")
    pie(fig, f"Fuente: gold.resultado_lisa. Contigüidad Queen, pesos estandarizados por fila, "
             f"999 permutaciones, p < {ALFA}. Asociación espacial no implica causalidad.")
    guardar(fig, SALIDA / "maps" / str(anio) / f"lisa_{indicador}.png")


def mapa_sector_dominante(kpi: gpd.GeoDataFrame, anio: int) -> None:
    gdf = kpi[kpi["anio"] == anio]
    fig, ax = mapa_base()
    gdf.plot(ax=ax, color=gdf["sector_dominante"].map(COLORES_SECTOR).fillna(SIN_DATO),
             edgecolor="white", linewidth=0.4)
    rotular_merida(ax, gdf)
    encuadrar(ax, gdf)
    nombres = gdf.drop_duplicates("sector_dominante").set_index("sector_dominante")["desc_sector_dominante"]
    conteo = gdf["sector_dominante"].value_counts()
    leyenda = [Patch(facecolor=color, edgecolor="none",
                     label=f"{codigo} · {_acortar(nombres[codigo])}  ({conteo[codigo]})")
               for codigo, color in COLORES_SECTOR.items() if codigo in conteo]
    otros = set(conteo.index) - set(COLORES_SECTOR)
    if otros:
        leyenda.append(Patch(facecolor=SIN_DATO, edgecolor="none", label=f"Otros  ({conteo[list(otros)].sum()})"))
    ax.legend(handles=leyenda, title="Sector SCIAN con más negocios (municipios)", loc="lower left",
              fontsize=8, title_fontsize=8, alignment="left")
    ax.set_title(f"Actividad económica dominante · {anio}")
    pie(fig, "Fuente: gold.kpi_municipio_anio. Empate -> código de sector menor.")
    guardar(fig, SALIDA / "maps" / str(anio) / "sector_dominante.png")


def _acortar(texto: str, largo: int = 45) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip(", ") + "…"


# --------------------------------------------------------------------------
# Figuras por año
# --------------------------------------------------------------------------
def dispersion_correlacion(kpi: pd.DataFrame, corr: pd.DataFrame, x: str, y: str, anio: int) -> None:
    df = kpi[kpi["anio"] == anio][["nombre", x, y]].dropna()
    r = corr[(corr["anio"] == anio) & (corr["variable_x"] == x) & (corr["variable_y"] == y)].set_index("metodo")

    fig, ax = plt.subplots(figsize=(7, 5))
    rejilla_y(ax)
    ax.scatter(df[x], df[y], s=28, color=SERIE_1, edgecolor=SUPERFICIE, linewidth=1, zorder=3)
    for nombre in ("Mérida", "Kanasín"):  # los dos municipios más densos
        fila = df[df["nombre"] == nombre]
        if not fila.empty:
            ax.annotate(nombre, (fila[x].iloc[0], fila[y].iloc[0]), xytext=(6, 4), textcoords="offset points",
                        fontsize=8, color=TINTA)
    ax.set_xlabel(f"{ETIQUETAS[x]} ({INDICADORES_MAPA[x][1]})" if x in INDICADORES_MAPA else ETIQUETAS[x])
    ax.set_ylabel(f"{ETIQUETAS[y]} ({INDICADORES_MAPA[y][1]})" if y in INDICADORES_MAPA else ETIQUETAS[y])
    ax.set_title(f"{ETIQUETAS[x]} vs {ETIQUETAS[y]} · {anio}", pad=20)
    p, s = r.loc["pearson"], r.loc["spearman"]
    ax.text(0, 1.01, f"Pearson r = {p['coeficiente']:.3f} (p = {p['p_value']:.3f})   ·   "
                     f"Spearman ρ = {s['coeficiente']:.3f} (p = {s['p_value']:.3f})   ·   n = {int(p['n'])}",
            transform=ax.transAxes, fontsize=8, color=TINTA_2, va="bottom")
    pie(fig, "Fuente: gold.kpi_municipio_anio y gold.resultado_correlacion. Correlación no implica causalidad.")
    guardar(fig, SALIDA / "figures" / str(anio) / f"correlacion_{x}__{y}.png")


def diagrama_moran(kpi: pd.DataFrame, vecinos: pd.DataFrame, moran: pd.DataFrame, lisa: pd.DataFrame,
                   indicador: str, anio: int) -> None:
    """Valor estandarizado vs su rezago espacial (promedio de los vecinos, pesos por fila)."""
    df = kpi[kpi["anio"] == anio][["cvegeo", "nombre", indicador]].set_index("cvegeo")
    z = (df[indicador] - df[indicador].mean()) / df[indicador].std(ddof=0)
    rezago = vecinos.assign(z=vecinos["cvegeo_vecino"].map(z)).groupby("cvegeo")["z"].mean().reindex(z.index)
    cuadrante = lisa[(lisa["anio"] == anio) & (lisa["indicador"] == indicador)].set_index("cvegeo")["cuadrante"]
    cuadrante = cuadrante.reindex(z.index).fillna("no significativo")
    m = moran[(moran["anio"] == anio) & (moran["indicador"] == indicador)].iloc[0]

    fig, ax = plt.subplots(figsize=(6.5, 6))
    ax.axhline(0, color="#c3c2b7", linewidth=0.8)
    ax.axvline(0, color="#c3c2b7", linewidth=0.8)
    for c in ["no significativo", "LL", "LH", "HL", "HH"]:
        sel = cuadrante == c
        ax.scatter(z[sel], rezago[sel], s=30, color=COLORES_LISA[c], edgecolor=SUPERFICIE, linewidth=1,
                   zorder=3, label=f"{ETIQUETAS_LISA[c]} ({int(sel.sum())})")
    xs = np.array([z.min(), z.max()])
    ax.plot(xs, m["i"] * xs, color=TINTA_2, linewidth=1.5, zorder=4)
    for nombre in ("Mérida", "Kanasín"):
        if nombre in df["nombre"].values:
            c = df.index[df["nombre"] == nombre][0]
            ax.annotate(nombre, (z[c], rezago[c]), xytext=(6, 4), textcoords="offset points", fontsize=8, color=TINTA)
    ax.set_xlabel(f"{ETIQUETAS[indicador]} (estandarizada)")
    ax.set_ylabel("Rezago espacial (promedio de vecinos, estandarizado)")
    ax.set_title(f"Diagrama de Moran · {ETIQUETAS[indicador]} · {anio}", pad=20)
    ax.text(0, 1.01, f"I de Moran = {m['i']:.3f} (pendiente)   ·   z = {m['z']:.2f}   ·   p_sim = {m['p_sim']:.3f}",
            transform=ax.transAxes, fontsize=8, color=TINTA_2, va="bottom")
    ax.legend(title="Cuadrante LISA (municipios)", fontsize=8, title_fontsize=8, loc="upper left",
              bbox_to_anchor=(1.01, 1), alignment="left")
    pie(fig, "Fuente: gold.kpi_municipio_anio, gold.vecinos_municipio, gold.resultado_moran_global y resultado_lisa.")
    guardar(fig, SALIDA / "figures" / str(anio) / f"moran_{indicador}.png")


def incidentes_por_tipo(inc: pd.DataFrame, anio: int, top: int = 10) -> None:
    df = inc[inc["anio"] == anio].sort_values("incidentes", ascending=False)
    principales = df.head(top)
    resto = df["incidentes"].iloc[top:].sum()
    filas = pd.concat([principales, pd.DataFrame([{"tipo_delito": "otros tipos", "incidentes": resto}])])
    filas = filas.iloc[::-1]

    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.xaxis.grid(True, color=REJILLA, linewidth=0.6)
    ax.set_axisbelow(True)
    colores = [TINTA_TENUE if t == "otros tipos" else SERIE_1 for t in filas["tipo_delito"]]
    ax.barh(filas["tipo_delito"].str.capitalize(), filas["incidentes"], color=colores, height=0.7)
    for i, v in enumerate(filas["incidentes"]):
        ax.text(v, i, f" {int(v):,}", va="center", fontsize=8, color=TINTA_2)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, labelcolor=TINTA_2)
    ax.set_xlabel("Incidentes registrados")
    ax.set_title(f"Incidentes por tipo de delito · Yucatán · {anio}")
    pie(fig, f"Fuente: gold.fact_incidencia × dim_delito × dim_tiempo (SESNSP, fuero común). "
             f"Los {top} tipos con más incidentes; el resto se agrupa en «otros tipos».")
    guardar(fig, SALIDA / "figures" / str(anio) / "incidentes_por_tipo.png")


# --------------------------------------------------------------------------
# Series 2020-2025
# --------------------------------------------------------------------------
def serie_moran(moran: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    rejilla_y(ax)
    for indicador, color in [("densidad_negocios", SERIE_1), ("tasa_delictiva_1000_hab", SERIE_2)]:
        df = moran[moran["indicador"] == indicador].sort_values("anio")
        ax.plot(df["anio"], df["i"], color=color, linewidth=2, label=ETIQUETAS[indicador])
        sig = df["p_sim"] < ALFA
        ax.scatter(df["anio"][sig], df["i"][sig], s=40, color=color, edgecolor=SUPERFICIE, linewidth=1.5, zorder=3)
        ax.scatter(df["anio"][~sig], df["i"][~sig], s=40, facecolor=SUPERFICIE, edgecolor=color, linewidth=1.5,
                   zorder=3)
        ultimo = df.iloc[-1]
        ax.annotate(ETIQUETAS[indicador], (ultimo["anio"], ultimo["i"]), xytext=(8, 0), textcoords="offset points",
                    fontsize=8, color=TINTA_2, va="center")
    ei = moran["esperanza_i"].iloc[0]
    ax.axhline(ei, color=TINTA_TENUE, linewidth=0.8)
    ax.annotate(f"E[I] = {ei:.3f}", (min(ANIOS_PANEL), ei), xytext=(0, 4), textcoords="offset points",
                fontsize=7, color=TINTA_TENUE)
    ax.set_xticks(list(ANIOS_PANEL))
    ax.set_xlim(min(ANIOS_PANEL) - 0.2, max(ANIOS_PANEL) + 1.3)
    ax.set_ylabel("I de Moran global")
    ax.set_title("Autocorrelación espacial global, 2020–2025")
    ax.legend(loc="upper left", fontsize=8)
    pie(fig, f"Fuente: gold.resultado_moran_global. Punto relleno: p_sim < {ALFA}; hueco: no significativo. "
             "Contigüidad Queen, 999 permutaciones.")
    guardar(fig, SALIDA / "figures" / "serie" / "moran_global.png")


def serie_barras(df: pd.DataFrame, columna: str, titulo: str, eje: str, nota: str, archivo: str) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    rejilla_y(ax)
    ax.bar(df["anio"], df[columna], color=SERIE_1, width=0.6)
    for a, v in zip(df["anio"], df[columna]):
        ax.text(a, v, f"{int(v):,}", ha="center", va="bottom", fontsize=8, color=TINTA_2)
    ax.set_xticks(list(ANIOS_PANEL))
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.set_ylabel(eje)
    ax.set_title(titulo)
    pie(fig, nota)
    guardar(fig, SALIDA / "figures" / "serie" / archivo)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    engine = create_engine(DB_URL)
    d = leer_gold(engine)
    kpi = d["kpi"]

    anios = sorted(kpi["anio"].unique())
    if anios != list(ANIOS_PANEL):
        raise ValueError(f"kpi_municipio_anio: años {anios}, se esperaban {list(ANIOS_PANEL)}")

    cortes = {c: cortes_quintiles(kpi[c]) for c in INDICADORES_MAPA}
    pares = d["correlacion"][["variable_x", "variable_y"]].drop_duplicates().itertuples(index=False)
    pares = list(pares)
    indicadores_lisa = sorted(d["lisa"]["indicador"].unique())

    for anio in ANIOS_PANEL:
        for columna in INDICADORES_MAPA:
            mapa_coropleta(kpi, columna, anio, cortes[columna])
        for indicador in indicadores_lisa:
            mapa_lisa(kpi, d["lisa"], indicador, anio)
            diagrama_moran(kpi, d["vecinos"], d["moran"], d["lisa"], indicador, anio)
        mapa_sector_dominante(kpi, anio)
        for x, y in pares:
            dispersion_correlacion(kpi, d["correlacion"], x, y, anio)
        incidentes_por_tipo(d["incidencia_tipo"], anio)

    serie_moran(d["moran"])
    totales = kpi.groupby("anio", as_index=False)[["total_incidentes", "total_negocios"]].sum()
    serie_barras(totales, "total_incidentes", "Incidentes delictivos registrados · Yucatán, 2020–2025",
                 "Incidentes", "Fuente: gold.kpi_municipio_anio (SESNSP). La caída de 2022 parece un cambio de "
                 "registro de la fuente, no de criminalidad real (ver README).", "incidentes_por_anio.png")
    serie_barras(totales, "total_negocios", "Establecimientos DENUE · Yucatán, 2020–2025", "Establecimientos",
                 "Fuente: gold.kpi_municipio_anio. Edición de noviembre de cada año; 2025 usa mayo (2025_05). "
                 "SCIAN 2018 hasta 2023, SCIAN 2023 desde 2024.", "negocios_por_anio.png")

    print("Figuras generadas.")


if __name__ == "__main__":
    main()
