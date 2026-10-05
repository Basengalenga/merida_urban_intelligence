# Data dictionary — silver layer

Clean, typed and standardized data, one table per source. Loaded by
`src/bronze_to_silver.py`; DDL in `sql/init.sql`; post-load checks in
`validaciones/silver.sql`. Other layers: [bronze](data_dictionary_bronze.md) ·
[gold](data_dictionary_gold.md).

**Silver rules**

- Integration key: `cvegeo CHAR(5)` = `'31'` + 3-digit municipal code. Tables are never
  joined by name.
- Geometries in EPSG:6372 (Mexico ITRF2008 / LCC, metres); areas in km².
- INEGI confidential or unavailable values (`*`, `N/D`) → `NULL`.
- Every table is reloaded with TRUNCATE + INSERT in a single transaction.

**Lineage columns (all tables except `etl_log`)**

| Column | Type | Description |
| --- | --- | --- |
| `fuente` | TEXT | Source name (e.g. `INEGI DENUE`, `SESNSP`) |
| `version_fuente` | TEXT | Source version / edition (e.g. `Censo 2020`, `DENUE 2024-11`, `IDM_NM_dic25`) |
| `fecha_carga` | TIMESTAMPTZ | Time of the silver run that loaded the row (UTC) |

---

## `silver.municipios`

Municipal polygons. **Grain:** one municipality. **Rows:** 106. **From:** `bronze.marco_geo_municipal`.

| Column | Type | Key / constraint | Description |
| --- | --- | --- | --- |
| `cvegeo` | CHAR(5) | PK; `= cve_ent \|\| cve_mun` | Geostatistical key |
| `cve_ent` | CHAR(2) | NOT NULL, `= '31'` | State code |
| `cve_mun` | CHAR(3) | NOT NULL, 3 digits | Municipal code |
| `nombre_municipio` | TEXT | NOT NULL | Official name (`nomgeo`) |
| `geom` | geometry(MultiPolygon, 6372) | NOT NULL, `ST_IsValid`, GiST index | Boundary, repaired with `make_valid` and cast to MultiPolygon. **Progreso (31059) excludes the Arrecife Alacranes parts** (centroid inside `CAJA_ALACRANES`) |
| `area_km2` | DOUBLE PRECISION | NOT NULL, > 0 | Area of `geom` in km² |

---

## `silver.censo_municipal`

Census 2020 municipal totals. **Grain:** one municipality. **Rows:** 106. **From:**
`bronze.iter_2020` rows with `loc = '0000'` and `mun <> '000'` (grain changes from locality
file to municipal totals). The sum of `pobtot` is validated against the state total row.

| Column | Type | Key / constraint | Description |
| --- | --- | --- | --- |
| `cvegeo` | CHAR(5) | PK, FK → `municipios` | Municipality |
| `pobtot` | INTEGER | ≥ 0 | Total population |
| `p_12ymas` | INTEGER | ≥ 0 | Population aged 12 and over |
| `pea` | INTEGER | ≥ 0 | Economically active population (12+) |
| `pe_inac` | INTEGER | ≥ 0 | Economically inactive population (12+) |
| `pob0_14` | INTEGER | ≥ 0 | Population aged 0–14 |
| `pob15_64` | INTEGER | ≥ 0 | Population aged 15–64 |
| `pob65_mas` | INTEGER | ≥ 0 | Population aged 65+ |
| `vivtot` | INTEGER | ≥ 0 | Total dwellings |
| `tvivhab` | INTEGER | ≥ 0 | Inhabited dwellings |

All measures are nullable (`NULL` = `*` or `N/D` in the source).

---

## `silver.cat_scian`

SCIAN catalogue. **Grain:** code × SCIAN version. **Rows:** 3,009. **From:** `bronze.scian`
(sheets SECTOR, SUBSECTOR, RAMA, CLASE; the SUBRAMA level is omitted, and product-index
rows without a code are dropped).

| Column | Type | Key / constraint | Description |
| --- | --- | --- | --- |
| `codigo` | VARCHAR(6) | PK (with `version_scian`); format checked by level | SCIAN code. Sectors 31-33 and 48-49 are stored as ranges, as INEGI publishes them |
| `version_scian` | TEXT | PK; `SCIAN 2018` or `SCIAN 2023` | Catalogue version |
| `nivel` | TEXT | `sector`, `subsector`, `rama`, `clase` | Hierarchy level |
| `descripcion` | TEXT | NOT NULL | Category title (whitespace collapsed; trailing `T` marker removed) |
| `es_retail` | BOOLEAN | NOT NULL | `true` if the code is in sector 46 (retail trade) |
| `es_servicio` | BOOLEAN | NOT NULL | `true` if the code is in sectors 51 to 81 (services) |

---

## `silver.crosswalk_municipio_nombre`

Name → key lookup across sources, used **only to check** that each source's name and code
point to the same municipality. **Grain:** one distinct name as written in any source.
**Rows:** 106.

| Column | Type | Key / constraint | Description |
| --- | --- | --- | --- |
| `nombre_fuente` | TEXT | PK | Name exactly as it appears in the source(s) |
| `nombre_normalizado` | TEXT | NOT NULL | Lower-case, accent-free, single-spaced name |
| `cvegeo` | CHAR(5) | FK → `municipios` | Municipality the name maps to (taken from the code in the same source) |

`fuente` and `version_fuente` list every source where the name appears, comma-separated.

---

## `silver.denue_establecimientos`

Georeferenced establishments, all DENUE editions. **Grain:** establishment × edition (the
same establishment appears once per edition). **Rows:** 809,056. **From:** `bronze.denue`.

Transformations: duplicates on (`id`, `edicion`) removed; rows without coordinates dropped;
points built from lat/lon in EPSG:6365 and reprojected to EPSG:6372; municipality assigned
by spatial join (`intersects`), with ties resolved in favour of the source `cve_mun` (else
lowest `cvegeo`); points outside every polygon reassigned to the nearest municipality if
≤ 30 m away, otherwise dropped. Counts are logged in `etl_log`.

| Column | Type | Key / constraint | Description |
| --- | --- | --- | --- |
| `id_denue` | BIGINT | PK (with `edicion`) | DENUE establishment ID |
| `edicion` | CHAR(7) | PK; `YYYY_MM` | DENUE edition (`2020_11` … `2025_05`) |
| `nombre_establecimiento` | TEXT | | Establishment name |
| `codigo_act` | CHAR(6) | FK (with `version_scian`) → `cat_scian` | SCIAN class code |
| `version_scian` | TEXT | NOT NULL | SCIAN version of the edition |
| `sector_scian` | VARCHAR(5) | 2 digits or `31-33` / `48-49` | Sector derived from the first two digits of `codigo_act` |
| `nombre_act` | TEXT | | SCIAN class name as written in DENUE |
| `per_ocu` | TEXT | | Employee-size band: `0 a 5`, `6 a 10`, `11 a 30`, `31 a 50`, `51 a 100`, `101 a 250`, `251 y más personas` |
| `fecha_alta` | DATE | | Month the establishment entered DENUE; source has only year-month, so day = 1 |
| `latitud` | DOUBLE PRECISION | NOT NULL | Source latitude (EPSG:6365) |
| `longitud` | DOUBLE PRECISION | NOT NULL | Source longitude (EPSG:6365) |
| `geom` | geometry(Point, 6372) | NOT NULL, GiST index | Reprojected point |
| `cve_mun_original` | CHAR(3) | | Municipal code as reported by DENUE |
| `cvegeo` | CHAR(5) | NOT NULL, FK → `municipios` | Municipality **assigned by the spatial join** (replaces the source code) |
| `coincide_municipio` | BOOLEAN | | `true` if `cvegeo` = `'31' \|\| cve_mun_original` |

---

## `silver.incidencia_delictiva`

Reported crime incidents. **Grain:** municipality × month × crime (legal good, type,
subtype, modality). **Rows:** 1,371,216 (2015–2025). **From:**
`bronze.incidencia_delictiva`, unpivoted from one column per month to one row per month.
Category text is normalized (lower case, no accents, single spaces).

| Column | Type | Key / constraint | Description |
| --- | --- | --- | --- |
| `cvegeo` | CHAR(5) | PK, FK → `municipios` | Municipality (`cve_municipio` from the source) |
| `anio` | SMALLINT | PK | Year |
| `mes` | SMALLINT | PK; 1–12 | Month |
| `bien_juridico` | TEXT | NOT NULL | Legal good affected |
| `tipo_delito` | TEXT | PK | Crime type |
| `subtipo_delito` | TEXT | PK | Crime subtype |
| `modalidad` | TEXT | PK | Modality |
| `incidentes` | INTEGER | NOT NULL, ≥ 0 | Reported incidents in the month (zeros included) |

---

## `silver.etl_log`

Run log of the silver ETL. Never truncated. **Grain:** run × table × metric.

| Column | Type | Description |
| --- | --- | --- |
| `id` | BIGSERIAL (PK) | Row ID |
| `run_id` | UUID | Silver run ID |
| `tabla` | TEXT | Silver table the metric refers to |
| `metrica` | TEXT | Metric name (see below) |
| `valor` | BIGINT | Metric value |
| `fecha_carga` | TIMESTAMPTZ | Run timestamp |

Metrics: `filas_cargadas` (every table); `geometrias_reparadas`,
`partes_arrecife_alacranes_eliminadas` (`municipios`); `registros_bronze`,
`duplicados_eliminados`, `descartados_coordenada_nula`, `puntos_en_limite_entre_municipios`,
`reasignados_mas_cercano`, `descartados_fuera_umbral` (`denue_establecimientos`).
