# Warehouse model (gold layer)

Star schema of `yucatan_dw.gold`, loaded by `src/silver_to_gold.py` (`sql/gold/gold.sql`).
DDL in `sql/init.sql`. Static image: [`warehouse_model.png`](warehouse_model.png)
(source: [`warehouse_model.dot`](warehouse_model.dot)). Column details:
[`data_dictionary_gold.md`](data_dictionary_gold.md).

```mermaid
erDiagram
    dim_municipio ||--|| fact_censo : "cvegeo"
    dim_municipio ||--o{ fact_establecimiento : "cvegeo"
    dim_municipio ||--o{ fact_incidencia : "cvegeo"
    dim_tiempo ||--o{ fact_establecimiento : "id_tiempo (edition month)"
    dim_tiempo ||--o{ fact_incidencia : "id_tiempo"
    dim_actividad ||--o{ fact_establecimiento : "id_actividad"
    dim_delito ||--o{ fact_incidencia : "id_delito"
    dim_municipio ||--o{ vecinos_municipio : "cvegeo"
    dim_municipio ||--o{ vecinos_municipio : "cvegeo_vecino"

    dim_municipio {
        CHAR5 cvegeo PK
        TEXT nombre
        DOUBLE area_km2
        MULTIPOLYGON_6372 geom
    }
    dim_tiempo {
        INTEGER id_tiempo PK "YYYYMM"
        SMALLINT anio
        SMALLINT mes
        SMALLINT trimestre
        TEXT nombre_mes
    }
    dim_actividad {
        SERIAL id_actividad PK
        CHAR6 codigo_clase UK
        TEXT desc_clase
        TEXT version_scian UK
        CHAR4 codigo_rama
        TEXT desc_rama
        CHAR3 codigo_subsector
        TEXT desc_subsector
        VARCHAR5 codigo_sector
        TEXT desc_sector
        BOOLEAN es_retail
        BOOLEAN es_servicio
    }
    dim_delito {
        SERIAL id_delito PK
        TEXT bien_juridico UK
        TEXT tipo_delito UK
        TEXT subtipo_delito UK
        TEXT modalidad UK
    }
    fact_censo {
        CHAR5 cvegeo PK,FK
        INTEGER pobtot
        INTEGER p_12ymas
        INTEGER pea
        INTEGER pe_inac
        INTEGER pob0_14
        INTEGER pob15_64
        INTEGER pob65_mas
        INTEGER vivtot
        INTEGER tvivhab
        TEXT fuente "lineage"
        TEXT version_fuente "lineage"
        TIMESTAMPTZ fecha_carga "lineage"
        UUID run_id "lineage"
    }
    fact_establecimiento {
        BIGINT id_denue PK
        CHAR7 edicion PK
        CHAR5 cvegeo FK
        INTEGER id_actividad FK
        INTEGER id_tiempo FK
        TEXT per_ocu
        POINT_6372 geom
        TEXT fuente "lineage"
        TEXT version_fuente "lineage"
        TIMESTAMPTZ fecha_carga "lineage"
        UUID run_id "lineage"
    }
    fact_incidencia {
        CHAR5 cvegeo PK,FK
        INTEGER id_tiempo PK,FK
        INTEGER id_delito PK,FK
        INTEGER incidentes
        TEXT fuente "lineage"
        TEXT version_fuente "lineage"
        TIMESTAMPTZ fecha_carga "lineage"
        UUID run_id "lineage"
    }
    vecinos_municipio {
        CHAR5 cvegeo PK,FK
        CHAR5 cvegeo_vecino PK,FK
    }
```

## Grain

| Table | Type | Grain |
| --- | --- | --- |
| `dim_municipio` | dimension | one municipality (106) |
| `dim_tiempo` | dimension | year-month, 2020–2025 (72) |
| `dim_actividad` | dimension | SCIAN class × SCIAN version (2018, 2023) |
| `dim_delito` | dimension | legal good × crime type × subtype × modality |
| `fact_censo` | fact | municipality (Census 2020) |
| `fact_establecimiento` | fact (periodic snapshot) | establishment × DENUE edition — **do not sum across editions** |
| `fact_incidencia` | fact | municipality × month × crime |
| `vecinos_municipio` | bridge | pair of contiguous municipalities (Queen), stored in both directions |

The views `kpi_municipio_anio` and `cociente_localizacion` and the `resultado_*` tables
are derived from this model and are documented in the gold data dictionary.
