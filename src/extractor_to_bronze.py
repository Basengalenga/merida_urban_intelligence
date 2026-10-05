"""
Extracción y carga a la capa bronze de yucatan_dw.

Cada extracción es una función independiente que descarga su fuente y la
carga a su propia tabla dentro del esquema `bronze`:

    bronze.incidencia_delictiva  <- SESNSP (solo Yucatán, clave 31)
    bronze.iter_2020             <- INEGI ITER 2020, Yucatán
    bronze.denue                 <- INEGI DENUE, ediciones 2020-2025
    bronze.marco_geo_municipal   <- INEGI Marco Geoestadístico, municipios
    bronze.scian                 <- INEGI catálogo SCIAN 2018 y 2023

Reglas de bronze:
    - Todas las columnas de la fuente se guardan como TEXT.
    - Nombres de columna normalizados a snake_case sin acentos.
    - Los datos se cargan tal como vienen (sin limpiar ni transformar).
    - Metadatos agregados: _ingested_at, _source_url, _run_id.
      _ingested_at es el inicio del lote (igual para las 5 tablas), no la hora
      en que se escribió cada tabla. En Airflow es el logical_date del DAG run.

Manejo de errores (fail-fast):
    Si cualquier descarga o carga falla, se detiene todo el script con error.
    Dentro de cada función primero se descarga todo y solo después se toca la
    tabla, así una descarga fallida nunca deja una tabla incompleta.

Requisito: el esquema `bronze` ya existe (se crea en el init de ./sql).
"""

import io
import re
import tempfile
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd
import requests
from curl_cffi import requests as cffi_requests
from sqlalchemy import create_engine
from sqlalchemy.types import Text

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
DB_URL = "postgresql+psycopg2://admin:admin123@db:5432/yucatan_dw"
SCHEMA = "bronze"

CLAVE_YUCATAN = "31"

URL_INCIDENCIA = "https://repodatos.atdt.gob.mx/api_update/sesnsp/incidencia_delictiva/IDM_NM_dic25.csv"
URL_ITER = "https://www.inegi.org.mx/contenidos/programas/ccpv/2020/datosabiertos/iter/iter_31_cpv2020_csv.zip"
URL_MARCO_GEO = "https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/geografia/marcogeo/889463807469/31_yucatan.zip"

BASE_DENUE = "https://www.inegi.org.mx/contenidos/masiva/denue"
EDICIONES_DENUE = {
    "2020_11": f"{BASE_DENUE}/2020_11/denue_31_1120_csv.zip",
    "2021_11": f"{BASE_DENUE}/2021_11/denue_31_1121_csv.zip",
    "2022_11": f"{BASE_DENUE}/2022_11/denue_31_1122_csv.zip",
    "2023_11": f"{BASE_DENUE}/2023_11/denue_31_1123_csv.zip",
    "2024_11": f"{BASE_DENUE}/2024_11/denue_31_1124_csv.zip",
    "2025_05": f"{BASE_DENUE}/2025_05/denue_31_0525_csv.zip",
}

# El DENUE usa SCIAN 2018 (ediciones 2020-2023) y SCIAN 2023 (2024 en adelante)
BASE_SCIAN = "https://www.inegi.org.mx/contenidos/app/scian"
URLS_SCIAN = {
    "SCIAN 2018": f"{BASE_SCIAN}/scian_2018_categorias_y_productos.xlsx",
    "SCIAN 2023": f"{BASE_SCIAN}/scian_2023_categorias_y_productos.xlsx",
}
HOJAS_SCIAN = ["SECTOR", "SUBSECTOR", "RAMA", "SUBRAMA", "CLASE"]


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def normalizar_columna(nombre: str) -> str:
    """'Bien jurídico afectado' -> 'bien_juridico_afectado'."""
    sin_acentos = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode("ascii")
    snake = re.sub(r"[^a-z0-9]+", "_", sin_acentos.lower())
    return snake.strip("_")


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [normalizar_columna(c) for c in df.columns]
    return df


def agregar_metadatos(df: pd.DataFrame, source_url: str, run_id: str, ingested_at: datetime) -> pd.DataFrame:
    """Se agregan después de normalizar, porque la normalización quitaría el '_' inicial."""
    df["_ingested_at"] = ingested_at  # timestamp con zona horaria (UTC)
    df["_source_url"] = source_url
    df["_run_id"] = run_id
    return df


def cargar_tabla(df: pd.DataFrame, tabla: str, engine) -> None:
    """Carga un DataFrame o GeoDataFrame a bronze, con todas las columnas de la fuente como TEXT."""
    dtype = {c: Text() for c in df.columns if c not in ("_ingested_at", "geometry")}

    # DECISIÓN HUMANA (temporal): reemplazo completo de la tabla en cada ejecución.
    # Se decidió así mientras se define la estrategia para re-ejecuciones
    # (append, historial por _run_id, etc.). Revisar antes de pasar a Airflow.
    # La carga corre dentro de una transacción: si falla, la tabla anterior no se pierde.
    with engine.begin() as conn:
        if isinstance(df, gpd.GeoDataFrame):
            df.to_postgis(tabla, conn, schema=SCHEMA, if_exists="replace", index=False, dtype=dtype)
        else:
            df.to_sql(
                tabla, conn, schema=SCHEMA, if_exists="replace", index=False,
                dtype=dtype, chunksize=10_000, method="multi",
            )

    print(f"OK   {SCHEMA}.{tabla}: {len(df):,} registros")


# --------------------------------------------------------------------------
# Extracciones
# --------------------------------------------------------------------------
def extraer_cargar_incidencia(engine, run_id: str, ingested_at: datetime) -> None:
    """Incidencia delictiva municipal (SESNSP). El archivo trae todo México; solo se guarda Yucatán."""
    r = cffi_requests.get(URL_INCIDENCIA, impersonate="chrome", timeout=300)
    r.raise_for_status()

    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1", dtype=str)
    df = normalizar_columnas(df)

    df = df[df["clave_ent"] == CLAVE_YUCATAN].copy()
    if df.empty:
        raise ValueError(f"No se encontraron registros con clave_ent = {CLAVE_YUCATAN}")

    df = agregar_metadatos(df, URL_INCIDENCIA, run_id, ingested_at)
    cargar_tabla(df, "incidencia_delictiva", engine)


def extraer_cargar_iter(engine, run_id: str, ingested_at: datetime) -> None:
    """Censo 2020, ITER Yucatán. Incluye filas de totales (MUN 000, LOC 0000/9998/9999) tal como vienen."""
    r = requests.get(URL_ITER, timeout=120)
    r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv") and "conjunto_de_datos" in n.lower())
        with z.open(csv_name) as f:
            df = pd.read_csv(f, dtype=str)

    df = normalizar_columnas(df)
    df = agregar_metadatos(df, URL_ITER, run_id, ingested_at)
    cargar_tabla(df, "iter_2020", engine)


def version_scian(edicion: str) -> str:
    # Desde 2019 hasta 05/2024 -> SCIAN 2018; desde 11/2024 -> SCIAN 2023
    return "SCIAN 2023" if edicion >= "2024_11" else "SCIAN 2018"


def descargar_denue(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv") and "conjunto_de_datos" in n.lower()]
        partes = []
        for name in csvs:  # algunas ediciones parten el estado en varios CSV
            raw = z.read(name)
            try:
                partes.append(pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8"))
            except UnicodeDecodeError:
                partes.append(pd.read_csv(io.BytesIO(raw), dtype=str, encoding="latin-1"))
        return pd.concat(partes, ignore_index=True)


def extraer_cargar_denue(engine, run_id: str, ingested_at: datetime) -> None:
    """DENUE Yucatán, todas las ediciones en una sola tabla.

    Si una edición no tiene alguna columna que otra sí tiene, queda en NULL
    (pd.concat). Pendiente de revisar si el cambio de SCIAN causa problemas.
    """
    dfs = []
    for edicion, url in EDICIONES_DENUE.items():
        df = descargar_denue(url)  # fail-fast: si una edición falla, no se carga nada
        df["EDICION"] = edicion
        df["ANIO"] = edicion[:4]
        df["SCIAN"] = version_scian(edicion)
        df = normalizar_columnas(df)
        df = agregar_metadatos(df, url, run_id, ingested_at)  # _source_url = URL de cada edición
        dfs.append(df)
        print(f"     denue {edicion}: {len(df):,} registros descargados")

    denue = pd.concat(dfs, ignore_index=True)
    cargar_tabla(denue, "denue", engine)


def extraer_cargar_marco_geo(engine, run_id: str, ingested_at: datetime) -> None:
    """Polígonos municipales de Yucatán (Marco Geoestadístico INEGI)."""
    r = requests.get(URL_MARCO_GEO, timeout=120)
    r.raise_for_status()

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(tmp)
            shp = next(n for n in z.namelist() if n.endswith("mun.shp"))
        mun = gpd.read_file(f"{tmp}/{shp}")

    # El .prj de INEGI describe EPSG:6372, pero con un nombre propio
    # ("MEXICO_ITRF_2008_LCC") que a veces no se reconoce como código EPSG.
    # Esto NO mueve las coordenadas: solo etiqueta el SRID para que PostGIS lo guarde como 6372.
    mun = mun.set_crs(epsg=6372, allow_override=True)

    mun = normalizar_columnas(mun)  # la columna 'geometry' conserva su nombre
    mun = agregar_metadatos(mun, URL_MARCO_GEO, run_id, ingested_at)
    cargar_tabla(mun, "marco_geo_municipal", engine)


def extraer_cargar_scian(engine, run_id: str, ingested_at: datetime) -> None:
    """Catálogo SCIAN (INEGI), versiones 2018 y 2023, una hoja de Excel por nivel.

    Se guardan todas las hojas de nivel tal como vienen, con dos columnas extra:
    HOJA (nivel) y SCIAN (versión). Solo se quitan los renglones completamente
    vacíos que deja el formato del Excel. La hoja CLASE incluye renglones del
    índice de productos sin código; se conservan (silver los filtra).
    """
    dfs = []
    for version, url in URLS_SCIAN.items():
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        for hoja in HOJAS_SCIAN:
            # La fila 0 es el título del documento; los encabezados están en la fila 1
            df = pd.read_excel(io.BytesIO(r.content), sheet_name=hoja, header=1, dtype=str)
            df = df.dropna(how="all")
            df["HOJA"] = hoja
            df["SCIAN"] = version
            df = normalizar_columnas(df)
            df = agregar_metadatos(df, url, run_id, ingested_at)
            dfs.append(df)
        print(f"     scian {version}: descargado")

    scian = pd.concat(dfs, ignore_index=True)
    cargar_tabla(scian, "scian", engine)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    engine = create_engine(DB_URL)
    run_id = str(uuid.uuid4())
    ingested_at = datetime.now(timezone.utc)
    print(f"run_id: {run_id}")

    # Fail-fast: cualquier excepción detiene el script (no se atrapa a propósito).
    extraer_cargar_incidencia(engine, run_id, ingested_at)
    extraer_cargar_iter(engine, run_id, ingested_at)
    extraer_cargar_denue(engine, run_id, ingested_at)
    extraer_cargar_marco_geo(engine, run_id, ingested_at)
    extraer_cargar_scian(engine, run_id, ingested_at)

    print("Carga a bronze completada.")


if __name__ == "__main__":
    main()