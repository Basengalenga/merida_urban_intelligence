-- Validaciones post-carga de la capa silver.
-- Correr con:
--   docker exec -i yucatan_dw psql -U admin -d yucatan_dw < validaciones/silver.sql
-- No vive en ./sql porque esa carpeta se monta en /docker-entrypoint-initdb.d y Docker
-- ejecutaría estas consultas al crear la base, cuando bronze todavía no existe.

-- 1. Conteo de filas por tabla
SELECT 'municipios' AS tabla, count(*) AS filas FROM silver.municipios
UNION ALL SELECT 'censo_municipal', count(*) FROM silver.censo_municipal
UNION ALL SELECT 'cat_scian', count(*) FROM silver.cat_scian
UNION ALL SELECT 'crosswalk_municipio_nombre', count(*) FROM silver.crosswalk_municipio_nombre
UNION ALL SELECT 'denue_establecimientos', count(*) FROM silver.denue_establecimientos
UNION ALL SELECT 'incidencia_delictiva', count(*) FROM silver.incidencia_delictiva;

-- 2. FKs sin huérfanos (todas deben dar 0)
SELECT 'censo -> municipios' AS relacion, count(*) AS huerfanos
FROM silver.censo_municipal c LEFT JOIN silver.municipios m USING (cvegeo) WHERE m.cvegeo IS NULL
UNION ALL
SELECT 'denue -> municipios', count(*)
FROM silver.denue_establecimientos d LEFT JOIN silver.municipios m USING (cvegeo) WHERE m.cvegeo IS NULL
UNION ALL
SELECT 'denue -> cat_scian', count(*)
FROM silver.denue_establecimientos d
LEFT JOIN silver.cat_scian s ON s.codigo = d.codigo_act AND s.version_scian = d.version_scian
WHERE s.codigo IS NULL
UNION ALL
SELECT 'incidencia -> municipios', count(*)
FROM silver.incidencia_delictiva i LEFT JOIN silver.municipios m USING (cvegeo) WHERE m.cvegeo IS NULL
UNION ALL
SELECT 'crosswalk -> municipios', count(*)
FROM silver.crosswalk_municipio_nombre x LEFT JOIN silver.municipios m USING (cvegeo) WHERE m.cvegeo IS NULL;

-- 3. Cobertura de cvegeo al 100%: municipios presentes en cada tabla (deben ser 106)
--    y registros sin cvegeo (deben ser 0)
SELECT 'censo_municipal' AS tabla, count(DISTINCT cvegeo) AS municipios_cubiertos,
       count(*) FILTER (WHERE cvegeo IS NULL) AS sin_cvegeo
FROM silver.censo_municipal
UNION ALL
SELECT 'denue_establecimientos', count(DISTINCT cvegeo), count(*) FILTER (WHERE cvegeo IS NULL)
FROM silver.denue_establecimientos
UNION ALL
SELECT 'incidencia_delictiva', count(DISTINCT cvegeo), count(*) FILTER (WHERE cvegeo IS NULL)
FROM silver.incidencia_delictiva
UNION ALL
SELECT 'crosswalk_municipio_nombre', count(DISTINCT cvegeo), count(*) FILTER (WHERE cvegeo IS NULL)
FROM silver.crosswalk_municipio_nombre;

-- Nombres del dataset de crimen sin entrada en el crosswalk (debe dar 0)
SELECT count(DISTINCT b.municipio) AS nombres_crimen_sin_crosswalk
FROM bronze.incidencia_delictiva b
LEFT JOIN silver.crosswalk_municipio_nombre x ON x.nombre_fuente = b.municipio
WHERE x.nombre_fuente IS NULL;

-- 4. % de establecimientos donde coincide_municipio = false, por edición
SELECT edicion,
       count(*) AS establecimientos,
       count(*) FILTER (WHERE NOT coincide_municipio) AS no_coincide,
       round(100.0 * count(*) FILTER (WHERE NOT coincide_municipio) / count(*), 3) AS pct_no_coincide
FROM silver.denue_establecimientos
GROUP BY ROLLUP (edicion)
ORDER BY edicion;

-- 5. Geometrías válidas y SRID correcto (inválidas y SRID distinto deben dar 0)
SELECT 'municipios' AS tabla,
       count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalidas,
       count(*) FILTER (WHERE ST_SRID(geom) <> 6372) AS srid_distinto
FROM silver.municipios
UNION ALL
SELECT 'denue_establecimientos',
       count(*) FILTER (WHERE NOT ST_IsValid(geom)),
       count(*) FILTER (WHERE ST_SRID(geom) <> 6372)
FROM silver.denue_establecimientos;

-- Extra: bitácora de la última corrida (descartes y reasignaciones del DENUE)
SELECT tabla, metrica, valor
FROM silver.etl_log
WHERE run_id = (SELECT run_id FROM silver.etl_log ORDER BY fecha_carga DESC, id DESC LIMIT 1)
ORDER BY id;
