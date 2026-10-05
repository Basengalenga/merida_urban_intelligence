"""
Pipeline completo de yucatan_dw: bronze -> silver -> gold -> analytics.

    extraer_incidencia ─┐
    extraer_iter       ─┤
    extraer_denue      ─┼──> bronze_to_silver ──> silver_to_gold ──> gold_analytics
    extraer_marco_geo  ─┤
    extraer_scian      ─┘

Los 5 extractores corren en paralelo, uno por fuente. bronze_to_silver espera a
los 5: si cualquiera falla, silver no se toca (mismo fail-fast que el script).

Metadatos de bronze:
    Los 5 extractores comparten run_id e _ingested_at, derivados del DAG run para
    que un reintento de cualquier tarea escriba los mismos valores:
        run_id       = uuid5 de "<dag_id>/<run_id de Airflow>"
        _ingested_at = logical_date del DAG run (inicio del lote, no la hora en
                       que se escribió cada tabla)

Los scripts de src/ se importan dentro de cada tarea, no al inicio del archivo,
para que el scheduler no cargue pandas/geopandas/esda cada vez que parsea el DAG.
"""

import sys
import uuid
from pathlib import Path

import pendulum
from airflow.decorators import dag, task

# dags/ y src/ se montan lado a lado en /opt/airflow/
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

EXTRACCIONES = {
    "extraer_incidencia": "extraer_cargar_incidencia",
    "extraer_iter": "extraer_cargar_iter",
    "extraer_denue": "extraer_cargar_denue",
    "extraer_marco_geo": "extraer_cargar_marco_geo",
    "extraer_scian": "extraer_cargar_scian",
}


def ejecutar_extraccion(funcion: str, dag_run=None, logical_date=None) -> None:
    from sqlalchemy import create_engine

    import extractor_to_bronze

    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{dag_run.dag_id}/{dag_run.run_id}"))
    print(f"run_id: {run_id} (Airflow: {dag_run.run_id})")

    engine = create_engine(extractor_to_bronze.DB_URL)
    getattr(extractor_to_bronze, funcion)(engine, run_id, logical_date)


@dag(
    dag_id="pipeline_yucatan_dw",
    schedule=None,  # solo manual
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["yucatan_dw"],
)
def pipeline_yucatan_dw():
    extracciones = [
        task(task_id=task_id)(ejecutar_extraccion)(funcion)
        for task_id, funcion in EXTRACCIONES.items()
    ]

    @task
    def bronze_to_silver():
        import bronze_to_silver
        bronze_to_silver.main()

    @task
    def silver_to_gold():
        import silver_to_gold
        silver_to_gold.main()

    @task
    def gold_analytics():
        import gold_analytics
        gold_analytics.main()

    extracciones >> bronze_to_silver() >> silver_to_gold() >> gold_analytics()


pipeline_yucatan_dw()
