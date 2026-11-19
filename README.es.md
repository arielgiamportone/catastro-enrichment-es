# catastro-enrichment-es

[![CI](https://github.com/arielgiamportone/catastro-enrichment-es/actions/workflows/ci.yml/badge.svg)](https://github.com/arielgiamportone/catastro-enrichment-es/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/catastro-enrich.svg)](https://pypi.org/project/catastro-enrich/)
![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)

Completa un inventario de inmuebles español con **coordenadas WGS84,
dirección oficial, superficie construida, año de construcción, uso catastral
y una imagen de localización**, usando solo servicios públicos gratuitos: los
cinco catastros españoles tras una única interfaz, Nominatim como geocoder de
respaldo, ortofotos PNOA y vistas de calle de Mapillary almacenadas con su
atribución, y Google solo como enlace en vivo.

*English version: [README.md](https://github.com/arielgiamportone/catastro-enrichment-es/blob/main/README.md).*

> Patrones desarrollados durante mi trabajo en Intervalor Data y publicados con autorización. Implementación independiente (clean-room) con datos sintéticos; no contiene código ni datos de la empresa ni de sus clientes.

Instalación: `pip install catastro-enrich` (el paquete importable es `catastro_enrich`).

## El problema

Los inventarios patrimoniales de entidades públicas y las carteras
inmobiliarias llegan incompletos: referencias catastrales sin coordenadas,
direcciones en texto libre sin referencia, superficies y años vacíos, ninguna
imagen. Completarlos a mano es lento y propenso a errores, y España no tiene
un catastro sino **cinco**: la Dirección General del Catastro (DGC) para el
régimen común y los catastros forales de Álava, Bizkaia, Gipuzkoa y Navarra,
cada uno con su protocolo, su formato de referencia, su dialecto de filtro y
su sistema de coordenadas.

La librería ofrece un contrato único (`lookup_rc`, `physical_data`, `batch`)
para todos los territorios, recurre al geocoding cuando el catastro no
resuelve y ejecuta **lotes idempotentes, en ensayo por defecto** y con
telemetría, de modo que una pasada pueda repetirse sin riesgo.

## Restricciones

| Restricción | Consecuencia |
|---|---|
| Servicios gratuitos, públicos y sin autenticación (Mapillary pide un token gratuito) | Retardo de cortesía por servicio, `User-Agent` identificable con contacto, sin reintentos ante HTTP 429 |
| Los errores de negocio llegan con HTTP 200 | Cada driver analiza el cuerpo y devuelve **códigos de error como datos**; un lote nunca aborta por un bien |
| Namespaces XML inconsistentes (ArcGIS Server) | Todo el XML se analiza por nombre local de etiqueta |
| Licencias distintas por fuente | PNOA y Mapillary se guardan con atribución; Google solo como enlace |
| El inventario es del llamador | La librería recibe y devuelve registros; un adaptador SQLite de referencia muestra cómo persistir cambios, imágenes y ejecuciones |
| Las pasadas deben poder repetirse | Escrituras aditivas, copia de seguridad antes de escribir, ensayo explícito |
| Caso de estudio público | Implementación clean-room, solo datos sintéticos, test de fugas en CI |

## Arquitectura

```
            PropertyRecord (id, referencia, calle, localidad, lat, lon, ...)
                                   |
                       enrich_batch / enrich_property
                                   |
      +----------------------------+-----------------------------+
      |                            |                             |
 resolve_cadastre_driver     geocode_address               fetch_imagery
 (provincia INE -> driver)   (Nominatim, precisión         (orto WMS,
      |                       por clase OSM)                Mapillary,
      |                                                     live_links)
 +----+----+----------+----------+----------+                    |
 | DGC     | Gipuzkoa | Navarra  | Álava    | Bizkaia  |      ImageStore
 | OVC XML | INSPIRE  | INSPIRE  | ArcGIS   | ArcGIS   |   (dedup SHA-256,
 | +INSPIRE| stored q.| cql_filt.| FES XML  | FES XML  |    una por fuente)
 +---------+----------+----------+----------+----------+
                                   |
                   Transport (urllib estándar, inyectable) + Pacer
```

| Módulo | Responsabilidad |
|---|---|
| `catastro_enrich.cadastre` | ABC `CadastreDriver`, cinco drivers y el router `resolve_cadastre_driver` (el código INE manda sobre el nombre, sinónimos, respaldo DGC) |
| `catastro_enrich.geocoding` | Normalización de direcciones, cliente Nominatim, elección del hit por clase OSM con precisión `exact` / `zone` / `administrative` |
| `catastro_enrich.geo` | Husos UTM 28–31 ↔ WGS84 con transformadores cacheados, centroide del anillo, comprobación del orden de ejes, bounding boxes WMS |
| `catastro_enrich.imagery` | Driver WMS genérico (PNOA por defecto, alias regionales), Mapillary, enlaces Google, `ImageStore` |
| `catastro_enrich.enrichment` | Cascada, diffs aditivos, lotes con contadores, callback de progreso y telemetría |
| `catastro_enrich.telemetry` | Una fila por ejecución (`batch_runs`), protegida para que un fallo de telemetría nunca pare el lote |
| `catastro_enrich.storage` | Adaptador SQLite de referencia: carga de registros, aplicación de diffs, metadatos de imagen, copia con marca de tiempo |
| `catastro_enrich.synthetic` | Generadores de registros, referencias artificiales y respuestas de servicio usados por los tests |
| `catastro_enrich.cli` | Comando `catastro-enrich` |

**Cascada de coordenadas.** Con referencia → catastro del territorio.
Referencia inexistente o inválida y dirección presente → geocoding. Nada
resuelve → pendiente con motivo. El geocoding nunca sobrescribe una
coordenada catastral y solo los hits `exact` se persisten salvo
`--accept-zone`.

## Decisiones y alternativas descartadas

| Decisión | Elegido | Descartado | Por qué |
|---|---|---|---|
| Multi-catastro | Driver abstracto + router por provincia INE | Un cliente parametrizado | ArcGIS ignora `cql_filter`; los servicios INSPIRE no tienen `REF_CATASTRAL`; difieren el orden de ejes y el formato de referencia |
| Geocoder | Nominatim (ODbL, 1 pet/s, servidor propio configurable) | Google Geocoding | Coste, prohibición de almacenar resultados, envío de datos públicos a un tercero comercial |
| Confianza del geocoding | Clase OSM con etiquetas de precisión | Umbral de `importance` | La importancia mide relevancia global; las calles rurales puntúan 0,05–0,10 y se descartarían |
| Hits de zona | No se persisten por defecto | Aceptar `place`/`landuse` | El centroide de un barrio no es el inmueble |
| Imaginería | Guardar PNOA/Mapillary, enlazar Google | Enlazar todo / guardar todo | Portabilidad sin conexión frente a términos de licencia |
| Centroide de parcela | Media aritmética del anillo exterior | Centroide por área | Basta para situar; sin dependencia geométrica |
| WMS | 1.3.0 con ejes invertidos y soporte 1.1.1 | Solo 1.1.1 | El servicio nacional publica 1.3.0 |
| Datos físicos de una parcela | Expandir la lista de unidades y agregar (suma de superficies, año más antiguo) | Tomar la primera unidad | Verificado en vivo: una parcela responde con sus unidades y sin superficies |
| HTTP | Biblioteca estándar con transporte inyectable | `requests` / `httpx` | Cero dependencias en ejecución aparte de `pyproj`; los tests corren sin red |
| Errores | Códigos en el objeto resultado | Excepciones | Un lote debe contar y seguir |

Las decisiones tomadas durante la implementación están en
[docs/DECISIONES.md](https://github.com/arielgiamportone/catastro-enrichment-es/blob/main/docs/DECISIONES.md).

## Qué se midió y cómo reproducirlo

Todo lo siguiente corre sin red; la suite de tests bloquea el acceso a
Internet.

| Medida | Valor | Comando |
|---|---|---|
| Tests | 139 en verde, unos 1,3 s | `pytest` |
| Cobertura de líneas | 97 % (`pip install pytest-cov`) | `pytest --cov=catastro_enrich` |
| Lint y formato | limpio | `ruff check . && ruff format --check .` |
| Guardia de fugas | árbol, nombres de fichero y `git log -p` limpios | `pytest tests/test_no_leaks.py` |
| Ida y vuelta UTM ↔ WGS84 | error < 1e-6° y < 0,1 m en los husos 28–31 | `pytest tests/test_geo.py` |

Los servicios públicos se ejercitaron una vez a mano durante el desarrollo
(punto → referencia, referencia → coordenadas, datos físicos con expansión
de unidades, los cuatro catastros forales, Nominatim, PNOA). Nada de esas
llamadas se guarda en el repositorio.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```

## Inicio rápido

Crea un fichero de configuración y un inventario sintético, y lanza una
pasada en ensayo:

```bash
cat > config.toml <<'TOML'
[inventory]
database = "inventory.db"
municipality_id = "m-demo"
ine_code = "28079"
province = "Madrid"
municipality = "Madrid"

[images]
root = "."

[services]
contact = "https://example.org/contact"
mapillary_token_env = "MAPILLARY_TOKEN"
TOML
```

```bash
catastro-enrich init-demo --config config.toml --count 25
```

```bash
catastro-enrich cadastre --config config.toml --limit 5
```

El resumen termina con `DRY RUN - ensayo: nada escrito`; añade `--write`
para persistir (antes se toma una copia con marca de tiempo). Otros
subcomandos: `physical`, `geocoding --accept-zone`, `imagery --ortho
auto|ign|<alias>|none --mapillary-token ... --span-m 80`, `rc-at-point LAT
LON`, `runs`.

Como librería:

```python
from catastro_enrich import PropertyRecord, enrich_batch, resolve_cadastre_driver

cadastre = resolve_cadastre_driver("20069")  # Gipuzkoa por código INE
records = [PropertyRecord(id="1", cadastral_reference="069-REF-3232")]
stats = enrich_batch(records, cadastre=cadastre)  # ensayo: nada escrito
print(stats.ok, stats.not_found, stats.would_write)
```

Las referencias sintéticas de `catastro_enrich.synthetic` usan una hoja
artificial y no resuelven en los servicios reales; existen para ejercitar el
código sin red.

## Límites conocidos

- **Datos físicos solo de la DGC.** Los drivers forales devuelven
  `NOT_SUPPORTED`.
- **Capas y campos forales** leídos de las capabilities públicas de cada
  servicio en noviembre de 2026; son argumentos del constructor para poder
  cambiarlos sin publicar versión si un servicio los renombra.
- **Provincia y municipio DGC** en mayúsculas con grafía oficial INE; la
  configuración permite sobrescribir ambos.
- **La expansión de parcela** hace una petición por unidad (tope `max_units`,
  40 por defecto) y es lenta en bloques grandes.
- **La instancia pública de Nominatim** es para uso ligero (≤ 1 petición/s,
  cientos de bienes por municipio); apunta `services.nominatim_url` a una
  instancia propia para algo mayor.
- **Centroide aritmético**, no por área: vale para situar, no para geometría.
- **Las vistas de calle pueden mostrar personas o matrículas**; Mapillary las
  difumina, pero revísalas antes de publicar.
- Sin Registro de la Propiedad, sin descarga de parcelario completo, sin
  geocoders de pago.

## Cumplimiento y atribución de fuentes

| Fuente | Licencia | Atribución usada |
|---|---|---|
| DGC (Sede Electrónica del Catastro) | Datos abiertos, uso no masivo e identificado | "Dirección General del Catastro" |
| Catastros forales (Álava, Bizkaia, Gipuzkoa, Navarra) | CC-BY / CC-BY 4.0 | Cada Diputación Foral / Gobierno de Navarra |
| PNOA (IGN) | CC-BY 4.0 | "PNOA © Instituto Geográfico Nacional" |
| Mapillary | CC-BY-SA | "© Mapillary contributors" más id de imagen y fecha de captura |
| Nominatim / OSM | ODbL, política de uso de la OSMF | "© OpenStreetMap contributors" |
| Google Maps | Los términos prohíben cachear | Solo enlaces en vivo |

Las direcciones y referencias de edificios públicos no son datos personales;
aplicado a carteras privadas sí pueden serlo (referencia más titular). La
librería solo transmite referencias, direcciones y coordenadas.

## Cómo se construyó

Proceso clean-room en tres fases: a partir del trabajo original se redactó
una especificación sin código; este repositorio se implementó en una sesión
separada a partir de esa especificación y de la documentación pública de los
servicios, solo con datos sintéticos; una tercera revisión compara ambos en
busca de similitudes y fugas antes de publicar. Un test de fugas con lista
ofuscada corre en CI junto a `gitleaks`.

## Licencia

MIT. Véase [LICENSE](https://github.com/arielgiamportone/catastro-enrichment-es/blob/main/LICENSE) y [CHANGELOG.md](https://github.com/arielgiamportone/catastro-enrichment-es/blob/main/CHANGELOG.md).
