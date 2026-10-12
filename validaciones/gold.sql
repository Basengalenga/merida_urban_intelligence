-- Consultas de validación de la capa gold: cada KPI requerido se calcula directamente
-- desde las tablas de hechos y dimensiones (sin usar la vista gold.kpi_municipio_anio),
-- para demostrar que el warehouse conserva la información necesaria.
-- Correr con:
--   docker exec -i yucatan_dw psql -U admin -d yucatan_dw < validaciones/gold.sql
-- Solo lectura (SELECT). Las consultas por municipio muestran 10 filas (en general, los
-- valores más altos del KPI); quitar el LIMIT para ver los 106.

-- Año a consultar y edición DENUE que le corresponde (noviembre de cada año; 2025 usa 2025_05).
\set anio 2024
\set edicion '\'2024_11\''

-- ==========================================================================
-- KPIs demográficos (fact_censo × dim_municipio, Censo 2020)
-- ==========================================================================

-- 1. Población total
SELECT m.nombre, c.pobtot AS poblacion_total
FROM gold.fact_censo c
JOIN gold.dim_municipio m USING (cvegeo)
ORDER BY poblacion_total DESC
LIMIT 10;

-- 2. Densidad poblacional (hab/km²) = pobtot / área
SELECT m.nombre, c.pobtot, round(m.area_km2::NUMERIC, 2) AS area_km2,
       round((c.pobtot / m.area_km2)::NUMERIC, 2) AS densidad_poblacional
FROM gold.fact_censo c
JOIN gold.dim_municipio m USING (cvegeo)
ORDER BY densidad_poblacional DESC
LIMIT 10;

-- 3. Tasa de población económicamente activa = pea / población de 12 años y más
SELECT m.nombre, c.pea, c.p_12ymas,
       round(c.pea::NUMERIC / NULLIF(c.p_12ymas, 0), 4) AS tasa_pea
FROM gold.fact_censo c
JOIN gold.dim_municipio m USING (cvegeo)
ORDER BY tasa_pea DESC NULLS LAST
LIMIT 10;

-- 4. Población por grupo de edad (conteos y proporción sobre pobtot)
SELECT m.nombre, c.pob0_14, c.pob15_64, c.pob65_mas,
       round(c.pob0_14::NUMERIC   / NULLIF(c.pobtot, 0), 4) AS prop_0_14,
       round(c.pob15_64::NUMERIC  / NULLIF(c.pobtot, 0), 4) AS prop_15_64,
       round(c.pob65_mas::NUMERIC / NULLIF(c.pobtot, 0), 4) AS prop_65_mas
FROM gold.fact_censo c
JOIN gold.dim_municipio m USING (cvegeo)
ORDER BY prop_65_mas DESC NULLS LAST
LIMIT 10;

-- ==========================================================================
-- KPIs económicos (fact_establecimiento × dim_actividad × dim_municipio)
-- fact_establecimiento NO se suma entre ediciones: siempre se filtra una sola.
-- ==========================================================================

-- 5. Total de negocios
SELECT m.nombre, count(*) AS total_negocios
FROM gold.fact_establecimiento f
JOIN gold.dim_municipio m USING (cvegeo)
WHERE f.edicion = :edicion
GROUP BY m.nombre
ORDER BY total_negocios DESC
LIMIT 10;

-- 6. Densidad de negocios (negocios/km²)
SELECT m.nombre, count(*) AS total_negocios,
       round((count(*) / m.area_km2)::NUMERIC, 2) AS densidad_negocios
FROM gold.fact_establecimiento f
JOIN gold.dim_municipio m USING (cvegeo)
WHERE f.edicion = :edicion
GROUP BY m.nombre, m.area_km2
ORDER BY densidad_negocios DESC
LIMIT 10;

-- 7. Negocios por 1,000 habitantes
SELECT m.nombre, count(*) AS total_negocios, c.pobtot,
       round(1000.0 * count(*) / NULLIF(c.pobtot, 0), 2) AS negocios_por_1000_hab
FROM gold.fact_establecimiento f
JOIN gold.dim_municipio m USING (cvegeo)
JOIN gold.fact_censo c USING (cvegeo)
WHERE f.edicion = :edicion
GROUP BY m.nombre, c.pobtot
ORDER BY negocios_por_1000_hab DESC
LIMIT 10;

-- 8. Densidad retail (sector 46, negocios/km²)
SELECT m.nombre, count(*) AS negocios_retail,
       round((count(*) / m.area_km2)::NUMERIC, 2) AS densidad_retail
FROM gold.fact_establecimiento f
JOIN gold.dim_actividad a USING (id_actividad)
JOIN gold.dim_municipio m USING (cvegeo)
WHERE f.edicion = :edicion AND a.es_retail
GROUP BY m.nombre, m.area_km2
ORDER BY densidad_retail DESC
LIMIT 10;

-- 9. Densidad de servicios (sectores 51 a 81, negocios/km²)
SELECT m.nombre, count(*) AS negocios_servicio,
       round((count(*) / m.area_km2)::NUMERIC, 2) AS densidad_servicios
FROM gold.fact_establecimiento f
JOIN gold.dim_actividad a USING (id_actividad)
JOIN gold.dim_municipio m USING (cvegeo)
WHERE f.edicion = :edicion AND a.es_servicio
GROUP BY m.nombre, m.area_km2
ORDER BY densidad_servicios DESC
LIMIT 10;

-- 10. Actividad económica dominante: sector con más negocios (empate -> código de sector menor)
SELECT DISTINCT ON (m.nombre)
       m.nombre, a.codigo_sector, a.desc_sector, count(*) AS negocios_sector
FROM gold.fact_establecimiento f
JOIN gold.dim_actividad a USING (id_actividad)
JOIN gold.dim_municipio m USING (cvegeo)
WHERE f.edicion = :edicion
GROUP BY m.nombre, a.codigo_sector, a.desc_sector
ORDER BY m.nombre, negocios_sector DESC, a.codigo_sector
LIMIT 10;

-- ==========================================================================
-- KPIs de seguridad (fact_incidencia × dim_tiempo × dim_delito × dim_municipio)
-- ==========================================================================

-- 11. Total de incidentes en el año
SELECT m.nombre, sum(i.incidentes) AS total_incidentes
FROM gold.fact_incidencia i
JOIN gold.dim_tiempo t USING (id_tiempo)
JOIN gold.dim_municipio m USING (cvegeo)
WHERE t.anio = :anio
GROUP BY m.nombre
ORDER BY total_incidentes DESC
LIMIT 10;

-- 12. Tasa delictiva por 1,000 habitantes
SELECT m.nombre, sum(i.incidentes) AS total_incidentes, c.pobtot,
       round(1000.0 * sum(i.incidentes) / NULLIF(c.pobtot, 0), 2) AS tasa_delictiva_1000_hab
FROM gold.fact_incidencia i
JOIN gold.dim_tiempo t USING (id_tiempo)
JOIN gold.dim_municipio m USING (cvegeo)
JOIN gold.fact_censo c USING (cvegeo)
WHERE t.anio = :anio
GROUP BY m.nombre, c.pobtot
ORDER BY tasa_delictiva_1000_hab DESC
LIMIT 10;

-- 13a. Incidentes por tipo de delito en el año (todo el estado)
SELECT d.tipo_delito, sum(i.incidentes) AS incidentes
FROM gold.fact_incidencia i
JOIN gold.dim_tiempo t USING (id_tiempo)
JOIN gold.dim_delito d USING (id_delito)
WHERE t.anio = :anio
GROUP BY d.tipo_delito
ORDER BY incidentes DESC
LIMIT 10;

-- 13b. Incidentes por mes en el año (todo el estado)
SELECT t.mes, t.nombre_mes, sum(i.incidentes) AS incidentes
FROM gold.fact_incidencia i
JOIN gold.dim_tiempo t USING (id_tiempo)
WHERE t.anio = :anio
GROUP BY t.mes, t.nombre_mes
ORDER BY t.mes;

-- 13c. Incidentes por año, 2020 a 2025 (todo el estado)
SELECT t.anio, sum(i.incidentes) AS incidentes
FROM gold.fact_incidencia i
JOIN gold.dim_tiempo t USING (id_tiempo)
GROUP BY t.anio
ORDER BY t.anio;

-- 14. Crimen relativo a la actividad económica = 100 × incidentes / negocios
WITH crimen AS (
    SELECT i.cvegeo, sum(i.incidentes) AS total_incidentes
    FROM gold.fact_incidencia i
    JOIN gold.dim_tiempo t USING (id_tiempo)
    WHERE t.anio = :anio
    GROUP BY i.cvegeo
),
negocios AS (
    SELECT cvegeo, count(*) AS total_negocios
    FROM gold.fact_establecimiento
    WHERE edicion = :edicion
    GROUP BY cvegeo
)
SELECT m.nombre, cr.total_incidentes, n.total_negocios,
       round(100.0 * cr.total_incidentes / NULLIF(n.total_negocios, 0), 2) AS incidentes_por_100_negocios
FROM gold.dim_municipio m
JOIN crimen cr USING (cvegeo)
JOIN negocios n USING (cvegeo)
ORDER BY incidentes_por_100_negocios DESC NULLS LAST
LIMIT 10;
