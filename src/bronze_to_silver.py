"""
Transformación bronze -> silver de yucatan_dw.

Cada tabla silver se construye con una función independiente que lee su
fuente en bronze, la limpia y devuelve un DataFrame listo para cargar:

    silver.municipios                  <- bronze.marco_geo_municipal
    silver.cat_scian                   <- bronze.scian
    silver.crosswalk_municipio_nombre  <- nombres de municipio de todas las fuentes
    silver.censo_municipal             <- bronze.iter_2020 (LOC = 0000)
    silver.denue_establecimientos      <- bronze.denue (todas las ediciones)
    silver.incidencia_delictiva        <- bronze.incidencia_delictiva

Reglas de silver:
    - Llave de integración: cvegeo CHAR(5) = '31' + cve_mun. Nunca se une por nombre.
    - Geometrías en EPSG:6372; áreas en km².
    - Valores confidenciales o no disponibles del INEGI ('*', 'N/D') -> NULL.
    - Columnas de trazabilidad: fuente, version_fuente, fecha_carga.
    - Descartes y reasignaciones se registran en silver.etl_log.

Manejo de errores (fail-fast):
    Primero se transforman y validan todas las tablas en memoria; solo después
    se toca la base, en una sola transacción. Si algo falla, silver queda como estaba.

El DDL vive en sql/init.sql. Este script lo ejecuta al inicio (es idempotente),
porque Docker solo lo corre cuando el volumen de la base es nuevo.
"""

import csv
import io
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import create_engine, text

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
DB_URL = "postgresql+psycopg2://admin:admin123@db:5432/yucatan_dw"
SCHEMA = "silver"
INIT_SQL = Path(__file__).resolve().parent.parent / "sql" / "init.sql"

CLAVE_YUCATAN = "31"
N_MUNICIPIOS = 106
SRID = 6372
SRID_DENUE = 6365  # lat/long del DENUE: geográficas ITRF2008 (marco oficial de INEGI)

# Puntos DENUE que no caen en ningún polígono se asignan al municipio más
# cercano si están a esta distancia o menos; los demás se descartan.
# 30 m = 25 m del punto de borde más alejado encontrado + 5 m de margen (ver README).
UMBRAL_MAS_CERCANO_M = 30

# DECISIÓN HUMANA: el Arrecife Alacranes (Progreso, ~120 km mar adentro) no es habitable y
# se quita del polígono de Progreso (inflaba su área). Se quitan las partes cuyo centroide
# cae en esta caja (lon/lat, EPSG:4326). Los demás cayos e islas se conservan (ver README).
CVEGEO_PROGRESO = "31059"
CAJA_ALACRANES = shapely.box(-89.85, 22.30, -89.55, 22.65)

VALORES_NULOS_INEGI = {"*", "N/D"}

COLUMNAS_CENSO = ["pobtot", "p_12ymas", "pea", "pe_inac", "pob0_14", "pob15_64", "pob65_mas", "vivtot", "tvivhab"]

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# Sectores SCIAN que INEGI define como rango de códigos de 2 dígitos
SECTORES_RANGO = {"31": "31-33", "32": "31-33", "33": "31-33", "48": "48-49", "49": "48-49"}
NIVELES_SCIAN = {"SECTOR": "sector", "SUBSECTOR": "subsector", "RAMA": "rama", "CLASE": "clase"}

# Orden de carga (respeta las FKs). etl_log no se trunca.
TABLAS = [
    "municipios",
    "cat_scian",
    "crosswalk_municipio_nombre",
    "censo_municipal",
    "denue_establecimientos",
    "incidencia_delictiva",
]


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def normalizar_texto(valor: str) -> str:
    """'  La vida y la Integridad  corporal ' -> 'la vida y la integridad corporal' (sin acentos)."""
    sin_acentos = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sin_acentos).strip().lower()


def a_entero(serie: pd.Series, columna: str) -> pd.Series:
    """Texto -> Int64. '*' y 'N/D' pasan a NULL; cualquier otro valor no numérico es error."""
    limpia = serie.str.strip()
    limpia = limpia.where(~limpia.isin(VALORES_NULOS_INEGI))
    invalidos = limpia.notna() & ~limpia.str.fullmatch(r"[0-9]+", na=False)
    if invalidos.any():
        raise ValueError(f"{columna}: valores no numéricos {limpia[invalidos].unique()[:10].tolist()}")
    return limpia.astype("Int64")


def cvegeo_desde_cve_mun(cve_mun: pd.Series) -> pd.Series:
    return CLAVE_YUCATAN + cve_mun.str.strip().str.zfill(3)


def a_multipolygon(geom):
    """Repara (ST_MakeValid) si hace falta y devuelve siempre MultiPolygon."""
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, MultiPolygon):
        return geom
    # make_valid puede devolver GeometryCollection: se conservan solo las partes poligonales
    partes = [p for g in getattr(geom, "geoms", []) for p in (g.geoms if isinstance(g, MultiPolygon) else [g])
              if isinstance(p, Polygon)]
    if not partes:
        raise ValueError(f"Geometría no poligonal tras reparar: {geom.geom_type}")
    return MultiPolygon(partes)


def exigir(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise ValueError(mensaje)


def copy_postgres(table, conn, keys, data_iter) -> None:
    """Método de inserción para pandas.to_sql usando COPY (mucho más rápido que INSERT)."""
    dbapi_conn = conn.connection
    with dbapi_conn.cursor() as cur:
        buf = io.StringIO()
        csv.writer(buf).writerows(data_iter)
        buf.seek(0)
        columnas = ", ".join(f'"{k}"' for k in keys)
        cur.copy_expert(f'COPY {table.schema}.{table.name} ({columnas}) FROM STDIN WITH CSV', buf)


def geom_a_ewkb(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """GeoDataFrame -> DataFrame con la geometría como EWKB hex (PostGIS la convierte al hacer COPY)."""
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    df["geom"] = shapely.to_wkb(shapely.set_srid(gdf.geometry.values, SRID), hex=True, include_srid=True)
    return df


# --------------------------------------------------------------------------
# Transformaciones
# --------------------------------------------------------------------------
def transformar_municipios(engine, fecha_carga: datetime, log: list) -> gpd.GeoDataFrame:
    mun = gpd.read_postgis(
        "SELECT cvegeo, cve_ent, cve_mun, nomgeo, geometry FROM bronze.marco_geo_municipal",
        engine, geom_col="geometry",
    )
    exigir(mun.crs is not None and mun.crs.to_epsg() == SRID, f"marco_geo_municipal no está en EPSG:{SRID}")
    exigir(len(mun) == N_MUNICIPIOS, f"municipios: se esperaban {N_MUNICIPIOS} filas, hay {len(mun)}")

    mun["cve_ent"] = mun["cve_ent"].str.strip()
    mun["cve_mun"] = mun["cve_mun"].str.strip().str.zfill(3)
    cvegeo = cvegeo_desde_cve_mun(mun["cve_mun"])
    exigir((cvegeo == mun["cvegeo"].str.strip()).all(), "municipios: cvegeo no coincide con '31' + cve_mun")
    mun["cvegeo"] = cvegeo

    invalidas = int((~mun.geometry.is_valid).sum())
    log.append(("municipios", "geometrias_reparadas", invalidas))
    mun["geometry"] = mun.geometry.apply(a_multipolygon)

    caja = gpd.GeoSeries([CAJA_ALACRANES], crs=4326).to_crs(SRID).iloc[0]
    progreso = mun.index[mun["cvegeo"] == CVEGEO_PROGRESO][0]
    partes = list(mun.at[progreso, "geometry"].geoms)
    conservadas = [p for p in partes if not caja.contains(p.centroid)]
    exigir(len(conservadas) < len(partes), "municipios: no se encontró el Arrecife Alacranes en Progreso")
    mun.at[progreso, "geometry"] = MultiPolygon(conservadas)
    log.append(("municipios", "partes_arrecife_alacranes_eliminadas", len(partes) - len(conservadas)))

    mun = mun.rename(columns={"nomgeo": "nombre_municipio", "geometry": "geom"}).set_geometry("geom")
    mun["area_km2"] = mun.geom.area / 1_000_000
    mun["fuente"] = "INEGI Marco Geoestadístico"
    mun["version_fuente"] = "Marco Geoestadístico 2020"
    mun["fecha_carga"] = fecha_carga
    return mun[["cvegeo", "cve_ent", "cve_mun", "nombre_municipio", "geom", "area_km2",
                "fuente", "version_fuente", "fecha_carga"]]


def transformar_cat_scian(engine, fecha_carga: datetime) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT codigo, titulo, hoja, scian FROM bronze.scian WHERE codigo IS NOT NULL", engine,
    )
    df = df[df["hoja"].isin(NIVELES_SCIAN)].copy()  # se omite SUBRAMA

    df["codigo"] = df["codigo"].str.strip()
    df["nivel"] = df["hoja"].map(NIVELES_SCIAN)
    df["version_scian"] = df["scian"]
    # INEGI marca algunos títulos con una 'T' pegada al final ("ConstrucciónT"); no es parte del nombre
    df["descripcion"] = (
        df["titulo"].str.replace(r"\s+", " ", regex=True).str.strip()
        .str.replace(r"(?<=[a-záéíóúñü)])T$", "", regex=True).str.strip()
    )

    sector = df["codigo"].str[:2].astype(int)
    df["es_retail"] = sector == 46
    df["es_servicio"] = sector.between(51, 81)

    exigir(not df.duplicated(["codigo", "version_scian"]).any(), "cat_scian: códigos duplicados")
    exigir(df["descripcion"].ne("").all(), "cat_scian: descripciones vacías")

    df["fuente"] = "INEGI SCIAN"
    df["version_fuente"] = df["version_scian"]
    df["fecha_carga"] = fecha_carga
    return df[["codigo", "version_scian", "nivel", "descripcion", "es_retail", "es_servicio",
               "fuente", "version_fuente", "fecha_carga"]]


def transformar_crosswalk(engine, cvegeos: set, fecha_carga: datetime) -> pd.DataFrame:
    """Cada nombre se liga a su cvegeo con la clave que trae su propia fuente."""
    consultas = {
        ("INEGI Marco Geoestadístico", "Marco Geoestadístico 2020"):
            "SELECT DISTINCT nomgeo AS nombre, cvegeo FROM bronze.marco_geo_municipal",
        ("INEGI ITER", "Censo 2020"):
            f"SELECT DISTINCT nom_mun AS nombre, '{CLAVE_YUCATAN}' || mun AS cvegeo FROM bronze.iter_2020 "
            "WHERE loc = '0000' AND mun <> '000'",
        ("INEGI DENUE", "DENUE 2020-11 a 2025-05"):
            f"SELECT DISTINCT municipio AS nombre, '{CLAVE_YUCATAN}' || cve_mun AS cvegeo FROM bronze.denue",
        ("SESNSP", "IDM_NM_dic25"):
            "SELECT DISTINCT municipio AS nombre, cve_municipio AS cvegeo FROM bronze.incidencia_delictiva",
    }
    partes = []
    for (fuente, version), sql in consultas.items():
        p = pd.read_sql(sql, engine)
        p["fuente"], p["version_fuente"] = fuente, version
        partes.append(p)
    df = pd.concat(partes, ignore_index=True).dropna(subset=["nombre"])
    df["cvegeo"] = df["cvegeo"].str.strip()

    conflictos = df.groupby("nombre")["cvegeo"].nunique()
    exigir((conflictos == 1).all(), f"crosswalk: nombres con más de un cvegeo {conflictos[conflictos > 1].index.tolist()}")
    exigir(df["cvegeo"].isin(cvegeos).all(), "crosswalk: cvegeo que no existe en municipios")

    cw = df.groupby("nombre", as_index=False).agg(
        cvegeo=("cvegeo", "first"),
        fuente=("fuente", lambda s: ", ".join(sorted(set(s)))),
        version_fuente=("version_fuente", lambda s: ", ".join(sorted(set(s)))),
    )
    cw = cw.rename(columns={"nombre": "nombre_fuente"})
    cw["nombre_normalizado"] = cw["nombre_fuente"].map(normalizar_texto)
    cw["fecha_carga"] = fecha_carga
    return cw[["nombre_fuente", "nombre_normalizado", "cvegeo", "fuente", "version_fuente", "fecha_carga"]]


def transformar_censo(engine, cvegeos: set, fecha_carga: datetime) -> pd.DataFrame:
    df = pd.read_sql(
        f"SELECT entidad, mun, {', '.join(COLUMNAS_CENSO)} FROM bronze.iter_2020 "
        f"WHERE loc = '0000' AND entidad = '{CLAVE_YUCATAN}'",
        engine,
    )
    for c in COLUMNAS_CENSO:
        df[c] = a_entero(df[c], c)

    total_estatal = df[df["mun"] == "000"]
    exigir(len(total_estatal) == 1, "censo: no se encontró la fila del total estatal (MUN 000)")
    df = df[df["mun"] != "000"].copy()

    df["cvegeo"] = cvegeo_desde_cve_mun(df["mun"])
    exigir(len(df) == N_MUNICIPIOS, f"censo: se esperaban {N_MUNICIPIOS} filas, hay {len(df)}")
    exigir(df["cvegeo"].is_unique, "censo: cvegeo duplicado")
    exigir(df["cvegeo"].isin(cvegeos).all(), "censo: cvegeo que no existe en municipios")

    suma, total = df["pobtot"].sum(), total_estatal["pobtot"].iloc[0]
    exigir(suma == total, f"censo: la suma de pobtot ({suma}) no coincide con el total estatal ({total})")

    df["fuente"] = "INEGI ITER"
    df["version_fuente"] = "Censo 2020"
    df["fecha_carga"] = fecha_carga
    return df[["cvegeo", *COLUMNAS_CENSO, "fuente", "version_fuente", "fecha_carga"]]


def transformar_denue(engine, municipios: gpd.GeoDataFrame, cat_scian: pd.DataFrame,
                      fecha_carga: datetime, log: list) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT id, edicion, nom_estab, codigo_act, nombre_act, per_ocu, fecha_alta, "
        "latitud, longitud, cve_mun, scian FROM bronze.denue",
        engine,
    )
    log.append(("denue_establecimientos", "registros_bronze", len(df)))

    antes = len(df)
    df = df.drop_duplicates(subset=["id", "edicion"])
    log.append(("denue_establecimientos", "duplicados_eliminados", antes - len(df)))

    # Coordenadas nulas o no numéricas
    df["latitud"] = pd.to_numeric(df["latitud"], errors="coerce")
    df["longitud"] = pd.to_numeric(df["longitud"], errors="coerce")
    sin_coord = df["latitud"].isna() | df["longitud"].isna()
    log.append(("denue_establecimientos", "descartados_coordenada_nula", int(sin_coord.sum())))
    df = df[~sin_coord].reset_index(drop=True)

    puntos = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["longitud"], df["latitud"]), crs=SRID_DENUE,
    ).to_crs(SRID)
    puntos["cve_mun"] = puntos["cve_mun"].str.strip().str.zfill(3)
    puntos["cvegeo_original"] = cvegeo_desde_cve_mun(puntos["cve_mun"])
    poligonos = municipios[["cvegeo", "geom"]].rename_geometry("geometry")

    def resolver_empates(unidos: gpd.GeoDataFrame) -> pd.Series:
        """Un punto que toca 2+ polígonos se queda con el que coincide con su cve_mun; si ninguno, el cvegeo menor."""
        unidos = unidos.assign(_coincide=unidos["cvegeo"] == unidos["cvegeo_original"])
        unidos = unidos.sort_values(["_coincide", "cvegeo"], ascending=[False, True])
        return unidos[~unidos.index.duplicated(keep="first")]["cvegeo"]

    # 1) Punto dentro de un polígono
    dentro = gpd.sjoin(puntos[["cvegeo_original", "geometry"]], poligonos, how="inner", predicate="intersects")
    log.append(("denue_establecimientos", "puntos_en_limite_entre_municipios",
                dentro.index[dentro.index.duplicated()].nunique()))
    asignado = resolver_empates(dentro)

    # 2) Fuera de todo polígono: municipio más cercano dentro del umbral
    fuera = puntos.loc[~puntos.index.isin(asignado.index), ["cvegeo_original", "geometry"]]
    cercanos = gpd.sjoin_nearest(fuera, poligonos, how="inner", max_distance=UMBRAL_MAS_CERCANO_M)
    reasignados = resolver_empates(cercanos)
    log.append(("denue_establecimientos", "reasignados_mas_cercano", len(reasignados)))

    asignado = pd.concat([asignado, reasignados])
    descartados = len(puntos) - len(asignado)
    log.append(("denue_establecimientos", "descartados_fuera_umbral", descartados))
    print(f"     denue: {len(reasignados)} reasignados al municipio más cercano (<= {UMBRAL_MAS_CERCANO_M} m), "
          f"{descartados} descartados por estar fuera de Yucatán")

    puntos = puntos.loc[asignado.index].copy()
    puntos["cvegeo"] = asignado

    # fecha_alta: 'YYYY-MM' (algunas filas vienen como 'YYYY MM') -> DATE día 1
    fecha = puntos["fecha_alta"].str.strip()
    formato_ok = fecha.isna() | fecha.str.fullmatch(r"[0-9]{4}[- ][0-9]{2}", na=False)
    exigir(formato_ok.all(), f"denue: fecha_alta con formato desconocido {fecha[~formato_ok].unique()[:10].tolist()}")
    puntos["fecha_alta"] = fecha.str.replace(" ", "-") + "-01"

    puntos["codigo_act"] = puntos["codigo_act"].str.strip()
    prefijo = puntos["codigo_act"].str[:2]
    puntos["sector_scian"] = prefijo.map(SECTORES_RANGO).fillna(prefijo)
    puntos["version_scian"] = puntos["scian"]

    llaves_scian = set(zip(cat_scian["codigo"], cat_scian["version_scian"]))
    sin_catalogo = [k for k in set(zip(puntos["codigo_act"], puntos["version_scian"])) if k not in llaves_scian]
    exigir(not sin_catalogo, f"denue: códigos que no existen en cat_scian {sin_catalogo[:10]}")

    puntos["coincide_municipio"] = puntos["cvegeo_original"] == puntos["cvegeo"]
    puntos["id_denue"] = puntos["id"].str.strip().astype("int64")
    puntos["fuente"] = "INEGI DENUE"
    puntos["version_fuente"] = "DENUE " + puntos["edicion"].str.replace("_", "-")
    puntos["fecha_carga"] = fecha_carga
    puntos = puntos.rename(columns={
        "nom_estab": "nombre_establecimiento",
        "cve_mun": "cve_mun_original",
    })

    columnas = ["id_denue", "edicion", "nombre_establecimiento", "codigo_act", "version_scian", "sector_scian",
                "nombre_act", "per_ocu", "fecha_alta", "latitud", "longitud", "geometry", "cve_mun_original",
                "cvegeo", "coincide_municipio", "fuente", "version_fuente", "fecha_carga"]
    return geom_a_ewkb(puntos[columnas])


def transformar_incidencia(engine, crosswalk: pd.DataFrame, cvegeos: set, fecha_carga: datetime) -> pd.DataFrame:
    df = pd.read_sql(
        f"SELECT ano, cve_municipio, municipio, bien_juridico_afectado, tipo_de_delito, subtipo_de_delito, "
        f"modalidad, {', '.join(MESES)}, _source_url FROM bronze.incidencia_delictiva",
        engine,
    )
    # La fuente trae la clave del municipio, así que se usa directamente.
    # El crosswalk se usa solo para confirmar que nombre y clave apuntan al mismo municipio.
    df["cvegeo"] = df["cve_municipio"].str.strip()
    exigir(df["cvegeo"].isin(cvegeos).all(), "incidencia: cve_municipio que no existe en municipios")
    por_nombre = df["municipio"].map(crosswalk.set_index("nombre_fuente")["cvegeo"])
    exigir(por_nombre.notna().all(), "incidencia: nombres de municipio sin cobertura en el crosswalk")
    exigir((por_nombre == df["cvegeo"]).all(), "incidencia: nombre y clave de municipio no coinciden")

    df = df.melt(
        id_vars=["ano", "cvegeo", "bien_juridico_afectado", "tipo_de_delito", "subtipo_de_delito",
                 "modalidad", "_source_url"],
        value_vars=list(MESES), var_name="mes_nombre", value_name="incidentes",
    )
    df["mes"] = df["mes_nombre"].map(MESES)
    df["anio"] = a_entero(df["ano"], "ano")
    df["incidentes"] = a_entero(df["incidentes"], "incidentes")
    exigir(df["incidentes"].notna().all(), "incidencia: incidentes vacíos")

    df = df.rename(columns={
        "bien_juridico_afectado": "bien_juridico",
        "tipo_de_delito": "tipo_delito",
        "subtipo_de_delito": "subtipo_delito",
    })
    for c in ["bien_juridico", "tipo_delito", "subtipo_delito", "modalidad"]:
        df[c] = df[c].map(normalizar_texto)

    pk = ["cvegeo", "anio", "mes", "tipo_delito", "subtipo_delito", "modalidad"]
    exigir(not df.duplicated(pk).any(), "incidencia: llave duplicada tras normalizar categorías")

    df["fuente"] = "SESNSP"
    df["version_fuente"] = df["_source_url"].str.extract(r"([^/]+)\.csv$")[0]
    df["fecha_carga"] = fecha_carga
    return df[[*pk[:3], "bien_juridico", *pk[3:], "incidentes", "fuente", "version_fuente", "fecha_carga"]]


# --------------------------------------------------------------------------
# Carga
# --------------------------------------------------------------------------
def aplicar_ddl(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(INIT_SQL.read_text())


def cargar_silver(engine, tablas: dict, log: list, run_id: str, fecha_carga: datetime) -> None:
    # DECISIÓN HUMANA (temporal): TRUNCATE + INSERT en cada ejecución.
    # Se eligió así mientras se valida si es correcto recargar todo; si resulta
    # que no se debe hacer (p. ej. se necesita historial), se busca otra estrategia.
    # Todo corre en una transacción: si algo falla, silver queda como estaba.
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in TABLAS)}"))
        for nombre in TABLAS:
            df = tablas[nombre]
            df.to_sql(nombre, conn, schema=SCHEMA, if_exists="append", index=False,
                      method=copy_postgres, chunksize=50_000)
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
    log: list = []
    print(f"run_id: {run_id}")

    aplicar_ddl(engine)

    # Fail-fast: cualquier excepción detiene el script (no se atrapa a propósito).
    municipios = transformar_municipios(engine, fecha_carga, log)
    cvegeos = set(municipios["cvegeo"])
    cat_scian = transformar_cat_scian(engine, fecha_carga)
    crosswalk = transformar_crosswalk(engine, cvegeos, fecha_carga)

    tablas = {
        "municipios": geom_a_ewkb(municipios),
        "cat_scian": cat_scian,
        "crosswalk_municipio_nombre": crosswalk,
        "censo_municipal": transformar_censo(engine, cvegeos, fecha_carga),
        "denue_establecimientos": transformar_denue(engine, municipios, cat_scian, fecha_carga, log),
        "incidencia_delictiva": transformar_incidencia(engine, crosswalk, cvegeos, fecha_carga),
    }
    cargar_silver(engine, tablas, log, run_id, fecha_carga)

    print("Carga a silver completada.")


if __name__ == "__main__":
    main()
