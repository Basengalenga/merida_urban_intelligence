"""
Transformación silver -> gold de yucatan_dw.

La carga es SQL puro (sql/gold/gold.sql): dimensiones, hechos, vecinos_municipio
y las vistas kpi_municipio_anio y cociente_localizacion. Este script la ejecuta,
valida el resultado contra silver y registra los conteos en gold.etl_log.

Las tablas gold.resultado_* no se tocan aquí; las llena src/gold_analytics.py.

Los hechos llevan columnas de trazabilidad: fuente y version_fuente (copiadas de
silver), fecha_carga y run_id (de esta corrida; se cruzan con gold.etl_log).

Manejo de errores (fail-fast):
    TRUNCATE, carga y validaciones corren en una sola transacción. Si algo falla,
    gold queda como estaba.

El DDL vive en sql/init.sql. Este script lo ejecuta al inicio (es idempotente),
porque Docker solo lo corre cuando el volumen de la base es nuevo.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
DB_URL = "postgresql+psycopg2://admin:admin123@db:5432/yucatan_dw"
SCHEMA = "gold"
INIT_SQL = Path(__file__).resolve().parent.parent / "sql" / "init.sql"
GOLD_SQL = Path(__file__).resolve().parent.parent / "sql" / "gold" / "gold.sql"

N_MUNICIPIOS = 106
ANIOS_PANEL = range(2020, 2026)

# Tablas que se recargan. etl_log y resultado_* no se truncan aquí.
TABLAS = [
    "dim_municipio",
    "dim_tiempo",
    "dim_actividad",
    "dim_delito",
    "fact_censo",
    "fact_establecimiento",
    "fact_incidencia",
    "vecinos_municipio",
]


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def exigir(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise ValueError(mensaje)


def escalar(conn, sql: str):
    return conn.execute(text(sql)).scalar()


# --------------------------------------------------------------------------
# Validaciones
# --------------------------------------------------------------------------
def validar(conn) -> None:
    n = escalar(conn, "SELECT count(*) FROM gold.dim_municipio")
    exigir(n == N_MUNICIPIOS, f"dim_municipio: se esperaban {N_MUNICIPIOS} filas, hay {n}")

    n = escalar(conn, "SELECT count(*) FROM gold.kpi_municipio_anio")
    esperadas = N_MUNICIPIOS * len(ANIOS_PANEL)
    exigir(n == esperadas, f"kpi_municipio_anio: se esperaban {esperadas} filas, hay {n}")

    gold, silver = (escalar(conn, "SELECT sum(pobtot) FROM gold.fact_censo"),
                    escalar(conn, "SELECT sum(pobtot) FROM silver.censo_municipal"))
    exigir(gold == silver, f"fact_censo: suma de pobtot ({gold}) no coincide con silver ({silver})")

    diferencias = conn.execute(text(f"""
        SELECT s.anio, s.incidentes AS silver, g.incidentes AS gold
        FROM (SELECT anio, sum(incidentes) AS incidentes FROM silver.incidencia_delictiva
              WHERE anio BETWEEN {min(ANIOS_PANEL)} AND {max(ANIOS_PANEL)} GROUP BY anio) s
        FULL JOIN (SELECT t.anio, sum(i.incidentes) AS incidentes FROM gold.fact_incidencia i
                   JOIN gold.dim_tiempo t USING (id_tiempo) GROUP BY t.anio) g USING (anio)
        WHERE s.incidentes IS DISTINCT FROM g.incidentes
    """)).fetchall()
    exigir(not diferencias, f"fact_incidencia: incidentes por año no coinciden con silver {diferencias}")

    diferencias = conn.execute(text("""
        SELECT edicion, s.n AS silver, g.n AS gold
        FROM (SELECT edicion, count(*) AS n FROM silver.denue_establecimientos GROUP BY edicion) s
        FULL JOIN (SELECT edicion, count(*) AS n FROM gold.fact_establecimiento GROUP BY edicion) g USING (edicion)
        WHERE s.n IS DISTINCT FROM g.n
    """)).fetchall()
    exigir(not diferencias, f"fact_establecimiento: conteo por edición no coincide con silver {diferencias}")

    sin_vecinos = conn.execute(text("""
        SELECT cvegeo, nombre FROM gold.dim_municipio
        WHERE cvegeo NOT IN (SELECT cvegeo FROM gold.vecinos_municipio)
    """)).fetchall()
    exigir(not sin_vecinos, f"vecinos_municipio: municipios sin vecinos {sin_vecinos}")


# --------------------------------------------------------------------------
# Carga
# --------------------------------------------------------------------------
def aplicar_ddl(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(INIT_SQL.read_text())


def cargar_gold(engine, run_id: str, fecha_carga: datetime) -> None:
    # DECISIÓN HUMANA (temporal): TRUNCATE + INSERT en cada ejecución.
    # Se eligió así mientras se valida si es correcto recargar todo; si resulta
    # que no se debe hacer (p. ej. se necesita historial), se busca otra estrategia.
    # Todo corre en una transacción: si algo falla, gold queda como estaba.
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in TABLAS)} RESTART IDENTITY"))
        # Trazabilidad: gold.sql lee estos valores con current_setting() al cargar los hechos.
        # is_local = true: solo viven dentro de esta transacción.
        conn.execute(
            text("SELECT set_config('gold.run_id', :run_id, true), set_config('gold.fecha_carga', :fecha_carga, true)"),
            {"run_id": run_id, "fecha_carga": fecha_carga.isoformat()},
        )
        conn.exec_driver_sql(GOLD_SQL.read_text())

        validar(conn)

        log = []
        for nombre in [*TABLAS, "kpi_municipio_anio", "cociente_localizacion"]:
            n = escalar(conn, f"SELECT count(*) FROM {SCHEMA}.{nombre}")
            log.append((nombre, "filas_cargadas", n))
            print(f"OK   {SCHEMA}.{nombre}: {n:,} registros")

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

    aplicar_ddl(engine)

    # Fail-fast: cualquier excepción detiene el script (no se atrapa a propósito).
    cargar_gold(engine, run_id, fecha_carga)

    print("Carga a gold completada.")


if __name__ == "__main__":
    main()
