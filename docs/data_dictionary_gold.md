# Data dictionary — gold layer

Dimensional model (star schema), KPI views and spatial-analysis results. Model diagram:
[`warehouse_model.md`](warehouse_model.md) / [`warehouse_model.png`](warehouse_model.png).
Other layers: [bronze](data_dictionary_bronze.md) · [silver](data_dictionary_silver.md).

| Object | Loaded by | Defined in |
| --- | --- | --- |
| Dimensions, facts, `vecinos_municipio` | `src/silver_to_gold.py` → `sql/gold/gold.sql` | `sql/init.sql` |
| Views `kpi_municipio_anio`, `cociente_localizacion` | `sql/gold/gold.sql` | `sql/gold/gold.sql` |
| Tables `resultado_*` | `src/gold_analytics.py` | `sql/init.sql` |

Panel period: **2020–2025**. Geometries in EPSG:6372.

---

## Dimensions

### `gold.dim_municipio`

**Grain:** one municipality. **Rows:** 106. **From:** `silver.municipios`.

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `cvegeo` | CHAR(5) | PK | Geostatistical key (`'31'` + municipal code) |
| `nombre` | TEXT | | Municipality name |
| `area_km2` | DOUBLE PRECISION | | Area in km² (denominator of all densities; Progreso without Arrecife Alacranes) |
| `geom` | geometry(MultiPolygon, 6372) | GiST index | Municipal boundary |

### `gold.dim_tiempo`

**Grain:** year-month. **Rows:** 72 (Jan 2020 – Dec 2025), generated in SQL.

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `id_tiempo` | INTEGER | PK; `= anio * 100 + mes` | Smart key `YYYYMM` (e.g. `202011`) |
| `anio` | SMALLINT | | Year |
| `mes` | SMALLINT | 1–12 | Month |
| `trimestre` | SMALLINT | 1–4 | Quarter |
| `nombre_mes` | TEXT | | Month name in Spanish (`enero` … `diciembre`) |

### `gold.dim_actividad`

**Grain:** SCIAN class × SCIAN version. **Rows:** 2,170. **From:** `silver.cat_scian`, with
the hierarchy flattened by code prefix (rama = 4 digits, subsector = 3, sector = 2 or range).

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `id_actividad` | SERIAL | PK | Surrogate key |
| `codigo_clase` | CHAR(6) | UNIQUE (with `version_scian`) | SCIAN class code |
| `desc_clase` | TEXT | | Class title |
| `version_scian` | TEXT | UNIQUE (with `codigo_clase`) | `SCIAN 2018` or `SCIAN 2023` |
| `codigo_rama` / `desc_rama` | CHAR(4) / TEXT | | Industry group (rama) |
| `codigo_subsector` / `desc_subsector` | CHAR(3) / TEXT | | Subsector |
| `codigo_sector` / `desc_sector` | VARCHAR(5) / TEXT | | Sector (`31-33` and `48-49` as ranges) |
| `es_retail` | BOOLEAN | | Sector 46, retail trade |
| `es_servicio` | BOOLEAN | | Sectors 51 to 81, services |

### `gold.dim_delito`

**Grain:** legal good × crime type × subtype × modality. **Rows:** 98 (40 crime types).
**From:** distinct categories in `silver.incidencia_delictiva` for 2020–2025.

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `id_delito` | SERIAL | PK | Surrogate key |
| `bien_juridico` | TEXT | UNIQUE (all four) | Legal good affected (e.g. `el patrimonio`) |
| `tipo_delito` | TEXT | | Crime type |
| `subtipo_delito` | TEXT | | Crime subtype |
| `modalidad` | TEXT | | Modality |

Text is normalized (lower case, no accents).

---

## Facts

### `gold.fact_censo`

**Grain:** municipality (Census 2020, single point in time). **Rows:** 106.
**From:** `silver.censo_municipal`. Relationship 1:1 with `dim_municipio`.

| Column | Type | Key | Description | Additive |
| --- | --- | --- | --- | --- |
| `cvegeo` | CHAR(5) | PK, FK → `dim_municipio` | Municipality | |
| `pobtot` | INTEGER | | Total population | yes |
| `p_12ymas` | INTEGER | | Population aged 12+ (base of the EAP rate) | yes |
| `pea` | INTEGER | | Economically active population (12+) | yes |
| `pe_inac` | INTEGER | | Economically inactive population (12+) | yes |
| `pob0_14` | INTEGER | | Population aged 0–14 | yes |
| `pob15_64` | INTEGER | | Population aged 15–64 | yes |
| `pob65_mas` | INTEGER | | Population aged 65+ | yes |
| `vivtot` | INTEGER | | Total dwellings | yes |
| `tvivhab` | INTEGER | | Inhabited dwellings | yes |
| `fuente` | TEXT | | Source name, copied from silver | |
| `version_fuente` | TEXT | | Source version, copied from silver | |
| `fecha_carga` | TIMESTAMPTZ | | Time of the gold run that loaded the row (UTC) | |
| `run_id` | UUID | | Gold run that loaded the row (joins with `gold.etl_log`) | |

`NULL` = confidential or unavailable in the source.

### `gold.fact_establecimiento`

**Grain:** establishment × DENUE edition (periodic snapshot). **Rows:** 809,056.
**From:** `silver.denue_establecimientos`.

> **Do not sum across editions.** The same establishment appears once per edition; always
> filter a single `edicion` (the views use one edition per year).

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `id_denue` | BIGINT | PK | DENUE establishment ID |
| `edicion` | CHAR(7) | PK | Edition (`2020_11`, `2021_11`, `2022_11`, `2023_11`, `2024_11`, `2025_05`) |
| `cvegeo` | CHAR(5) | FK → `dim_municipio`, index | Municipality assigned by spatial join |
| `id_actividad` | INTEGER | FK → `dim_actividad` | SCIAN class of the edition's SCIAN version |
| `id_tiempo` | INTEGER | FK → `dim_tiempo` | Month of the edition (`'2020_11'` → `202011`) |
| `per_ocu` | TEXT | | Employee-size band (degenerate attribute) |
| `geom` | geometry(Point, 6372) | GiST index | Establishment location |
| `fuente` | TEXT | | Source name, copied from silver |
| `version_fuente` | TEXT | | Source version / edition, copied from silver |
| `fecha_carga` | TIMESTAMPTZ | | Time of the gold run that loaded the row (UTC) |
| `run_id` | UUID | | Gold run that loaded the row (joins with `gold.etl_log`) |

Measure: row count (one establishment).

### `gold.fact_incidencia`

**Grain:** municipality × month × crime. **Rows:** 747,936 (2020–2025; includes months with
zero incidents). **From:** `silver.incidencia_delictiva`.

| Column | Type | Key | Description | Additive |
| --- | --- | --- | --- | --- |
| `cvegeo` | CHAR(5) | PK, FK → `dim_municipio` | Municipality | |
| `id_tiempo` | INTEGER | PK, FK → `dim_tiempo` | Month | |
| `id_delito` | INTEGER | PK, FK → `dim_delito` | Crime category | |
| `incidentes` | INTEGER | ≥ 0 | Reported incidents (SESNSP, common jurisdiction) | yes, across all dimensions |
| `fuente` | TEXT | | Source name, copied from silver | |
| `version_fuente` | TEXT | | Source version, copied from silver | |
| `fecha_carga` | TIMESTAMPTZ | | Time of the gold run that loaded the row (UTC) | |
| `run_id` | UUID | | Gold run that loaded the row (joins with `gold.etl_log`) | |

---

## Bridge

### `gold.vecinos_municipio`

**Grain:** ordered pair of contiguous municipalities. **Rows:** 576 (288 pairs × 2
directions). Built with `ST_Intersects` on `dim_municipio.geom` (Queen contiguity; see the
README for why `ST_Touches` is not used). Source of the spatial weights in
`gold_analytics.py`.

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `cvegeo` | CHAR(5) | PK, FK → `dim_municipio` | Municipality |
| `cvegeo_vecino` | CHAR(5) | PK, FK → `dim_municipio`; `≠ cvegeo` | Neighbouring municipality |

---

## Views

### `gold.kpi_municipio_anio`

**Grain:** municipality × year (2020–2025). **Rows:** 636 (106 × 6).

Rules: population is Census 2020 for every year; businesses come from the November edition
of each year, except **2025, which uses 2025_05**; crime is the sum of the 12 months of the
year.

| Column | Type | KPI | Formula |
| --- | --- | --- | --- |
| `anio` | INTEGER | — | Panel year |
| `cvegeo` | CHAR(5) | — | Municipality |
| `nombre` | TEXT | — | Municipality name |
| `poblacion_total` | INTEGER | Total population | `pobtot` |
| `densidad_poblacional` | DOUBLE | Population density (inhab/km²) | `pobtot / area_km2` |
| `tasa_pea` | DOUBLE | Economically active population rate (0–1) | `pea / p_12ymas` |
| `prop_0_14` | DOUBLE | Population by age group (share) | `pob0_14 / pobtot` |
| `prop_15_64` | DOUBLE | Population by age group (share) | `pob15_64 / pobtot` |
| `prop_65_mas` | DOUBLE | Population by age group (share) | `pob65_mas / pobtot` |
| `total_negocios` | BIGINT | Total businesses | establishments in the year's edition |
| `densidad_negocios` | DOUBLE | Business density (per km²) | `total_negocios / area_km2` |
| `negocios_por_1000_hab` | NUMERIC | Businesses per 1,000 residents | `1000 × total_negocios / pobtot` |
| `densidad_retail` | DOUBLE | Retail density (per km²) | establishments with `es_retail` / `area_km2` |
| `densidad_servicios` | DOUBLE | Service density (per km²) | establishments with `es_servicio` / `area_km2` |
| `sector_dominante` | VARCHAR | Dominant economic activity (code) | sector with most establishments; tie → lowest sector code |
| `desc_sector_dominante` | TEXT | Dominant economic activity (name) | title of `sector_dominante` |
| `total_incidentes` | BIGINT | Total crime incidents | `sum(incidentes)` over the year |
| `tasa_delictiva_1000_hab` | NUMERIC | Crime rate per 1,000 residents | `1000 × total_incidentes / pobtot` |
| `incidentes_por_100_negocios` | NUMERIC | Crime relative to business activity | `100 × total_incidentes / total_negocios` |
| `geom` | geometry(MultiPolygon, 6372) | — | Municipal boundary (for mapping) |

The KPI *Incidents by type and time* is not a column: it is computed directly from
`fact_incidencia` × `dim_delito` × `dim_tiempo`.

### `gold.cociente_localizacion`

**Grain:** municipality × year × SCIAN sector (only sectors present in the municipality).
**Rows:** 9,407. Additional KPI (location quotient), same edition-per-year rule.

| Column | Type | Description |
| --- | --- | --- |
| `anio` | INTEGER | Panel year |
| `cvegeo` | CHAR(5) | Municipality |
| `codigo_sector` | VARCHAR | SCIAN sector |
| `desc_sector` | TEXT | Sector title |
| `negocios_sector` | BIGINT | Establishments of the sector in the municipality |
| `negocios_municipio` | NUMERIC | All establishments in the municipality |
| `negocios_sector_estado` | NUMERIC | Establishments of the sector in the state |
| `negocios_estado` | NUMERIC | All establishments in the state |
| `lq` | DOUBLE | `(negocios_sector / negocios_municipio) / (negocios_sector_estado / negocios_estado)`; > 1 = sector over-represented in the municipality |

---

## Analysis results

Written by `src/gold_analytics.py` from `kpi_municipio_anio` and `vecinos_municipio`, for
each year 2020–2025. Queen weights, row-standardized; 999 permutations with fixed seed (42);
significance α = 0.05. Variable / indicator names are column names of
`kpi_municipio_anio`.

### `gold.resultado_correlacion`

**Grain:** year × variable pair × method. **Rows:** 36 (6 years × 3 pairs × 2 methods).

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `anio` | SMALLINT | PK | Year |
| `variable_x` | TEXT | PK | First variable |
| `variable_y` | TEXT | PK | Second variable |
| `metodo` | TEXT | PK; `pearson` / `spearman` | Correlation method |
| `coeficiente` | DOUBLE | | Correlation coefficient |
| `p_value` | DOUBLE | | Two-sided p-value |
| `n` | INTEGER | | Municipalities with both values non-null |

Pairs: `tasa_delictiva_1000_hab` vs `negocios_por_1000_hab`; `densidad_poblacional` vs
`densidad_negocios`; `tasa_pea` vs `tasa_delictiva_1000_hab`.

### `gold.resultado_moran_global`

**Grain:** year × indicator. **Rows:** 12. Indicators: `tasa_delictiva_1000_hab`,
`densidad_negocios`.

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `anio` | SMALLINT | PK | Year |
| `indicador` | TEXT | PK | Indicator |
| `i` | DOUBLE | | Global Moran's I |
| `esperanza_i` | DOUBLE | | Expected I under no autocorrelation (−1 / (n − 1)) |
| `z` | DOUBLE | | z-score from the permutation distribution (`z_sim`) |
| `p_sim` | DOUBLE | | Pseudo p-value from permutations |

### `gold.resultado_lisa`

**Grain:** year × indicator × municipality. **Rows:** 1,272 (6 × 2 × 106). Same indicators
as global Moran.

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `anio` | SMALLINT | PK | Year |
| `indicador` | TEXT | PK | Indicator |
| `cvegeo` | CHAR(5) | PK | Municipality |
| `ii` | DOUBLE | | Local Moran's I |
| `p_sim` | DOUBLE | | Pseudo p-value from permutations |
| `cuadrante` | TEXT | `HH`, `LL`, `HL`, `LH`, `no significativo` | Cluster / outlier type; quadrant assigned only if `p_sim < 0.05` |

### `gold.resultado_moran_bivariado`

**Grain:** year × variable pair. **Rows:** 6. Pair: `tasa_delictiva_1000_hab` (x) vs spatial
lag of `densidad_negocios` (y).

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| `anio` | SMALLINT | PK | Year |
| `variable_x` | TEXT | PK | Variable at the location |
| `variable_y` | TEXT | PK | Variable whose spatial lag is used |
| `i` | DOUBLE | | Bivariate Moran's I |
| `z` | DOUBLE | | z-score from permutations (`z_sim`) |
| `p_sim` | DOUBLE | | Pseudo p-value from permutations |

---

## `gold.etl_log`

Run log of `silver_to_gold.py` and `gold_analytics.py`. Never truncated. Same columns as
`silver.etl_log` (`id`, `run_id`, `tabla`, `metrica`, `valor`, `fecha_carga`); the only
metric is `filas_cargadas` per gold table or view.
