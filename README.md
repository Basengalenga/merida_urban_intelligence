# merida_urban_intelligence

Data warehouse de Yucatán (`yucatan_dw`) construido con la arquitectura medallion
(bronze → silver → gold) sobre PostgreSQL + PostGIS.

## Para Gio: estado actual y lo que falta

**Estamos en la capa bronze.** La extracción y carga a bronze ya está hecha; lo
demás está pendiente.

| Fase | Estado |
| --- | --- |
| Capa bronze | ✅ Lista |
| Capa silver | ⏳ Pendiente |
| Capa gold | ⏳ Pendiente |
| API para acceder al warehouse (FastAPI) | ⏳ Pendiente |
| Dashboards (Metabase o Apache Superset, por decidir) | ⏳ Pendiente |
| Orquestación | ⏳ Pendiente |

Sobre la orquestación: si da tiempo, ver si jala Airflow. Si no da chance, basta
con un script que corra el código con las fases del medallion en orden
(bronze → silver → gold).

Los esquemas `silver` y `gold` ya existen en la base (se crean en `sql/init.sql`),
pero todavía no tienen tablas.

## Capa bronze

El script `dags/extractor_to_bronze.py` descarga cada fuente y la carga a su
propia tabla en el esquema `bronze`:

| Tabla | Fuente |
| --- | --- |
| `bronze.incidencia_delictiva` | SESNSP, incidencia delictiva municipal (solo Yucatán, clave 31) |
| `bronze.iter_2020` | INEGI, ITER Censo 2020, Yucatán |
| `bronze.denue` | INEGI DENUE, ediciones 2020–2025 |
| `bronze.marco_geo_municipal` | INEGI Marco Geoestadístico, polígonos municipales |

Reglas de bronze:

- Todas las columnas de la fuente se guardan como `TEXT`.
- Nombres de columna normalizados a snake_case sin acentos.
- Los datos se cargan tal como vienen, sin limpiar ni transformar.
- Se agregan los metadatos `_ingested_at`, `_source_url` y `_run_id`.
- Fail-fast: si cualquier descarga o carga falla, el script se detiene.
- Por ahora cada ejecución reemplaza la tabla completa (decisión temporal
  mientras se define la estrategia de re-ejecuciones).

## Cómo levantarlo

1. Levantar la base de datos (PostGIS en `localhost:5432`, base `yucatan_dw`,
   usuario `admin` / `admin123`):

   ```bash
   docker compose up -d
   ```

2. Instalar dependencias:

   ```bash
   pip install -r requirements.txt
   ```

3. Correr la carga a bronze:

   ```bash
   python dags/extractor_to_bronze.py
   ```

## Estructura

```
dags/extractor_to_bronze.py   # extracción y carga a bronze
sql/init.sql                  # extensión PostGIS y esquemas bronze/silver/gold
docker-compose.yml            # contenedor de PostGIS
requirements.txt              # dependencias de Python
```
