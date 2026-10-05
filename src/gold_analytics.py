"""
Análisis estadístico y espacial sobre gold de yucatan_dw.

Lee SOLO de gold (kpi_municipio_anio y vecinos_municipio) y, para cada año del
panel (2020 a 2025), calcula:

    gold.resultado_correlacion      <- Pearson y Spearman (scipy)
    gold.resultado_moran_global     <- Moran global (esda)
    gold.resultado_lisa             <- Moran local (LISA), significancia 0.05
    gold.resultado_moran_bivariado  <- Moran bivariado

Pesos espaciales: contigüidad Queen desde gold.vecinos_municipio, estandarizados
por fila. Permutaciones: 999 con semilla fija (resultados reproducibles).

Manejo de errores (fail-fast):
    Primero se calcula todo en memoria; después TRUNCATE + INSERT de las tablas
    resultado_* en una sola transacción. Si algo falla, gold queda como estaba.
"""

import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from esda import Moran, Moran_BV, Moran_Local
from libpysal.weights import W
from scipy import stats
from sqlalchemy import create_engine, text

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
DB_URL = "postgresql+psycopg2://admin:admin123@db:5432/yucatan_dw"
SCHEMA = "gold"

ANIOS_PANEL = range(2020, 2026)
PERMUTACIONES = 999
SEMILLA = 42
ALFA = 0.05

# Pares (x, y) de columnas de gold.kpi_municipio_anio
CORRELACIONES = [
    ("tasa_delictiva_1000_hab", "negocios_por_1000_hab"),
    ("densidad_poblacional", "densidad_negocios"),
    ("tasa_pea", "tasa_delictiva_1000_hab"),
]
INDICADORES_MORAN = ["tasa_delictiva_1000_hab", "densidad_negocios"]
MORAN_BIVARIADO = [("tasa_delictiva_1000_hab", "densidad_negocios")]

# Cuadrantes de esda.Moran_Local (geoda_quads=False)
CUADRANTES = {1: "HH", 2: "LH", 3: "LL", 4: "HL"}

TABLAS = [
    "resultado_correlacion",
    "resultado_moran_global",
    "resultado_lisa",
    "resultado_moran_bivariado",
]


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def exigir(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise ValueError(mensaje)


def construir_pesos(engine, cvegeos: list) -> W:
    """Pesos Queen desde gold.vecinos_municipio, estandarizados por fila, en el orden de cvegeos."""
    vecinos = pd.read_sql("SELECT cvegeo, cvegeo_vecino FROM gold.vecinos_municipio", engine)
    vecinos["cvegeo"] = vecinos["cvegeo"].str.strip()
    vecinos["cvegeo_vecino"] = vecinos["cvegeo_vecino"].str.strip()
    por_municipio = vecinos.groupby("cvegeo")["cvegeo_vecino"].apply(sorted).to_dict()
    sin_vecinos = [c for c in cvegeos if c not in por_municipio]
    exigir(not sin_vecinos, f"vecinos_municipio: municipios sin vecinos {sin_vecinos}")

    w = W({c: por_municipio[c] for c in cvegeos}, id_order=cvegeos)
    w.transform = "r"
    return w


# --------------------------------------------------------------------------
# Análisis
# --------------------------------------------------------------------------
def correlaciones(df: pd.DataFrame, anio: int) -> list:
    filas = []
    for x, y in CORRELACIONES:
        datos = df[[x, y]].dropna()
        for metodo, funcion in [("pearson", stats.pearsonr), ("spearman", stats.spearmanr)]:
            coef, p = funcion(datos[x], datos[y])
            filas.append((anio, x, y, metodo, float(coef), float(p), len(datos)))
    return filas


def moran_global(df: pd.DataFrame, w: W, anio: int) -> list:
    filas = []
    for indicador in INDICADORES_MORAN:
        np.random.seed(SEMILLA)  # esda.Moran usa el generador global de numpy
        m = Moran(df[indicador].to_numpy(), w, permutations=PERMUTACIONES)
        filas.append((anio, indicador, m.I, m.EI, m.z_sim, m.p_sim))
    return filas


def lisa(df: pd.DataFrame, w: W, anio: int) -> list:
    filas = []
    for indicador in INDICADORES_MORAN:
        m = Moran_Local(df[indicador].to_numpy(), w, permutations=PERMUTACIONES, seed=SEMILLA)
        for cvegeo, ii, p, q in zip(df["cvegeo"], m.Is, m.p_sim, m.q):
            cuadrante = CUADRANTES[q] if p < ALFA else "no significativo"
            filas.append((anio, indicador, cvegeo, float(ii), float(p), cuadrante))
    return filas


def moran_bivariado(df: pd.DataFrame, w: W, anio: int) -> list:
    filas = []
    for x, y in MORAN_BIVARIADO:
        np.random.seed(SEMILLA)  # esda.Moran_BV usa el generador global de numpy
        m = Moran_BV(df[x].to_numpy(), df[y].to_numpy(), w, permutations=PERMUTACIONES)
        filas.append((anio, x, y, m.I, m.z_sim, m.p_sim))
    return filas


# --------------------------------------------------------------------------
# Carga
# --------------------------------------------------------------------------
def cargar_resultados(engine, resultados: dict, run_id: str, fecha_carga: datetime) -> None:
    # DECISIÓN HUMANA (temporal): TRUNCATE + INSERT en cada ejecución.
    # Se eligió así mientras se valida si es correcto recargar todo; si resulta
    # que no se debe hacer (p. ej. se necesita historial), se busca otra estrategia.
    # Todo corre en una transacción: si algo falla, gold queda como estaba.
    log = []
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in TABLAS)}"))
        for nombre in TABLAS:
            df = resultados[nombre]
            df.to_sql(nombre, conn, schema=SCHEMA, if_exists="append", index=False)
            log.append((nombre, "filas_cargadas", len(df)))
            print(f"OK   {SCHEMA}.{nombre}: {len(df):,} registros")

        pd.DataFrame(log, columns=["tabla", "metrica", "valor"]).assign(
            run_id=run_id, fecha_carga=fecha_carga,
        ).to_sql("etl_log", conn, schema=SCHEMA, if_exists="append", index=False)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    engine = create_engine(DB_URL)
    run_id = str(uuid.uuid4())
    fecha_carga = datetime.now(timezone.utc)
    print(f"run_id: {run_id}")

    columnas = sorted({c for par in CORRELACIONES + MORAN_BIVARIADO for c in par} | set(INDICADORES_MORAN))
    kpi = pd.read_sql(f"SELECT anio, cvegeo, {', '.join(columnas)} FROM gold.kpi_municipio_anio", engine)
    kpi["cvegeo"] = kpi["cvegeo"].str.strip()
    exigir(sorted(kpi["anio"].unique()) == list(ANIOS_PANEL),
           f"kpi_municipio_anio: años {sorted(kpi['anio'].unique())}, se esperaban {list(ANIOS_PANEL)}")

    cvegeos = sorted(kpi["cvegeo"].unique())
    w = construir_pesos(engine, cvegeos)

    # Fail-fast: cualquier excepción detiene el script (no se atrapa a propósito).
    filas = {t: [] for t in TABLAS}
    for anio in ANIOS_PANEL:
        df = kpi[kpi["anio"] == anio].set_index("cvegeo").loc[cvegeos].reset_index()
        nulos = df[INDICADORES_MORAN].isna().any(axis=1)
        exigir(not nulos.any(), f"{anio}: indicadores nulos en {df.loc[nulos, 'cvegeo'].tolist()}")

        filas["resultado_correlacion"] += correlaciones(df, anio)
        filas["resultado_moran_global"] += moran_global(df, w, anio)
        filas["resultado_lisa"] += lisa(df, w, anio)
        filas["resultado_moran_bivariado"] += moran_bivariado(df, w, anio)
        print(f"     {anio}: análisis completado")

    resultados = {
        "resultado_correlacion": pd.DataFrame(filas["resultado_correlacion"], columns=[
            "anio", "variable_x", "variable_y", "metodo", "coeficiente", "p_value", "n"]),
        "resultado_moran_global": pd.DataFrame(filas["resultado_moran_global"], columns=[
            "anio", "indicador", "i", "esperanza_i", "z", "p_sim"]),
        "resultado_lisa": pd.DataFrame(filas["resultado_lisa"], columns=[
            "anio", "indicador", "cvegeo", "ii", "p_sim", "cuadrante"]),
        "resultado_moran_bivariado": pd.DataFrame(filas["resultado_moran_bivariado"], columns=[
            "anio", "variable_x", "variable_y", "i", "z", "p_sim"]),
    }
    cargar_resultados(engine, resultados, run_id, fecha_carga)

    print("Análisis de gold completado.")


if __name__ == "__main__":
    main()
