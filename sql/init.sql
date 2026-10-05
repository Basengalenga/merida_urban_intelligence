CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- ==========================================================================
-- Capa silver: datos limpios y estandarizados, uno a uno por fuente.
-- Solo estructura; la carga la hace dags/bronze_to_silver.py.
-- Llave de integración: cvegeo CHAR(5) = '31' + cve_mun (3 dígitos).
-- Geometrías en EPSG:6372. Áreas en km².
-- ==========================================================================

-- 1. Municipios (INEGI Marco Geoestadístico)
CREATE TABLE IF NOT EXISTS silver.municipios (
    cvegeo            CHAR(5)                         PRIMARY KEY,
    cve_ent           CHAR(2)                         NOT NULL CHECK (cve_ent = '31'),
    cve_mun           CHAR(3)                         NOT NULL CHECK (cve_mun ~ '^[0-9]{3}$'),
    nombre_municipio  TEXT                            NOT NULL,
    geom              geometry(MultiPolygon, 6372)    NOT NULL CHECK (ST_IsValid(geom)),
    area_km2          DOUBLE PRECISION                NOT NULL CHECK (area_km2 > 0),
    fuente            TEXT                            NOT NULL,
    version_fuente    TEXT                            NOT NULL,
    fecha_carga       TIMESTAMPTZ                     NOT NULL,
    CHECK (cvegeo = cve_ent || cve_mun)
);
CREATE INDEX IF NOT EXISTS municipios_geom_gist ON silver.municipios USING GIST (geom);

-- 2. Censo 2020, ITER, solo totales municipales (LOC = 0000).
--    NULL = valor confidencial o no disponible en la fuente ('*', 'N/D').
CREATE TABLE IF NOT EXISTS silver.censo_municipal (
    cvegeo          CHAR(5)       PRIMARY KEY REFERENCES silver.municipios (cvegeo),
    pobtot          INTEGER       CHECK (pobtot >= 0),
    p_12ymas        INTEGER       CHECK (p_12ymas >= 0),
    pea             INTEGER       CHECK (pea >= 0),
    pe_inac         INTEGER       CHECK (pe_inac >= 0),
    pob0_14         INTEGER       CHECK (pob0_14 >= 0),
    pob15_64        INTEGER       CHECK (pob15_64 >= 0),
    pob65_mas       INTEGER       CHECK (pob65_mas >= 0),
    vivtot          INTEGER       CHECK (vivtot >= 0),
    tvivhab         INTEGER       CHECK (tvivhab >= 0),
    fuente          TEXT          NOT NULL,
    version_fuente  TEXT          NOT NULL,
    fecha_carga     TIMESTAMPTZ   NOT NULL
);

-- 6. Catálogo SCIAN (INEGI), versiones 2018 y 2023, 4 niveles (sin subrama).
--    Los sectores 31-33 y 48-49 se guardan como rango, igual que en INEGI.
CREATE TABLE IF NOT EXISTS silver.cat_scian (
    codigo          VARCHAR(6)    NOT NULL,
    version_scian   TEXT          NOT NULL CHECK (version_scian IN ('SCIAN 2018', 'SCIAN 2023')),
    nivel           TEXT          NOT NULL CHECK (nivel IN ('sector', 'subsector', 'rama', 'clase')),
    descripcion     TEXT          NOT NULL,
    es_retail       BOOLEAN       NOT NULL,  -- sector 46
    es_servicio     BOOLEAN       NOT NULL,  -- sectores 51 a 81
    fuente          TEXT          NOT NULL,
    version_fuente  TEXT          NOT NULL,
    fecha_carga     TIMESTAMPTZ   NOT NULL,
    PRIMARY KEY (codigo, version_scian),
    CHECK (
           (nivel = 'sector'    AND codigo ~ '^([0-9]{2}|31-33|48-49)$')
        OR (nivel = 'subsector' AND codigo ~ '^[0-9]{3}$')
        OR (nivel = 'rama'      AND codigo ~ '^[0-9]{4}$')
        OR (nivel = 'clase'     AND codigo ~ '^[0-9]{6}$')
    )
);

-- 3. DENUE, todas las ediciones. Un establecimiento aparece una vez por edición.
CREATE TABLE IF NOT EXISTS silver.denue_establecimientos (
    id_denue                BIGINT                   NOT NULL,
    edicion                 CHAR(7)                  NOT NULL CHECK (edicion ~ '^[0-9]{4}_[0-9]{2}$'),
    nombre_establecimiento  TEXT,
    codigo_act              CHAR(6)                  NOT NULL CHECK (codigo_act ~ '^[0-9]{6}$'),
    version_scian           TEXT                     NOT NULL,
    sector_scian            VARCHAR(5)               NOT NULL CHECK (sector_scian ~ '^([0-9]{2}|31-33|48-49)$'),
    nombre_act              TEXT,
    per_ocu                 TEXT,                    -- estrato de personal ocupado
    fecha_alta              DATE,                    -- el DENUE solo trae año y mes; se usa el día 1
    latitud                 DOUBLE PRECISION         NOT NULL,
    longitud                DOUBLE PRECISION         NOT NULL,
    geom                    geometry(Point, 6372)    NOT NULL,
    cve_mun_original        CHAR(3),                 -- cve_mun tal como lo trae el DENUE
    cvegeo                  CHAR(5)                  NOT NULL REFERENCES silver.municipios (cvegeo),  -- por spatial join
    coincide_municipio      BOOLEAN,
    fuente                  TEXT                     NOT NULL,
    version_fuente          TEXT                     NOT NULL,
    fecha_carga             TIMESTAMPTZ              NOT NULL,
    PRIMARY KEY (id_denue, edicion),
    FOREIGN KEY (codigo_act, version_scian) REFERENCES silver.cat_scian (codigo, version_scian)
);
CREATE INDEX IF NOT EXISTS denue_geom_gist ON silver.denue_establecimientos USING GIST (geom);
CREATE INDEX IF NOT EXISTS denue_cvegeo_idx ON silver.denue_establecimientos (cvegeo);
CREATE INDEX IF NOT EXISTS denue_scian_idx ON silver.denue_establecimientos (codigo_act, version_scian);

-- 5. Crosswalk de nombres de municipio (todas las fuentes) a cvegeo
CREATE TABLE IF NOT EXISTS silver.crosswalk_municipio_nombre (
    nombre_fuente       TEXT          PRIMARY KEY,
    nombre_normalizado  TEXT          NOT NULL,
    cvegeo              CHAR(5)       NOT NULL REFERENCES silver.municipios (cvegeo),
    fuente              TEXT          NOT NULL,  -- fuentes donde aparece el nombre
    version_fuente      TEXT          NOT NULL,
    fecha_carga         TIMESTAMPTZ   NOT NULL
);

-- 4. Incidencia delictiva municipal, fuero común (SESNSP), en formato largo
CREATE TABLE IF NOT EXISTS silver.incidencia_delictiva (
    cvegeo          CHAR(5)       NOT NULL REFERENCES silver.municipios (cvegeo),
    anio            SMALLINT      NOT NULL,
    mes             SMALLINT      NOT NULL CHECK (mes BETWEEN 1 AND 12),
    bien_juridico   TEXT          NOT NULL,
    tipo_delito     TEXT          NOT NULL,
    subtipo_delito  TEXT          NOT NULL,
    modalidad       TEXT          NOT NULL,
    incidentes      INTEGER       NOT NULL CHECK (incidentes >= 0),
    fuente          TEXT          NOT NULL,
    version_fuente  TEXT          NOT NULL,
    fecha_carga     TIMESTAMPTZ   NOT NULL,
    PRIMARY KEY (cvegeo, anio, mes, tipo_delito, subtipo_delito, modalidad)
);

-- Bitácora del ETL de silver (descartes, reasignaciones, conteos). No se trunca.
CREATE TABLE IF NOT EXISTS silver.etl_log (
    id           BIGSERIAL     PRIMARY KEY,
    run_id       UUID          NOT NULL,
    tabla        TEXT          NOT NULL,
    metrica      TEXT          NOT NULL,
    valor        BIGINT        NOT NULL,
    fecha_carga  TIMESTAMPTZ   NOT NULL
);

-- ==========================================================================
-- Capa gold: modelo estrella y resultados del análisis espacial.
-- Solo estructura; la carga la hacen src/silver_to_gold.py (sql/gold/gold.sql)
-- y src/gold_analytics.py (tablas resultado_*). Las vistas viven en sql/gold/gold.sql.
-- ==========================================================================

-- Dimensión municipio
CREATE TABLE IF NOT EXISTS gold.dim_municipio (
    cvegeo    CHAR(5)                         PRIMARY KEY,
    nombre    TEXT                            NOT NULL,
    area_km2  DOUBLE PRECISION                NOT NULL,
    geom      geometry(MultiPolygon, 6372)    NOT NULL
);
CREATE INDEX IF NOT EXISTS dim_municipio_geom_gist ON gold.dim_municipio USING GIST (geom);

-- Dimensión tiempo, grano año-mes. id_tiempo = AAAAMM (p. ej. 202011).
CREATE TABLE IF NOT EXISTS gold.dim_tiempo (
    id_tiempo   INTEGER    PRIMARY KEY,
    anio        SMALLINT   NOT NULL,
    mes         SMALLINT   NOT NULL CHECK (mes BETWEEN 1 AND 12),
    trimestre   SMALLINT   NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    nombre_mes  TEXT       NOT NULL,
    CHECK (id_tiempo = anio * 100 + mes)
);

-- Dimensión actividad: una fila por clase SCIAN + versión, con la jerarquía aplanada.
-- Los sectores 31-33 y 48-49 son rangos, igual que en silver.
CREATE TABLE IF NOT EXISTS gold.dim_actividad (
    id_actividad      SERIAL       PRIMARY KEY,
    codigo_clase      CHAR(6)      NOT NULL,
    desc_clase        TEXT         NOT NULL,
    version_scian     TEXT         NOT NULL,
    codigo_rama       CHAR(4)      NOT NULL,
    desc_rama         TEXT         NOT NULL,
    codigo_subsector  CHAR(3)      NOT NULL,
    desc_subsector    TEXT         NOT NULL,
    codigo_sector     VARCHAR(5)   NOT NULL,
    desc_sector       TEXT         NOT NULL,
    es_retail         BOOLEAN      NOT NULL,  -- sector 46
    es_servicio       BOOLEAN      NOT NULL,  -- sectores 51 a 81
    UNIQUE (codigo_clase, version_scian)
);

-- Dimensión delito
CREATE TABLE IF NOT EXISTS gold.dim_delito (
    id_delito       SERIAL   PRIMARY KEY,
    bien_juridico   TEXT     NOT NULL,
    tipo_delito     TEXT     NOT NULL,
    subtipo_delito  TEXT     NOT NULL,
    modalidad       TEXT     NOT NULL,
    UNIQUE (bien_juridico, tipo_delito, subtipo_delito, modalidad)
);

-- Hecho censo: grano municipio (Censo 2020)
CREATE TABLE IF NOT EXISTS gold.fact_censo (
    cvegeo     CHAR(5)   PRIMARY KEY REFERENCES gold.dim_municipio (cvegeo),
    pobtot     INTEGER,
    p_12ymas   INTEGER,
    pea        INTEGER,
    pe_inac    INTEGER,
    pob0_14    INTEGER,
    pob15_64   INTEGER,
    pob65_mas  INTEGER,
    vivtot     INTEGER,
    tvivhab    INTEGER,
    -- Trazabilidad: fuente y versión copiadas de silver; fecha_carga y run_id de la corrida de gold
    fuente          TEXT          NOT NULL,
    version_fuente  TEXT          NOT NULL,
    fecha_carga     TIMESTAMPTZ   NOT NULL,
    run_id          UUID          NOT NULL
);

-- Hecho establecimiento: grano establecimiento × edición DENUE.
-- Snapshot periódico: NO se suma entre ediciones.
CREATE TABLE IF NOT EXISTS gold.fact_establecimiento (
    id_denue      BIGINT                   NOT NULL,
    edicion       CHAR(7)                  NOT NULL,
    cvegeo        CHAR(5)                  NOT NULL REFERENCES gold.dim_municipio (cvegeo),
    id_actividad  INTEGER                  NOT NULL REFERENCES gold.dim_actividad (id_actividad),
    id_tiempo     INTEGER                  NOT NULL REFERENCES gold.dim_tiempo (id_tiempo),  -- mes de la edición
    per_ocu       TEXT,
    geom          geometry(Point, 6372)    NOT NULL,
    fuente          TEXT          NOT NULL,
    version_fuente  TEXT          NOT NULL,
    fecha_carga     TIMESTAMPTZ   NOT NULL,
    run_id          UUID          NOT NULL,
    PRIMARY KEY (id_denue, edicion)
);
CREATE INDEX IF NOT EXISTS fact_establecimiento_geom_gist ON gold.fact_establecimiento USING GIST (geom);
CREATE INDEX IF NOT EXISTS fact_establecimiento_cvegeo_idx ON gold.fact_establecimiento (cvegeo);

-- Hecho incidencia: grano municipio × mes × delito
CREATE TABLE IF NOT EXISTS gold.fact_incidencia (
    cvegeo      CHAR(5)   NOT NULL REFERENCES gold.dim_municipio (cvegeo),
    id_tiempo   INTEGER   NOT NULL REFERENCES gold.dim_tiempo (id_tiempo),
    id_delito   INTEGER   NOT NULL REFERENCES gold.dim_delito (id_delito),
    incidentes  INTEGER   NOT NULL CHECK (incidentes >= 0),
    fuente          TEXT          NOT NULL,
    version_fuente  TEXT          NOT NULL,
    fecha_carga     TIMESTAMPTZ   NOT NULL,
    run_id          UUID          NOT NULL,
    PRIMARY KEY (cvegeo, id_tiempo, id_delito)
);

-- Pares de municipios contiguos (contigüidad Queen, ST_Intersects; ver sql/gold/gold.sql). Cada par va en ambas direcciones.
CREATE TABLE IF NOT EXISTS gold.vecinos_municipio (
    cvegeo         CHAR(5)   NOT NULL REFERENCES gold.dim_municipio (cvegeo),
    cvegeo_vecino  CHAR(5)   NOT NULL REFERENCES gold.dim_municipio (cvegeo),
    PRIMARY KEY (cvegeo, cvegeo_vecino),
    CHECK (cvegeo <> cvegeo_vecino)
);

-- Resultados del análisis (los llena src/gold_analytics.py)
CREATE TABLE IF NOT EXISTS gold.resultado_correlacion (
    anio         SMALLINT           NOT NULL,
    variable_x   TEXT               NOT NULL,
    variable_y   TEXT               NOT NULL,
    metodo       TEXT               NOT NULL CHECK (metodo IN ('pearson', 'spearman')),
    coeficiente  DOUBLE PRECISION,
    p_value      DOUBLE PRECISION,
    n            INTEGER            NOT NULL,
    PRIMARY KEY (anio, variable_x, variable_y, metodo)
);

CREATE TABLE IF NOT EXISTS gold.resultado_moran_global (
    anio         SMALLINT           NOT NULL,
    indicador    TEXT               NOT NULL,
    i            DOUBLE PRECISION   NOT NULL,
    esperanza_i  DOUBLE PRECISION   NOT NULL,
    z            DOUBLE PRECISION   NOT NULL,  -- z_sim (permutaciones)
    p_sim        DOUBLE PRECISION   NOT NULL,
    PRIMARY KEY (anio, indicador)
);

CREATE TABLE IF NOT EXISTS gold.resultado_lisa (
    anio       SMALLINT           NOT NULL,
    indicador  TEXT               NOT NULL,
    cvegeo     CHAR(5)            NOT NULL,
    ii         DOUBLE PRECISION   NOT NULL,
    p_sim      DOUBLE PRECISION   NOT NULL,
    cuadrante  TEXT               NOT NULL CHECK (cuadrante IN ('HH', 'LL', 'HL', 'LH', 'no significativo')),
    PRIMARY KEY (anio, indicador, cvegeo)
);

CREATE TABLE IF NOT EXISTS gold.resultado_moran_bivariado (
    anio        SMALLINT           NOT NULL,
    variable_x  TEXT               NOT NULL,
    variable_y  TEXT               NOT NULL,
    i           DOUBLE PRECISION   NOT NULL,
    z           DOUBLE PRECISION   NOT NULL,  -- z_sim (permutaciones)
    p_sim       DOUBLE PRECISION   NOT NULL,
    PRIMARY KEY (anio, variable_x, variable_y)
);

-- Bitácora del ETL de gold (conteos). No se trunca.
CREATE TABLE IF NOT EXISTS gold.etl_log (
    id           BIGSERIAL     PRIMARY KEY,
    run_id       UUID          NOT NULL,
    tabla        TEXT          NOT NULL,
    metrica      TEXT          NOT NULL,
    valor        BIGINT        NOT NULL,
    fecha_carga  TIMESTAMPTZ   NOT NULL
);
