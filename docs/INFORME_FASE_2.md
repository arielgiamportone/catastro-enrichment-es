# Informe de cierre de la fase 2 · catastro-enrichment-es

Fecha: 2026-11-17 · Versión: 0.1.0 · Rama: `claude/cool-davinci-g30af9`

## 1. Qué se implementó de la especificación

| Sección de la spec | Estado | Dónde |
|---|---|---|
| §3 Router provincia INE → driver, sinónimos, INE manda | Completo | `cadastre/router.py`, `tests/test_router.py` |
| §3.1 DGC: RC → coordenadas, punto → RC, dirección → RCs, parcela INSPIRE (área y polígono con comprobación de ejes) | Completo | `cadastre/dgc.py`, `tests/test_cadastre_dgc.py` |
| §3.1 DGC datos físicos (`Consulta_DNPRC`, marcado [INFERIDO]) | Completo y **verificado en vivo**; ampliado con expansión de unidades | `cadastre/dgc.py` |
| §3.2 Gipuzkoa (stored query, RC `MMM-REF-NNNN`) | Completo, verificado en vivo | `cadastre/gipuzkoa.py` |
| §3.3 Navarra (`cql_filter`, centroide) | Completo, verificado en vivo | `cadastre/navarra.py` |
| §3.4 Álava (FES XML, urbanas antes que rústicas) | Completo, verificado en vivo con capas y campos reales | `cadastre/alava.py`, `cadastre/arcgis.py` |
| §3.5 Bizkaia (descomposición de RC, tres `PropertyIsEqualTo`) | Completo, verificado en vivo | `cadastre/bizkaia.py` |
| §3.6 Nominatim (parámetros, idiomas, 1,1 s) | Completo, verificado en vivo | `geocoding.py` |
| §3.7 PNOA WMS 1.3.0, ortofoto regional por alias, Mapillary, enlaces Google | Completo (PNOA verificado en vivo; Mapillary sin token) | `imagery.py` |
| §4 Normalización de direcciones, cascada, precisión por clase, UTM↔WGS84, bbox WMS, centroide, dedup, almacenar vs enlazar | Completo | `geocoding.py`, `enrichment.py`, `geo.py`, `imagery.py` |
| §5 Ensayo por defecto, aditivo, `--overwrite`, backup previo, registro por ejecución, histórico, callback protegido | Completo | `enrichment.py`, `telemetry.py`, `storage.py`, `cli.py` |
| §6 API pública | Completa con los nombres propuestos (`resolve_cadastre_driver`, `lookup_rc`, `physical_data`, `batch`, `rc_at_point`, `rcs_by_address`, `parcel_area`, `parcel_polygon_wkt`, `geocode_address`, `utm_to_wgs84`, `wgs84_to_utm`, `enrich_property`, `enrich_batch`, `fetch_imagery`, `live_links`) | `catastro_enrich/__init__.py` |
| §6 CLI (`cadastre`, `physical`, `geocoding`, `imagery`, `rc-at-point`, `runs`, opciones comunes, `--accept-zone`, `--ortho`, `--mapillary-token`, `--span-m`, `--delay`) | Completa, más `init-demo` | `cli.py`, `tests/test_cli.py` |
| §7 Modelo de datos (entrada, escritura, resultados, fichas de imagen) | Completo | `models.py` |
| §8 Casos de prueba | Los 24 casos de la tabla tienen test | `tests/` |
| §10 Cumplimiento | Atribuciones en código y README; aviso sobre vistas de calle | `README.md`, `README.es.md` |
| Adaptador SQLite de referencia | Completo (carga, aplicación de diffs, imágenes, backup, telemetría) | `storage.py`, `telemetry.py` |

## 2. Qué quedó fuera y por qué

- **Datos físicos en catastros forales**: la spec los limita a la DGC;
  los drivers forales devuelven `NOT_SUPPORTED`.
- **Ortofoto regional concreta**: solo el driver genérico y el mecanismo
  de alias; no se configura ninguna infraestructura autonómica por nombre
  (la spec lo deja como opcional y la lista de riesgos desaconseja citar
  proveedores concretos).
- **Mapillary no se verificó en vivo**: requiere token; el flujo está
  cubierto por tests con respuestas sintéticas.
- **`Accept-Language` en euskera** se envía como preferencia secundaria,
  no se prueba contra el servicio.
- **Centroide por área y descarga de parcelario**: fuera de alcance según
  la spec.

## 3. Decisiones propias

Están enumeradas en `docs/DECISIONES.md` (28 entradas). Las de mayor
impacto:

1. Transporte con biblioteca estándar e inyectable; tests sin red y con
   bloqueo activo del acceso a Internet.
2. Capas y campos reales de Álava y Bizkaia leídos de las capabilities
   públicas, como argumentos del constructor.
3. Expansión de unidades en `physical_data` para parcelas de 14 caracteres,
   tras comprobar en vivo que el servicio responde con la lista de unidades
   y sin superficies.
4. Clases OSM desconocidas etiquetadas como `zone` (no se persisten por
   defecto).
5. `limit` cuenta registros consultados; los registros sin referencia ni
   dirección no cuentan como consultados.
6. Subcomando `init-demo` para reproducir el flujo completo con datos
   sintéticos.

## 4. Resultados de tests y calidad

| Comprobación | Resultado |
|---|---|
| `pytest` | 139 tests en verde en ~1,3 s (Python 3.11 local) |
| Cobertura de líneas (`pytest-cov`, no incluido en las dependencias) | 97 % |
| `ruff check .` y `ruff format --check .` | sin avisos |
| `tests/test_no_leaks.py` | árbol, nombres de fichero y `git log -p` de la rama limpios |
| CI (`.github/workflows/ci.yml`) | en verde en GitHub Actions (run 2 de la rama): tests en 3.11 y 3.12, ruff, gitleaks |

Comprobaciones manuales contra los servicios públicos (noviembre de 2026),
sin guardar ninguna respuesta ni referencia real:

- DGC: punto → RC (ok y `NOT_FOUND` en una plaza), RC → coordenadas,
  dirección → RCs, parcela INSPIRE (área y polígono), datos físicos con
  expansión de unidades (parcela con 77 unidades, tope respetado).
- Gipuzkoa, Navarra, Álava y Bizkaia: una parcela real resuelta con
  coordenadas y área cada una; referencias inexistentes → `NOT_FOUND`.
- Nominatim: una consulta con clase `amenity` → `exact`.
- PNOA: recorte JPEG de 36 KB.

## 5. Incidencias relevantes durante la fase

- **Commit raíz creado por GitHub.** El repositorio vacío traía un commit
  inicial cuyo autor era el nombre completo y el correo personal del titular,
  ambos en la lista prohibida. Se reescribió ese commit raíz con la identidad
  pública `arielgiamportone` (mismo contenido) y la rama se publicó con
  `--force-with-lease`. **La rama `main` del remoto conserva el commit
  original**: antes de hacer público el repo hay que reemplazar `main` por
  esta rama (por ejemplo `git push --force-with-lease origin
  claude/cool-davinci-g30af9:main`) en lugar de fusionarla, o el historial
  público volvería a contener el nombre.
- **Un test llegó a la red** por una ruta de la CLI que ignoraba el
  transporte inyectado; se corrigió y desde entonces `conftest.py` bloquea
  `urlopen` en toda la suite.
- **Respuestas de muestra descargadas** para inspeccionar estructuras GML
  cayeron por un momento en el directorio del repositorio; se movieron fuera
  antes de cualquier `git add` y no aparecen en el historial (el test de
  fugas y `git log --stat` lo confirman).
- **gitleaks en CI.** La acción oficial calculaba en el primer push un rango
  `raíz^..HEAD` inexistente y fallaba antes de escanear; el job instala el
  binario fijado (8.24.3) y escanea todo el historial y el árbol, lo que
  también sirve cuando se reemplace `main`.
- Los mensajes de commit llevan los trailers `Co-Authored-By` y
  `Claude-Session` que añade la herramienta de sesión. Si se prefiere un
  historial sin ellos, basta un `git filter-repo --message-callback` antes
  de publicar; no contienen palabras prohibidas.

## 6. Qué revisar en la fase 3

1. **Identificadores**: nombres de módulos, clases y funciones frente a la
   fuente (por ejemplo `CadastreDriver`, `EnrichmentDiff`, `RunStats`,
   `ImageStore`, `Pacer`, `batch_runs`, `property_images`).
2. **Constantes**: URLs y nombres de capa/campo provienen de documentación
   pública (capabilities); comprobar que no coinciden con constantes
   privadas no públicas.
3. **Expresión regular de prefijos ruidosos** en `geocoding.py`: es una
   reconstrucción propia de la lista de la spec.
4. **Formato de ruta de imágenes** y nombres de tabla del adaptador SQLite.
5. **Lista de palabras prohibidas** en `tests/test_no_leaks.py`: confirmar
   que cubre la lista final de la fase 1 y que la excepción de la línea de
   atribución es la única.
6. **Historial**: `git log -p` de la rama está limpio; verificar `main`
   tras el reemplazo.
7. **Datos sintéticos**: ningún fichero binario ni CSV en el árbol; las
   imágenes de prueba se generan en memoria.
