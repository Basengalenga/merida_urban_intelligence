# Data dictionary — bronze layer

Raw copy of each source, loaded by `src/extractor_to_bronze.py`. Other layers:
[silver](data_dictionary_silver.md) · [gold](data_dictionary_gold.md).

**Bronze rules**

- Every source column is stored as `TEXT`, exactly as it comes (no cleaning, no type
  casting). The only filter is state `31` (Yucatán) in the crime file, which covers all of
  Mexico.
- Column names are normalized to snake_case without accents
  (`Bien jurídico afectado` → `bien_juridico_afectado`).
- Each run replaces the whole table (inside a transaction).
- Tables are created by the extractor (`to_sql` / `to_postgis`), not by `sql/init.sql`.

**Metadata columns (all bronze tables)**

| Column | Type | Description |
| --- | --- | --- |
| `_ingested_at` | TIMESTAMPTZ | Start of the batch (UTC); the same for all tables of one run. In Airflow, the DAG run's `logical_date` |
| `_source_url` | TEXT | Exact URL the file was downloaded from |
| `_run_id` | TEXT | UUID of the run; shared by the 5 extractors of one Airflow DAG run |

This dictionary details the columns that the silver layer reads. The remaining source
columns are listed by name; their official definitions are in the data descriptor that
each source publishes with the download.

---

## `bronze.incidencia_delictiva`

| | |
| --- | --- |
| Source | SESNSP, municipal crime incidence, common jurisdiction (`IDM_NM_dic25.csv`) |
| URL | `https://repodatos.atdt.gob.mx/api_update/sesnsp/incidencia_delictiva/IDM_NM_dic25.csv` |
| Grain | municipality × year × legal good × crime type × subtype × modality, one column per month |
| Rows | 114,268 (years 2015–2025) |
| Filter | `clave_ent = '31'` |

| Column | Description | Used in silver |
| --- | --- | --- |
| `ano` | Year | ✔ → `anio` |
| `clave_ent` | State code (`31`) | filter only |
| `entidad` | State name | |
| `cve_municipio` | 5-digit municipal code (state + municipality), equal to INEGI `cvegeo` | ✔ → `cvegeo` |
| `municipio` | Municipality name | ✔ checked against the crosswalk |
| `bien_juridico_afectado` | Legal good affected (top level of the crime classification) | ✔ |
| `tipo_de_delito` | Crime type | ✔ |
| `subtipo_de_delito` | Crime subtype | ✔ |
| `modalidad` | Modality (e.g. with/without violence) | ✔ |
| `enero` … `diciembre` | Number of reported incidents in each month (12 columns) | ✔ unpivoted to `mes` + `incidentes` |

---

## `bronze.iter_2020`

| | |
| --- | --- |
| Source | INEGI, Population and Housing Census 2020, *Principales resultados por localidad* (ITER), Yucatán |
| URL | `https://www.inegi.org.mx/contenidos/programas/ccpv/2020/datosabiertos/iter/iter_31_cpv2020_csv.zip` |
| Grain | locality, plus total rows: state (`mun = '000'`), municipality (`loc = '0000'`), and grouped small localities (`loc = '9998'`, `'9999'`) |
| Rows | 2,691 (289 columns) |

Columns used by silver (only rows with `loc = '0000'`):

| Column | Description | Used in silver |
| --- | --- | --- |
| `entidad` | State code (`31`) | filter |
| `mun` | 3-digit municipal code (`000` = state total) | ✔ → `cvegeo` |
| `nom_mun` | Municipality name | ✔ crosswalk |
| `loc` | 4-digit locality code (`0000` = municipal total) | filter |
| `pobtot` | Total population | ✔ |
| `p_12ymas` | Population aged 12 and over | ✔ |
| `pea` | Economically active population (aged 12 and over) | ✔ |
| `pe_inac` | Economically inactive population (aged 12 and over) | ✔ |
| `pob0_14` | Population aged 0–14 | ✔ |
| `pob15_64` | Population aged 15–64 | ✔ |
| `pob65_mas` | Population aged 65 and over | ✔ |
| `vivtot` | Total dwellings | ✔ |
| `tvivhab` | Total inhabited dwellings | ✔ |

Values `*` (confidential) and `N/D` (not available) are kept as text in bronze.

Other columns (not used), by group:

- **Identification and location:** `nom_ent`, `nom_loc`, `longitud`, `latitud`, `altitud`, `tamloc`.
- **Population by sex and age:** `pobfem`, `pobmas`, `p_0a2` … `p_85ymas` and their `_f` / `_m` variants, `p_60ymas*`, `p_15a49_f`, `rel_h_m`, `prom_hnv`.
- **Migration:** `pnacent*`, `pnacoe*`, `pres2015*`, `presoe15*`.
- **Indigenous language and Afro-descendant population:** `p3ym_hli*`, `p3hlinhe*`, `p3hli_he*`, `p5_hli*`, `phog_ind`, `pob_afro*`.
- **Disability and limitation:** `pcon_disc`, `pcdisc_*`, `pcon_limi`, `pclim_*`, `psind_lim`.
- **Education:** `p3a5_noa*`, `p6a11_noa*`, `p12a14noa*`, `p15a17a*`, `p18a24a*`, `p8a14an*`, `p15ym_an*`, `p15ym_se*`, `p15pri_*`, `p15sec_*`, `p18ym_pb*`, `graproes*`.
- **Economic activity (sex breakdown):** `pea_f`, `pea_m`, `pe_inac_f`, `pe_inac_m`, `pocupada*`, `pdesocup*`.
- **Health services:** `psinder`, `pder_*`, `pafil_*`.
- **Marital status and religion:** `p12ym_solt`, `p12ym_casa`, `p12ym_sepa`, `pcatolica`, `pro_crieva`, `potras_rel`, `psin_relig`.
- **Households:** `tothog`, `hogjef_*`, `pobhog`, `phogjef_*`.
- **Housing:** `tvivpar`, `vivpar_hab`, `vivparh_cv`, `tvivparhab`, `vivpar_des`, `vivpar_ut`, `ocupvivpar`, `prom_ocup`, `pro_ocup_c`, and the 40 `vph_*` dwelling-characteristic columns (floor, rooms, electricity, water, drainage, appliances, ICT).

---

## `bronze.denue`

| | |
| --- | --- |
| Source | INEGI, National Statistical Directory of Economic Units (DENUE), Yucatán |
| URLs | `https://www.inegi.org.mx/contenidos/masiva/denue/<edition>/denue_31_<MMYY>_csv.zip` for editions 2020_11, 2021_11, 2022_11, 2023_11, 2024_11 and 2025_05 |
| Grain | establishment × edition (all editions stacked in one table) |
| Rows | 809,101 (48 columns) |

| Column | Description | Used in silver |
| --- | --- | --- |
| `id` | DENUE establishment identifier | ✔ → `id_denue` |
| `nom_estab` | Establishment name | ✔ |
| `codigo_act` | 6-digit SCIAN class code | ✔ |
| `nombre_act` | SCIAN class name | ✔ |
| `per_ocu` | Employee-size band (e.g. `0 a 5 personas`) | ✔ |
| `cve_mun` | 3-digit municipal code as reported by DENUE | ✔ → `cve_mun_original` |
| `municipio` | Municipality name | ✔ crosswalk |
| `latitud` | Latitude (decimal degrees, ITRF2008 / EPSG:6365) | ✔ |
| `longitud` | Longitude (decimal degrees, ITRF2008 / EPSG:6365) | ✔ |
| `fecha_alta` | Month the establishment was added to DENUE (`YYYY-MM`, some rows `YYYY MM`) | ✔ |
| `edicion` | **Added by the extractor.** Edition (`2020_11` … `2025_05`) | ✔ |
| `anio` | **Added by the extractor.** Year of the edition | |
| `scian` | **Added by the extractor.** SCIAN version of the edition: `SCIAN 2018` (2020_11–2023_11) or `SCIAN 2023` (2024_11 onward) | ✔ → `version_scian` |

Other columns (not used):

- **Business identity:** `raz_social` (legal name), `clee` (economic-unit key; absent in edition 2020_11, so NULL there), `tipounieco` (`Fijo` / `Semifijo`).
- **Address:** `tipo_vial`, `nom_vial`, `tipo_v_e_1` … `nom_v_e_3`, `numero_ext`, `letra_ext`, `edificio`, `edificio_e`, `numero_int`, `letra_int`, `tipo_asent`, `nomb_asent`, `tipocencom`, `nom_cencom`, `num_local`, `cod_postal`.
- **Geographic codes:** `cve_ent`, `entidad`, `cve_loc`, `localidad`, `ageb`, `manzana`.
- **Contact:** `telefono`, `correoelec`, `www`.

If an edition lacks a column that another edition has, it is NULL for that edition.

---

## `bronze.marco_geo_municipal`

| | |
| --- | --- |
| Source | INEGI Marco Geoestadístico 2020, municipal layer (`*mun.shp` inside the zip) |
| URL | `https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/geografia/marcogeo/889463807469/31_yucatan.zip` |
| Grain | municipality (polygon) |
| Rows | 106 |
| CRS | EPSG:6372 (Mexico ITRF2008 / LCC). The `.prj` names it `MEXICO_ITRF_2008_LCC`; the extractor tags the SRID as 6372 without moving coordinates |

| Column | Type | Description | Used in silver |
| --- | --- | --- | --- |
| `cvegeo` | TEXT | 5-digit geostatistical key (`cve_ent` + `cve_mun`) | ✔ validated |
| `cve_ent` | TEXT | State code (`31`) | ✔ |
| `cve_mun` | TEXT | 3-digit municipal code | ✔ |
| `nomgeo` | TEXT | Municipality name | ✔ → `nombre_municipio` |
| `geometry` | geometry (Polygon / MultiPolygon, 6372) | Municipal boundary (103 Polygon, 3 MultiPolygon) | ✔ |

---

## `bronze.scian`

| | |
| --- | --- |
| Source | INEGI, North American Industry Classification System (SCIAN) México 2018 and 2023, *categorías y productos* workbook |
| URLs | `https://www.inegi.org.mx/contenidos/app/scian/scian_2018_categorias_y_productos.xlsx`, `…/scian_2023_categorias_y_productos.xlsx` |
| Grain | code × level (sheet) × version |
| Rows | 73,499 |

| Column | Description | Used in silver |
| --- | --- | --- |
| `codigo` | SCIAN code (2 digits sector, or range `31-33` / `48-49`; 3 subsector; 4 rama; 5 subrama; 6 clase). NULL on product-index rows of the CLASE sheet | ✔ |
| `titulo` | Category title | ✔ → `descripcion` |
| `descripcion` | Long description of the category | |
| `incluye` | What the category includes | |
| `excluye` | What the category excludes | |
| `productos` | Products (CLASE sheet) | |
| `indice_de_bienes_y_servicios_comprendidos_en_las_categorias_del` | Index of goods and services (CLASE sheet) | |
| `hoja` | **Added by the extractor.** Level / sheet name: `SECTOR`, `SUBSECTOR`, `RAMA`, `SUBRAMA`, `CLASE` | ✔ → `nivel` |
| `scian` | **Added by the extractor.** Version: `SCIAN 2018` or `SCIAN 2023` | ✔ → `version_scian` |

Fully empty spreadsheet rows are dropped; product-index rows without a code are kept
(silver filters them).
