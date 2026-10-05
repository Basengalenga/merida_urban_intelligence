-- Carga de la capa gold desde silver y creación de vistas.
-- Lo ejecuta src/silver_to_gold.py dentro de una transacción, después del TRUNCATE.
-- Vive en sql/gold/ para que Docker NO lo corra al crear la base (el entrypoint solo
-- ejecuta los archivos de primer nivel de /docker-entrypoint-initdb.d).
-- Periodo de gold: 2020 a 2025.
-- Trazabilidad de los hechos: fuente y version_fuente se copian de silver; fecha_carga y run_id
-- los fija silver_to_gold.py con set_config('gold.fecha_carga' / 'gold.run_id') en la transacción.

-- --------------------------------------------------------------------------
-- Dimensiones
-- --------------------------------------------------------------------------
INSERT INTO gold.dim_municipio (cvegeo, nombre, area_km2, geom)
SELECT cvegeo, nombre_municipio, area_km2, geom
FROM silver.municipios;

INSERT INTO gold.dim_tiempo (id_tiempo, anio, mes, trimestre, nombre_mes)
SELECT a * 100 + m, a, m, (m - 1) / 3 + 1,
       (ARRAY['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
              'septiembre', 'octubre', 'noviembre', 'diciembre'])[m]
FROM generate_series(2020, 2025) AS a, generate_series(1, 12) AS m;

-- Jerarquía por prefijo del código de clase; el sector se lleva a rango (31-33, 48-49) como en silver.
INSERT INTO gold.dim_actividad (codigo_clase, desc_clase, version_scian, codigo_rama, desc_rama,
                                codigo_subsector, desc_subsector, codigo_sector, desc_sector,
                                es_retail, es_servicio)
SELECT c.codigo, c.descripcion, c.version_scian,
       r.codigo, r.descripcion,
       ss.codigo, ss.descripcion,
       s.codigo, s.descripcion,
       c.es_retail, c.es_servicio
FROM silver.cat_scian c
JOIN silver.cat_scian r  ON r.version_scian  = c.version_scian AND r.nivel  = 'rama'      AND r.codigo  = left(c.codigo, 4)
JOIN silver.cat_scian ss ON ss.version_scian = c.version_scian AND ss.nivel = 'subsector' AND ss.codigo = left(c.codigo, 3)
JOIN silver.cat_scian s  ON s.version_scian  = c.version_scian AND s.nivel  = 'sector'    AND s.codigo  =
     CASE WHEN left(c.codigo, 2) IN ('31', '32', '33') THEN '31-33'
          WHEN left(c.codigo, 2) IN ('48', '49') THEN '48-49'
          ELSE left(c.codigo, 2) END
WHERE c.nivel = 'clase';

INSERT INTO gold.dim_delito (bien_juridico, tipo_delito, subtipo_delito, modalidad)
SELECT DISTINCT bien_juridico, tipo_delito, subtipo_delito, modalidad
FROM silver.incidencia_delictiva
WHERE anio BETWEEN 2020 AND 2025
ORDER BY 1, 2, 3, 4;

-- --------------------------------------------------------------------------
-- Hechos
-- --------------------------------------------------------------------------
INSERT INTO gold.fact_censo (cvegeo, pobtot, p_12ymas, pea, pe_inac, pob0_14, pob15_64, pob65_mas, vivtot, tvivhab,
                             fuente, version_fuente, fecha_carga, run_id)
SELECT cvegeo, pobtot, p_12ymas, pea, pe_inac, pob0_14, pob15_64, pob65_mas, vivtot, tvivhab,
       fuente, version_fuente,
       current_setting('gold.fecha_carga')::TIMESTAMPTZ, current_setting('gold.run_id')::UUID
FROM silver.censo_municipal;

-- id_tiempo = mes de la edición ('2020_11' -> 202011)
INSERT INTO gold.fact_establecimiento (id_denue, edicion, cvegeo, id_actividad, id_tiempo, per_ocu, geom,
                                       fuente, version_fuente, fecha_carga, run_id)
SELECT d.id_denue, d.edicion, d.cvegeo, a.id_actividad, replace(d.edicion, '_', '')::INTEGER, d.per_ocu, d.geom,
       d.fuente, d.version_fuente,
       current_setting('gold.fecha_carga')::TIMESTAMPTZ, current_setting('gold.run_id')::UUID
FROM silver.denue_establecimientos d
JOIN gold.dim_actividad a ON a.codigo_clase = d.codigo_act AND a.version_scian = d.version_scian;

INSERT INTO gold.fact_incidencia (cvegeo, id_tiempo, id_delito, incidentes,
                                  fuente, version_fuente, fecha_carga, run_id)
SELECT i.cvegeo, i.anio * 100 + i.mes, d.id_delito, i.incidentes,
       i.fuente, i.version_fuente,
       current_setting('gold.fecha_carga')::TIMESTAMPTZ, current_setting('gold.run_id')::UUID
FROM silver.incidencia_delictiva i
JOIN gold.dim_delito d USING (bien_juridico, tipo_delito, subtipo_delito, modalidad)
WHERE i.anio BETWEEN 2020 AND 2025;

-- Contigüidad Queen: vecinos si comparten un borde o solo un vértice.
-- DECISIÓN HUMANA: se usa ST_Intersects y no ST_Touches porque 45 pares de municipios
-- vecinos se enciman unos m² (máx. 3.61 m², imprecisión del Marco Geoestadístico) y
-- ST_Touches los descarta (Kanasín quedaba sin vecinos). Ningún par separado está a < 1 m,
-- así que ST_Intersects no agrega falsos vecinos (ver README).
INSERT INTO gold.vecinos_municipio (cvegeo, cvegeo_vecino)
SELECT a.cvegeo, b.cvegeo
FROM gold.dim_municipio a
JOIN gold.dim_municipio b ON a.cvegeo <> b.cvegeo AND ST_Intersects(a.geom, b.geom);

-- --------------------------------------------------------------------------
-- Vistas
-- --------------------------------------------------------------------------

-- Panel anual municipio × año (2020 a 2025).
--   Población: Censo 2020 para todos los años.
--   DENUE: edición de noviembre de cada año; 2025 usa 2025_05 (no existe noviembre).
--   Crimen: suma de los 12 meses del año.
--   Actividad dominante: sector con más negocios; empate -> código de sector menor.
CREATE OR REPLACE VIEW gold.kpi_municipio_anio AS
WITH anio_edicion (anio, edicion) AS (
    VALUES (2020, '2020_11'), (2021, '2021_11'), (2022, '2022_11'),
           (2023, '2023_11'), (2024, '2024_11'), (2025, '2025_05')
),
negocios AS (
    SELECT ae.anio, f.cvegeo,
           count(*)                              AS total_negocios,
           count(*) FILTER (WHERE a.es_retail)   AS negocios_retail,
           count(*) FILTER (WHERE a.es_servicio) AS negocios_servicio
    FROM anio_edicion ae
    JOIN gold.fact_establecimiento f ON f.edicion = ae.edicion
    JOIN gold.dim_actividad a USING (id_actividad)
    GROUP BY ae.anio, f.cvegeo
),
sector_dominante AS (
    SELECT DISTINCT ON (ae.anio, f.cvegeo)
           ae.anio, f.cvegeo, a.codigo_sector, a.desc_sector
    FROM anio_edicion ae
    JOIN gold.fact_establecimiento f ON f.edicion = ae.edicion
    JOIN gold.dim_actividad a USING (id_actividad)
    GROUP BY ae.anio, f.cvegeo, a.codigo_sector, a.desc_sector
    ORDER BY ae.anio, f.cvegeo, count(*) DESC, a.codigo_sector
),
crimen AS (
    SELECT t.anio, i.cvegeo, sum(i.incidentes) AS total_incidentes
    FROM gold.fact_incidencia i
    JOIN gold.dim_tiempo t USING (id_tiempo)
    GROUP BY t.anio, i.cvegeo
)
SELECT ae.anio,
       m.cvegeo,
       m.nombre,
       c.pobtot                                                              AS poblacion_total,
       c.pobtot / NULLIF(m.area_km2, 0)                                      AS densidad_poblacional,
       c.pea::DOUBLE PRECISION / NULLIF(c.p_12ymas, 0)                       AS tasa_pea,
       c.pob0_14::DOUBLE PRECISION / NULLIF(c.pobtot, 0)                     AS prop_0_14,
       c.pob15_64::DOUBLE PRECISION / NULLIF(c.pobtot, 0)                    AS prop_15_64,
       c.pob65_mas::DOUBLE PRECISION / NULLIF(c.pobtot, 0)                   AS prop_65_mas,
       n.total_negocios,
       n.total_negocios / NULLIF(m.area_km2, 0)                              AS densidad_negocios,
       1000.0 * n.total_negocios / NULLIF(c.pobtot, 0)                       AS negocios_por_1000_hab,
       n.negocios_retail / NULLIF(m.area_km2, 0)                             AS densidad_retail,
       n.negocios_servicio / NULLIF(m.area_km2, 0)                           AS densidad_servicios,
       sd.codigo_sector                                                      AS sector_dominante,
       sd.desc_sector                                                        AS desc_sector_dominante,
       cr.total_incidentes,
       1000.0 * cr.total_incidentes / NULLIF(c.pobtot, 0)                    AS tasa_delictiva_1000_hab,
       100.0 * cr.total_incidentes / NULLIF(n.total_negocios, 0)             AS incidentes_por_100_negocios,
       m.geom
FROM gold.dim_municipio m
CROSS JOIN anio_edicion ae
JOIN gold.fact_censo c USING (cvegeo)
LEFT JOIN negocios n          ON n.cvegeo = m.cvegeo  AND n.anio = ae.anio
LEFT JOIN sector_dominante sd ON sd.cvegeo = m.cvegeo AND sd.anio = ae.anio
LEFT JOIN crimen cr           ON cr.cvegeo = m.cvegeo AND cr.anio = ae.anio;

-- Cociente de localización municipio × año × sector, con la misma regla de edición por año.
-- LQ = (negocios del sector en el municipio / negocios del municipio)
--    / (negocios del sector en el estado / negocios del estado)
-- Solo incluye los sectores presentes en cada municipio.
CREATE OR REPLACE VIEW gold.cociente_localizacion AS
WITH anio_edicion (anio, edicion) AS (
    VALUES (2020, '2020_11'), (2021, '2021_11'), (2022, '2022_11'),
           (2023, '2023_11'), (2024, '2024_11'), (2025, '2025_05')
),
conteo AS (
    SELECT ae.anio, f.cvegeo, a.codigo_sector, a.desc_sector, count(*) AS negocios_sector
    FROM anio_edicion ae
    JOIN gold.fact_establecimiento f ON f.edicion = ae.edicion
    JOIN gold.dim_actividad a USING (id_actividad)
    GROUP BY ae.anio, f.cvegeo, a.codigo_sector, a.desc_sector
),
totales AS (
    SELECT conteo.*,
           sum(negocios_sector) OVER (PARTITION BY anio, cvegeo)        AS negocios_municipio,
           sum(negocios_sector) OVER (PARTITION BY anio, codigo_sector) AS negocios_sector_estado,
           sum(negocios_sector) OVER (PARTITION BY anio)                AS negocios_estado
    FROM conteo
)
SELECT anio, cvegeo, codigo_sector, desc_sector,
       negocios_sector, negocios_municipio, negocios_sector_estado, negocios_estado,
       (negocios_sector::DOUBLE PRECISION / NULLIF(negocios_municipio, 0))
         / NULLIF(negocios_sector_estado::DOUBLE PRECISION / NULLIF(negocios_estado, 0), 0) AS lq
FROM totales;
