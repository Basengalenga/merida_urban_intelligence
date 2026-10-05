"""
API de yucatan_dw (FastAPI).

Expone en JSON la capa gold que consume el dashboard: vistas kpi_municipio_anio y
cociente_localizacion, hechos y dimensiones, y las tablas resultado_* de src/gold_analytics.py.

Correr local:   uvicorn main:app --port 8000   (desde api/)
Correr Docker:  docker compose up -d  ->  http://api:8000 dentro de la red de docker
"""

import json
import os
from decimal import Decimal

import pandas as pd
from fastapi import FastAPI, Response
from sqlalchemy import create_engine

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
DB_URL = os.environ.get("DB_URL", "postgresql+psycopg2://admin:admin123@db:5432/yucatan_dw")

COLUMNAS_KPI = [
    "anio", "cvegeo", "nombre", "sector_dominante", "desc_sector_dominante",
    "poblacion_total", "densidad_poblacional", "tasa_pea", "prop_0_14", "prop_15_64", "prop_65_mas",
    "total_negocios", "densidad_negocios", "negocios_por_1000_hab", "densidad_retail", "densidad_servicios",
    "total_incidentes", "tasa_delictiva_1000_hab", "incidentes_por_100_negocios",
]

motor = create_engine(DB_URL)
app = FastAPI(title="Yucatán Urban Intelligence API")


# --------------------------------------------------------------------------
# Datos (todo desde gold)
# --------------------------------------------------------------------------
def consulta(sql: str) -> pd.DataFrame:
    df = pd.read_sql(sql, motor)
    # Las columnas NUMERIC de PostgreSQL llegan como Decimal; se pasan a float
    for c in df.columns:
        if df[c].map(lambda v: isinstance(v, Decimal)).any():
            df[c] = df[c].astype(float)
    if "cvegeo" in df.columns:
        df["cvegeo"] = df["cvegeo"].str.strip()
    return df


def registros(sql: str) -> Response:
    # to_json convierte NaN en null y las fechas a ISO 8601; double_precision=15 evita que redondee a 10 decimales
    return Response(consulta(sql).to_json(orient="records", date_format="iso", double_precision=15), media_type="application/json")


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/municipios/geojson")
def municipios_geojson():
    df = consulta("""
        SELECT cvegeo, nombre,
               ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, 100), 4326), 5) AS g
        FROM gold.dim_municipio
    """)
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"cvegeo": r.cvegeo, "nombre": r.nombre}, "geometry": json.loads(r.g)}
            for r in df.itertuples()
        ],
    }


@app.get("/kpi")
def kpi():
    return registros(f"SELECT {', '.join(COLUMNAS_KPI)} FROM gold.kpi_municipio_anio")


@app.get("/incidencia-mensual")
def incidencia_mensual():
    return registros("""
        SELECT t.id_tiempo, t.anio, t.mes, t.nombre_mes, d.bien_juridico, d.tipo_delito, i.cvegeo,
               sum(i.incidentes) AS incidentes
        FROM gold.fact_incidencia i
        JOIN gold.dim_tiempo t USING (id_tiempo)
        JOIN gold.dim_delito d USING (id_delito)
        WHERE i.incidentes > 0
        GROUP BY 1, 2, 3, 4, 5, 6, 7
    """)


@app.get("/cociente-localizacion")
def cociente_localizacion():
    return registros("SELECT * FROM gold.cociente_localizacion")


@app.get("/resultados/correlacion")
def resultado_correlacion():
    return registros("SELECT * FROM gold.resultado_correlacion")


@app.get("/resultados/moran-global")
def resultado_moran_global():
    return registros("SELECT * FROM gold.resultado_moran_global")


@app.get("/resultados/moran-bivariado")
def resultado_moran_bivariado():
    return registros("SELECT * FROM gold.resultado_moran_bivariado")


@app.get("/resultados/lisa")
def resultado_lisa():
    return registros("SELECT * FROM gold.resultado_lisa")


@app.get("/etl-log")
def etl_log():
    return registros("""
        SELECT tabla, metrica, valor, fecha_carga FROM gold.etl_log
        WHERE run_id IN (SELECT run_id FROM gold.etl_log GROUP BY run_id ORDER BY max(fecha_carga) DESC LIMIT 2)
        ORDER BY fecha_carga, id
    """)
