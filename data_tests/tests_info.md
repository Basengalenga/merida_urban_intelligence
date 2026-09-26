# Pruebas de fuentes de datos: Mérida Urban Intelligence

Antes de construir la infraestructura, probamos cada fuente y registramos qué columnas tiene.

## Reglas

1. **Un notebook (`.ipynb`) por fuente.**
2. **API cuando exista.** Lo que no se pueda obtener por API se descarga y se guarda en `data_tests/data/`.
3. **Los archivos descargados no se editan.** Se guardan tal cual, con la fecha de descarga en el nombre (ej. `censo_ageb_31_2026-09-26.csv`).
4. **Al final, todo se resume en `data_tests/usefulcolumns.txt`**, con el formato descrito al final de este documento.

## Estructura

```
data_tests/
├── data/                  ← archivos que no vienen de API (CSV, shapefiles)
├── 01_denue.ipynb
├── 02_cartografia.ipynb
├── 03_censo.ipynb
├── 04_delitos.ipynb
└── usefulcolumns.txt
```

## Qué revisar en cada notebook

Aplica a todas las fuentes:

- [ ] ¿La conexión o descarga funciona?
- [ ] ¿Cuántos registros trae?
- [ ] Lista de columnas y tipo de dato de cada una.
- [ ] Nulos por columna.
- [ ] Muestra de 5 a 10 filas.
- [ ] Problemas encontrados, en una celda de notas al final.

---

## 1. DENUE (capa económica): API

| | |
|---|---|
| **Tipo** | API (requiere token gratuito) |
| **Página de la API** | https://www.inegi.org.mx/servicios/api_denue.html |
| **Token** | https://www.inegi.org.mx/app/api/denue/tokenVerify/tokenverify.html |
| **Documentación** | Misma página, pestaña "Guía para desarrolladores" |
| **Notebook** | `01_denue.ipynb` |

**Llamadas a probar**
- Todos los negocios de Mérida (paginado con `{inicio}` y `{fin}`):
  `https://www.inegi.org.mx/app/api/denue/v1/consulta/BuscarAreaAct/31/050/0/0/0/0/0/0/0/0/{inicio}/{fin}/0/{token}`
- Total de negocios en Mérida, para validar la paginación:
  `https://www.inegi.org.mx/app/api/denue/v1/consulta/Cuantificar/0/31050/0/{token}`

**Columnas esperadas:** `CLEE`, `Id`, `Nombre`, `Clase_actividad`, `Estrato`, `Latitud`, `Longitud`, `AGEB`, `Manzana`, `AreaGeo`, `SECTOR_ACTIVIDAD_ID`, `SUBSECTOR_ACTIVIDAD_ID`, `RAMA_ACTIVIDAD_ID`, `CLASE_ACTIVIDAD_ID`, `Tipo`, `Fecha_Alta`

**Pruebas específicas**
- [ ] ¿El total que trajo la paginación coincide con el de `Cuantificar`?
- [ ] ¿Hay `Id` duplicados? Deduplicar por `Id`/`CLEE`, nunca por coordenadas.
- [ ] ¿Hay coordenadas vacías o fuera de Mérida?
- [ ] Guardar el JSON crudo con la fecha de extracción. La API se actualiza sola (la próxima versión sale el 25 nov 2026).

**Para qué KPIs sirve:** Total Businesses, Business Density, Businesses per 1,000, Retail Density (sector 46), Service Density (sectores por definir), Dominant Economic Activity.

---

## 2. Cartografía / polígonos AGEB (capa geográfica): API

| | |
|---|---|
| **Tipo** | API (sin token) |
| **Página de la API** | https://www.inegi.org.mx/servicios/catalogoUnico.html |
| **Endpoint AGEB urbanas de Mérida** | https://gaia.inegi.org.mx/wscatgeo/v2/geo/agebu/31/050 |
| **Documentación** | Misma página, más el PDF: https://www.inegi.org.mx/servicios/descargas/Consulta_info_vectorial.pdf |
| **Respaldo (no API)** | Marco Geoestadístico, corte "Censo 2020": https://www.inegi.org.mx/temas/mg/ (shapefile a `data_tests/data/`) |
| **Notebook** | `02_cartografia.ipynb` |

**Columnas esperadas:** `cvegeo`, `cve_ent`, `nom_ent`, `cve_mun`, `nom_mun`, `cve_loc`, `cve_ageb`, `pob_total`, `pob_femenina`, `pob_masculina`, `total_viviendas_habitadas`, `geometry`

**Pruebas específicas**
- [ ] ¿Cuál es el CRS? (el GeoJSON probablemente viene en lat/lon; el shapefile del MG, en EPSG:6372)
- [ ] ¿Hay geometrías inválidas?
- [ ] ¿Cuántas AGEB hay en total y cuántas con `cve_loc = 0001` (ciudad de Mérida)?
- [ ] **Prueba clave:** ¿las claves `cvegeo` coinciden al 100% con las del Censo 2020? Si no coinciden, usar el shapefile de respaldo.
- [ ] Todos los atributos vienen como texto: anotar cuáles hay que convertir a número.

**Para qué sirve:** unir puntos con polígonos (spatial join), calcular el área para las densidades y construir la matriz de vecinos para Moran/LISA.

---

## 3. Censo de Población y Vivienda 2020 (capa demográfica): descarga CSV

| | |
|---|---|
| **Tipo** | Descarga, **no hay API a nivel AGEB** |
| **Dónde** | https://www.inegi.org.mx/programas/ccpv/2020/#datos_abiertos → "Principales resultados por AGEB y manzana urbana" → Yucatán |
| **Documentación** | https://www.inegi.org.mx/contenidos/programas/ccpv/2020/doc/fd_agebmza_urbana_cpv2020.pdf |
| **Guardar en** | `data_tests/data/` |
| **Notebook** | `03_censo.ipynb` |

**Columnas que nos interesan**
- Identificación: `ENTIDAD`, `MUN`, `LOC`, `AGEB`, `MZA`, `NOM_LOC`
- Población: `POBTOT`
- Edad: `POB0_14`, `POB15_64`, `POB65_MAS` (o los rangos finos `P_0A2`, `P_6A11`, `P_15A17`, `P_18A24`, `P_60YMAS`)
- Actividad económica: `P_12YMAS`, `PEA`, `PE_INAC`, `POCUPADA`, `PDESOCUP`
- Vivienda: `VIVTOT`, `TVIVHAB`, `VIVPAR_HAB`

**Pruebas específicas**
- [ ] Quedarse solo con filas de AGEB: `AGEB != 0000` y `MZA == 000`. Las demás filas son totales.
- [ ] ¿Cuántas celdas tienen `*` (dato oculto) o `N/D`, por columna?
- [ ] La suma de `POBTOT` de las AGEB debe quedar por debajo de 995,129 (total municipal).
- [ ] ¿Cuántas AGEB tienen `POBTOT = 0`?

**Para qué KPIs sirve:** Total Population, Population Density, EAP Rate (`PEA / P_12YMAS`), Population by Age Group, y los denominadores de Crime Rate y Businesses per 1,000.

---

## 4. Delitos (capa de seguridad pública): pendiente

### 4a. SESNSP municipal: descarga CSV (disponible)

| | |
|---|---|
| **Tipo** | Descarga, no API |
| **Dónde** | https://www.datos.gob.mx/dataset/incidencia_delictiva (base municipal, 2015 a dic 2025) |
| **Guardar en** | `data_tests/data/` |
| **Notebook** | `04_delitos.ipynb` |

**Pruebas específicas**
- [ ] Registrar todas las columnas que trae (no tenemos la lista confirmada).
- [ ] Filtrar Mérida y ver los conteos por año y por tipo de delito.
- [ ] Anotar que 2026 usa una metodología nueva: no mezclarlo con 2015–2025.

> ⚠️ **No trae coordenadas.** Sirve para validar totales, no para asignar delitos a AGEB.

### 4b. Delitos georreferenciados (lat/lon): sin fuente pública

- [ ] Preguntar al profesor qué dataset aprueba.
- [ ] Opción alternativa: solicitud de transparencia a la Fiscalía General del Estado de Yucatán, la SSP Yucatán o la Policía Municipal de Mérida (pedir fecha, hora, tipo de delito y coordenadas; si no, al menos la colonia).

**Cuando se consiga, revisar**
- [ ] Coordenadas nulas, en (0,0), invertidas o fuera de Mérida.
- [ ] CRS (no asumir WGS84 sin verificar).
- [ ] Formato de fechas y horas; categorías de delito.
- [ ] ¿Cuántos incidentes por AGEB? Si son muy pocos, las tasas van a salir inestables.

**Para qué KPIs sirve:** Total Crime Incidents, Crime Rate, Incidents by Type and Time, Crime relative to Business Activity.

---

## Formato de `usefulcolumns.txt`

Una línea por columna, separada por `|`:

```
fuente | columna | descripción | tipo de dato | notas
```

Ejemplo:

```
DENUE | Id | Identificador único del establecimiento | texto | usar para deduplicar
DENUE | Latitud | Latitud del establecimiento | decimal | GEO: revisar
CENSO | POBTOT | Población total de la AGEB | entero | nunca viene con *
CENSO | PEA | Población de 12+ económicamente activa | entero (puede venir *) | * → NULL
CARTOGRAFIA | cvegeo | Clave AGEB de 13 caracteres | texto | llave de unión, ceros a la izquierda
```

### Columnas geoespaciales: investigar más

Marcarlas con `GEO: revisar` en la columna de notas y responder, para cada una:

1. **¿En qué CRS viene?** (lat/lon WGS84, EPSG:6372, etc.)
2. **¿Qué representa?** ¿Un punto exacto, un centroide o un polígono?
3. **¿Con qué otro dato geoespacial se compara, y coinciden?**
   - `Latitud`/`Longitud` del DENUE contra los polígonos AGEB.
   - `AGEB` del DENUE contra `cvegeo` de la cartografía (pueden venir de versiones distintas del mapa).
   - `cvegeo` de la cartografía contra `ENTIDAD+MUN+LOC+AGEB` del censo.
   - Coordenadas de delitos contra los polígonos AGEB.
4. **¿Qué versión o fecha tiene?** (Censo 2020, DENUE 05/2026, cartografía ¿?)

---

## Checklist final

- [ ] 4 notebooks ejecutados sin errores
- [ ] Archivos no-API guardados en `data_tests/data/` con fecha
- [ ] JSON crudo de las APIs guardado con fecha
- [ ] `usefulcolumns.txt` completo
- [ ] Columnas geoespaciales investigadas
- [ ] Dataset de delitos georreferenciados definido con el profesor