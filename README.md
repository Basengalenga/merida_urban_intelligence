# Yucatán Urban Intelligence

Data warehouse geoespacial de Yucatán (`yucatan_dw`) que integra datos demográficos,
económicos, geográficos y de seguridad pública a nivel **municipal**, construido con la
arquitectura medallion (bronze → silver → gold) sobre PostgreSQL + PostGIS.

## Objetivo

Calcular KPIs territoriales para los 106 municipios de Yucatán, compararlos entre sí y
evaluar si los patrones demográficos, económicos y de incidencia delictiva están
asociados geográficamente (correlaciones, Moran global, LISA y Moran bivariado).

**Alcance.** El proyecto comenzó con la ciudad de Mérida y se amplió a todo el estado de
Yucatán con el municipio como unidad de análisis. El cambio de alcance y el uso de la
incidencia delictiva municipal del SESNSP (en lugar de un dataset de crimen con
latitud/longitud) fueron aprobados por el profesor.

## Fuentes de datos

| Fuente | Tabla bronze | Grano original | Variables usadas |
| --- | --- | --- | --- |
| SESNSP, incidencia delictiva municipal del fuero común (`IDM_NM_dic25`), solo Yucatán (clave 31) | `bronze.incidencia_delictiva` | municipio × año × bien jurídico × tipo × subtipo × modalidad, con una columna por mes | `cve_municipio`, `municipio`, `ano`, `bien_juridico_afectado`, `tipo_de_delito`, `subtipo_de_delito`, `modalidad`, `enero` … `diciembre` |
| INEGI, Censo 2020, ITER Yucatán | `bronze.iter_2020` | localidad, más filas de totales municipales (`LOC = 0000`) y estatal (`MUN = 000`) | `pobtot`, `p_12ymas`, `pea`, `pe_inac`, `pob0_14`, `pob15_64`, `pob65_mas`, `vivtot`, `tvivhab` |
| INEGI DENUE, ediciones 2020_11, 2021_11, 2022_11, 2023_11, 2024_11 y 2025_05 | `bronze.denue` | establecimiento × edición | `id`, `nom_estab`, `codigo_act`, `nombre_act`, `per_ocu`, `fecha_alta`, `latitud`, `longitud`, `cve_mun` |
| INEGI Marco Geoestadístico, polígonos municipales de Yucatán | `bronze.marco_geo_municipal` | municipio (polígono, EPSG:6372) | `cvegeo`, `cve_ent`, `cve_mun`, `nomgeo`, geometría |
| INEGI, catálogo SCIAN 2018 y 2023 | `bronze.scian` | código × nivel (sector, subsector, rama, subrama, clase) × versión | `codigo`, `titulo` |

Las URLs exactas de descarga están en `src/extractor_to_bronze.py` y cada registro de bronze
guarda su `_source_url`.

## Estrategia geográfica

### Alternativas consideradas

Se consideró la **AGEB urbana**, pero se descartó porque los datos de seguridad (SESNSP)
solo vienen por municipio: no hay forma de asignar los incidentes a una unidad más fina.
Por eso se eligió el **municipio**, el nivel común a las cuatro fuentes; los polígonos
oficiales salen del Marco Geoestadístico. A cambio, se pierde la variación interna de cada
municipio (sobre todo en Mérida).

La llave de integración es `cvegeo` = `'31'` + `cve_mun` (3 dígitos). Nunca se une por
nombre: `silver.crosswalk_municipio_nombre` solo se usa para verificar que el nombre y la
clave de cada fuente apuntan al mismo municipio.

### Integración latitud/longitud → polígono (DENUE)

Los establecimientos del DENUE son las únicas observaciones puntuales del proyecto (la
incidencia delictiva ya viene agregada por municipio y se integra por clave).

1. Se crean los puntos desde `longitud`/`latitud` en EPSG:6365 (geográficas ITRF2008, el
   marco oficial de INEGI) y se reproyectan a EPSG:6372, el CRS de los polígonos.
2. Spatial join con GeoPandas (`predicate="intersects"`). Si un punto cae en el límite
   entre dos municipios, se queda con el que coincide con su `cve_mun` original; si
   ninguno coincide, con el `cvegeo` menor.
3. Los puntos que no caen en ningún polígono se asignan al municipio más cercano si están
   a **30 m o menos** (`sjoin_nearest`); los demás se descartan.
4. Se guarda `cve_mun_original` y `coincide_municipio` para comparar la asignación espacial
   con la clave que trae el DENUE.

**Origen del umbral de 30 m.** Se cruzaron los 809,101 puntos de `bronze.denue` (todas las
ediciones) contra los polígonos municipales con PostGIS (`ST_Intersects` y `ST_Distance` en
EPSG:6372). Solo 52 puntos quedan fuera:

| Grupo | Puntos | Distancia al polígono más cercano |
| --- | --- | --- |
| Borde | 7 | 2 a 25 m |
| Coordenada basura (10.258, -124.445, en el Pacífico) | 43 | ~3,600 km |
| Fuera del estado (Puebla y Sinaloa) | 2 | > 1,000 km |

El umbral deja 5 m de margen sobre el punto de borde más alejado (25 m).

### Arrecife Alacranes fuera del polígono de Progreso

El polígono de Progreso incluye el Arrecife Alacranes (42 partes, ~231 km², a ~120 km de
la costa). No es habitable e inflaba el área de Progreso (~658 km² en lugar de 426 km²), lo
que bajaba sus densidades. En silver se quitan las partes cuyo centroide cae en la caja del
arrecife (constante `CAJA_ALACRANES` en `src/bronze_to_silver.py`). Los demás cayos e islas
(Cayo Arenas en Progreso, las de Celestún y San Felipe) se conservan.

## Pipeline ETL

```
fuentes ──> bronze ──> silver ──> gold ──> gold.resultado_*
          (crudo)    (limpio +   (modelo    (análisis
                      spatial     estrella)  espacial)
                      join)
```

Cada etapa es un script de `src/` y el DAG `dags/pipeline_yucatan_dw.py` las ejecuta en
orden. Todas las tablas se definen en `sql/init.sql`.

### Bronze (`src/extractor_to_bronze.py`)

- Una función por fuente; descarga y carga a su propia tabla.
- Todas las columnas como `TEXT`, nombres en snake_case sin acentos, sin limpiar ni
  transformar. El único filtro es la entidad 31 en la incidencia (el archivo trae todo
  México).
- Metadatos: `_ingested_at`, `_source_url`, `_run_id`.
- Fail-fast: primero se descarga todo y luego se escribe la tabla dentro de una
  transacción; una descarga fallida nunca deja una tabla incompleta.
- Cada ejecución reemplaza la tabla completa.

El repositorio no guarda los archivos originales (no hay `data/raw/`): bronze es la copia
cruda e inalterada de cada fuente, dentro de la base, y se reconstruye en cualquier momento
desde las URLs oficiales (`_source_url`).

### Silver (`src/bronze_to_silver.py`)

- Valores confidenciales o no disponibles del INEGI (`*`, `N/D`) → `NULL`.
- Geometrías en EPSG:6372, reparadas con `make_valid` y convertidas a `MultiPolygon`;
  áreas en km².
- Columnas de trazabilidad: `fuente`, `version_fuente`, `fecha_carga`.
- Descartes, reasignaciones y conteos en `silver.etl_log`.
- Todo se transforma y valida en memoria y se carga en una sola transacción
  (TRUNCATE + INSERT).

Transformaciones que cambian el grano, el significado o la representación geográfica:

| Tabla | Transformación |
| --- | --- |
| `municipios` | Se quita el Arrecife Alacranes del polígono de Progreso |
| `censo_municipal` | Solo filas de total municipal (`LOC = 0000`); grano localidad → municipio. Se valida que la suma de `pobtot` coincida con el total estatal |
| `denue_establecimientos` | Lat/lon → punto en EPSG:6372 → municipio por spatial join (sustituye al `cve_mun` de la fuente). Se eliminan duplicados por (`id`, `edicion`) y los registros sin coordenadas. `fecha_alta` (`AAAA-MM`) pasa a fecha con día 1 |
| `incidencia_delictiva` | De ancho a largo: una columna por mes → una fila por mes (grano municipio × mes × delito). Categorías normalizadas (minúsculas, sin acentos) |
| `cat_scian` | Se omite el nivel subrama; sectores 31-33 y 48-49 como rango; banderas `es_retail` (sector 46) y `es_servicio` (sectores 51 a 81) |

Las validaciones post-carga están en `validaciones/silver.sql` (conteos, FKs sin
huérfanos, cobertura de los 106 municipios, geometrías válidas y SRID).

### Gold (`src/silver_to_gold.py` y `src/gold_analytics.py`)

`silver_to_gold.py` trunca gold, ejecuta `sql/gold/gold.sql` y valida contra silver en una
sola transacción: 106 municipios, 106 × 6 filas en el panel, suma de población, incidentes
por año, establecimientos por edición y que ningún municipio quede sin vecinos.
`gold_analytics.py` lee solo de gold y escribe los resultados en `gold.resultado_*`. Los dos
registran sus conteos en `gold.etl_log`.

`validaciones/gold.sql` calcula cada KPI requerido directamente desde las tablas de hechos y
dimensiones, sin pasar por la vista `gold.kpi_municipio_anio`, para demostrar que el
warehouse conserva la información necesaria sin volver a las fuentes.

## Data warehouse

Documentación del modelo en `docs/`:

- Diagrama dimensional: [`docs/warehouse_model.png`](docs/warehouse_model.png) (fuente
  Graphviz en `warehouse_model.dot`; versión Mermaid y granos en
  [`docs/warehouse_model.md`](docs/warehouse_model.md)).
- Diccionario de datos por capa: [bronze](docs/data_dictionary_bronze.md),
  [silver](docs/data_dictionary_silver.md) y [gold](docs/data_dictionary_gold.md).

### Modelo

| Tabla / vista | Tipo | Grano |
| --- | --- | --- |
| `dim_municipio` | dimensión | municipio (`cvegeo`), con polígono y área |
| `dim_tiempo` | dimensión | año-mes, 2020 a 2025 (`id_tiempo` = AAAAMM) |
| `dim_actividad` | dimensión | clase SCIAN + versión (2018 y 2023), con rama, subsector y sector |
| `dim_delito` | dimensión | bien jurídico + tipo + subtipo + modalidad |
| `fact_censo` | hecho | municipio (Censo 2020) |
| `fact_establecimiento` | hecho (snapshot periódico) | establecimiento × edición DENUE, con punto |
| `fact_incidencia` | hecho | municipio × mes × delito, 2020 a 2025 |
| `vecinos_municipio` | puente | par de municipios contiguos (cada par en ambas direcciones) |
| `kpi_municipio_anio` | vista | municipio × año, 2020 a 2025 |
| `cociente_localizacion` | vista | municipio × año × sector (solo sectores presentes) |
| `resultado_correlacion` | resultado | año × par de variables × método |
| `resultado_moran_global` | resultado | año × indicador |
| `resultado_lisa` | resultado | año × indicador × municipio |
| `resultado_moran_bivariado` | resultado | año × par de variables |

Relaciones:

- `fact_censo` → `dim_municipio`
- `fact_establecimiento` → `dim_municipio`, `dim_actividad`, `dim_tiempo` (mes de la edición)
- `fact_incidencia` → `dim_municipio`, `dim_tiempo`, `dim_delito`
- `vecinos_municipio` → `dim_municipio` (dos veces)

**`fact_establecimiento` NO se suma entre ediciones.** Un mismo establecimiento aparece una
vez por edición; sumar ediciones lo cuenta varias veces. Siempre hay que filtrar una sola
edición (como hacen las vistas).

## KPIs

Se calculan en la vista `gold.kpi_municipio_anio`, por municipio y año.

| Categoría | KPI | Columna | Fórmula |
| --- | --- | --- | --- |
| Demográfico | Población total | `poblacion_total` | `pobtot` |
| Demográfico | Densidad poblacional | `densidad_poblacional` | `pobtot / area_km2` |
| Demográfico | Tasa de PEA | `tasa_pea` | `pea / p_12ymas` |
| Demográfico | Población por grupo de edad | `prop_0_14`, `prop_15_64`, `prop_65_mas` | `pob0_14 / pobtot`, `pob15_64 / pobtot`, `pob65_mas / pobtot` |
| Económico | Total de negocios | `total_negocios` | establecimientos de la edición del año |
| Económico | Densidad de negocios | `densidad_negocios` | `total_negocios / area_km2` |
| Económico | Negocios por 1,000 habitantes | `negocios_por_1000_hab` | `1000 × total_negocios / pobtot` |
| Económico | Densidad retail | `densidad_retail` | negocios del sector 46 / `area_km2` |
| Económico | Densidad de servicios | `densidad_servicios` | negocios de los sectores 51 a 81 / `area_km2` |
| Económico | Actividad dominante | `sector_dominante`, `desc_sector_dominante` | sector con más negocios; empate → código de sector menor |
| Seguridad | Total de incidentes | `total_incidentes` | suma de los 12 meses del año |
| Seguridad | Tasa delictiva | `tasa_delictiva_1000_hab` | `1000 × total_incidentes / pobtot` |
| Seguridad | Incidentes por tipo y tiempo | — | `fact_incidencia` × `dim_delito` × `dim_tiempo` (endpoint `/incidencia-mensual`) |
| Seguridad | Crimen relativo a la actividad económica | `incidentes_por_100_negocios` | `100 × total_incidentes / total_negocios` |
| Económico (adicional) | Cociente de localización | `cociente_localizacion.lq` | (negocios del sector en el municipio / negocios del municipio) / (negocios del sector en el estado / negocios del estado) |

## Análisis espacial

`src/gold_analytics.py`, para cada año de 2020 a 2025 (999 permutaciones, semilla fija,
significancia al 0.05):

- **Correlación** (Pearson y Spearman): tasa delictiva vs negocios por 1,000 hab, densidad
  poblacional vs densidad de negocios, tasa de PEA vs tasa delictiva.
- **Moran global**: tasa delictiva por 1,000 hab y densidad de negocios.
- **LISA**: los mismos dos indicadores; cuadrantes HH, HL, LH y LL.
- **Moran bivariado**: tasa delictiva vs densidad de negocios.

Los resultados y su interpretación están en [`hallazgos.md`](hallazgos.md).

### Mapas y figuras

`src/figuras.py` lee solo de gold y genera PNG en `outputs/` (93 archivos):

| Carpeta | Contenido |
| --- | --- |
| `outputs/maps/<año>/` | Coropletas de población, densidad poblacional, densidad de negocios, negocios por 1,000 hab, tasa delictiva e incidentes por 100 negocios; mapas LISA de los dos indicadores; sector dominante |
| `outputs/figures/<año>/` | Dispersión de las tres correlaciones, diagramas de Moran, incidentes por tipo de delito |
| `outputs/figures/serie/` | Series 2020–2025: Moran global, incidentes y establecimientos por año |

Las coropletas de un mismo KPI usan los mismos cortes en todos los años (quintiles del panel
2020–2025), para que los mapas de distintos años sean comparables. No forma parte del DAG;
ver el paso 5 de [Cómo reproducirlo](#cómo-reproducirlo).

### Regla de vecindad (Queen)

Dos municipios son vecinos si comparten un borde o solo un vértice (contigüidad Queen). Se
calcula con `ST_Intersects` y no con `ST_Touches`: 45 de los 288 pares contiguos se enciman
unos m² (máx. 3.61 m², imprecisión del Marco Geoestadístico), y `ST_Touches` los
descartaba; Kanasín se quedaba sin vecinos. Ningún par de municipios separados está a menos
de 1 m, así que no se agregan falsos vecinos.

Implicaciones:

- Los pesos espaciales se estandarizan por fila: el rezago espacial de un municipio es el
  promedio de sus vecinos, sin importar cuántos tenga.
- Contar como vecinos a municipios que solo comparten un vértice amplía un poco la vecindad
  respecto a Rook (solo bordes).
- Municipios con pocos vecinos (en la costa o en el borde del estado) tienen un rezago
  espacial basado en menos observaciones; no se consideran los municipios de Campeche ni de
  Quintana Roo.

## Supuestos, calidad de datos y cautelas

### Supuestos

- La población es la del **Censo 2020 para todos los años** del panel; las tasas por
  habitante de 2021 a 2025 usan ese denominador.
- Para cada año se usa la edición DENUE de noviembre, salvo **2025, que usa la edición de
  mayo** (2025_05), porque no existe la de noviembre.
- Las ediciones 2020 a 2023 usan SCIAN 2018 y las de 2024 y 2025 usan SCIAN 2023.
- La incidencia delictiva es la registrada por el SESNSP (delitos denunciados del fuero
  común), no la criminalidad real.

### Calidad de datos

- **Caída abrupta de incidencia en 2022.** Los incidentes del estado pasan de 8,565 (2021) a
  4,209 (2022); en Mérida de 6,522 a 1,820. Parece un cambio de registro de la fuente, no de
  criminalidad real, y afecta cualquier comparación entre años.
- **Salto del DENUE en 2024.** El total del estado pasa de ~130 mil a ~142 mil
  establecimientos, justo cuando la clasificación cambia de SCIAN 2018 a SCIAN 2023. Puede
  ser un efecto de la fuente y no un cambio real.
- 52 puntos del DENUE fuera de los polígonos (ver [estrategia geográfica](#integración-latitudlongitud--polígono-denue)).
- 45 pares de polígonos vecinos se enciman unos m² (ver [regla de vecindad](#regla-de-vecindad-queen)).

### Limitaciones geográficas y cautelas

- **La incidencia delictiva no está georreferenciada**: solo se puede analizar a nivel
  municipal y no hay forma de ubicar incidentes dentro de un municipio.
- **El municipio oculta la variación interna**, sobre todo en Mérida, y los resultados
  dependen de esta unidad (problema de la unidad de área modificable, MAUP).
- **Mérida es un outlier** (mucha más población, negocios e incidentes que el resto) y puede
  dominar la correlación de Pearson; Spearman, por rangos, es más robusta.
- Las **tasas son inestables en municipios de baja población**: pocos incidentes mueven
  mucho la tasa por 1,000 habitantes.
- **Asociación espacial no implica causalidad.**

## Cómo reproducirlo

Requisitos: Docker con Docker Compose.

1. Levantar los contenedores:

   ```bash
   docker compose up -d
   ```

   | Servicio | Contenedor | Acceso |
   | --- | --- | --- |
   | PostGIS (warehouse) | `yucatan_dw` | `localhost:5432`, base `yucatan_dw`, usuario `admin` / `admin123` |
   | Airflow | `yucatan_airflow` | http://localhost:8080, usuario `admin` / `admin123` |
   | API (FastAPI) | `yucatan_api` | solo dentro de la red de Docker (`http://api:8000`) |
   | Dashboard (Streamlit) | `yucatan_dashboard` | http://localhost:8501 |

   Al crear el volumen de la base por primera vez, Docker ejecuta `sql/init.sql` (PostGIS,
   esquemas y tablas). Los scripts de silver y gold lo vuelven a aplicar al inicio, porque
   es idempotente.

2. En Airflow, activar y ejecutar manualmente el DAG `pipeline_yucatan_dw`:

   ```
   extraer_incidencia ─┐
   extraer_iter       ─┤
   extraer_denue      ─┼──> bronze_to_silver ──> silver_to_gold ──> gold_analytics
   extraer_marco_geo  ─┤
   extraer_scian      ─┘
   ```

   Los 5 extractores corren en paralelo; si cualquiera falla, silver no se toca. Los
   extractores de una misma corrida comparten `_run_id` (derivado del run de Airflow) e
   `_ingested_at` (`logical_date` del run).

3. (Opcional) Correr las validaciones de silver y gold:

   ```bash
   docker exec -i yucatan_dw psql -U admin -d yucatan_dw < validaciones/silver.sql
   docker exec -i yucatan_dw psql -U admin -d yucatan_dw < validaciones/gold.sql
   ```

   `validaciones/gold.sql` usa por defecto el año 2024 (variables `anio` y `edicion` al
   inicio del archivo).

4. Abrir el dashboard en http://localhost:8501.

5. Generar los mapas y figuras de `outputs/` (con gold ya cargado), en un contenedor
   desechable con la imagen de Airflow:

   ```bash
   docker compose run --rm --no-deps --user "$(id -u):0" \
     -v "$PWD/outputs:/opt/airflow/outputs" \
     --entrypoint python airflow /opt/airflow/src/figuras.py
   ```

   `--user` hace que los PNG queden a nombre de tu usuario y no del usuario de Airflow. Cada
   ejecución sobrescribe los PNG. Si la imagen se construyó antes de agregar `matplotlib` a
   `requirements.txt`, reconstruirla primero con `docker compose build airflow`.

El contenedor `airflow` monta `./dags`, `./src` y `./sql` en `/opt/airflow/` como solo
lectura (`:ro`): Airflow solo necesita leerlos, y así no los modifica por accidente. Los
cambios hechos en la máquina se ven al instante dentro del contenedor.

### API y dashboard

El dashboard no se conecta a PostgreSQL: pide los datos de gold a la API (`api/main.py`), y
la API es la única que consulta la base.

| Endpoint | Devuelve |
| --- | --- |
| `GET /health` | Estado de la API (lo usa el healthcheck de Docker) |
| `GET /municipios/geojson` | Polígonos de los municipios (FeatureCollection, EPSG:4326) |
| `GET /kpi` | `gold.kpi_municipio_anio` |
| `GET /incidencia-mensual` | Incidentes por mes, municipio y tipo de delito |
| `GET /cociente-localizacion` | `gold.cociente_localizacion` |
| `GET /resultados/correlacion` | `gold.resultado_correlacion` |
| `GET /resultados/moran-global` | `gold.resultado_moran_global` |
| `GET /resultados/moran-bivariado` | `gold.resultado_moran_bivariado` |
| `GET /resultados/lisa` | `gold.resultado_lisa` |
| `GET /etl-log` | Las dos últimas corridas de `gold.etl_log` |

Para correr la API y el dashboard fuera de Docker (con la base levantada):

```bash
cd api && pip install -r requirements.txt
DB_URL=postgresql+psycopg2://admin:admin123@localhost:5432/yucatan_dw uvicorn main:app --port 8000
# en otra terminal
cd dashboard && pip install -r requirements.txt && streamlit run main.py
```

La documentación interactiva de la API queda en http://localhost:8000/docs. El dashboard
usa `API_URL` (por defecto `http://localhost:8000`).

## Para explorar y aprender

Airflow corre aquí en modo `standalone`: un solo contenedor levanta el webserver, el
scheduler y el triggerer juntos. Es cómodo para empezar, pero en despliegues reales esos
componentes suelen vivir en contenedores separados. Queda pendiente investigar cómo se
reparten: qué hace exactamente cada uno, cómo se coordinan a través de la base de metadatos
(`airflow-db`), qué se gana al escalarlos por separado y qué cambia al pasar de
`LocalExecutor` a `CeleryExecutor` o `KubernetesExecutor`. Un buen punto de partida es el
`docker-compose.yaml` oficial de Airflow, que arma justo esa versión separada.

## Estructura

```
src/extractor_to_bronze.py    # extracción y carga a bronze
src/bronze_to_silver.py       # bronze -> silver (limpieza y spatial join)
src/silver_to_gold.py         # silver -> gold (ejecuta sql/gold/gold.sql y valida)
src/gold_analytics.py         # correlaciones, Moran, LISA -> gold.resultado_*
src/figuras.py                # mapas y figuras desde gold -> outputs/
dags/pipeline_yucatan_dw.py   # DAG de Airflow con todo el pipeline
sql/init.sql                  # extensión PostGIS, esquemas y DDL de todas las tablas
sql/gold/gold.sql             # carga de gold y vistas (en subcarpeta: Docker no lo ejecuta)
validaciones/silver.sql       # validaciones post-carga de silver
validaciones/gold.sql         # cálculo de cada KPI desde hechos y dimensiones
docs/warehouse_model.*        # diagrama dimensional (PNG, Graphviz y Mermaid)
docs/data_dictionary_*.md     # diccionario de datos de bronze, silver y gold
outputs/maps/                 # coropletas y mapas LISA por año
outputs/figures/              # correlaciones, Moran, incidentes por tipo y series
api/                          # API FastAPI sobre gold
dashboard/                    # dashboard Streamlit que consume la API
airflow/Dockerfile            # imagen de Airflow con las dependencias de src/
docker-compose.yml            # PostGIS, API, dashboard y Airflow
requirements.txt              # dependencias de Python del pipeline
hallazgos.md                  # resultados e interpretación del análisis
```
