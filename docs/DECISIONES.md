# Decisiones propias de la implementación

Decisiones que la especificación no fijaba y que se tomaron durante la fase
2. Cada entrada indica el contexto, la alternativa descartada y dónde vive
en el código.

## Infraestructura

1. **Transporte HTTP con la biblioteca estándar.** `urllib.request` tras un
   protocolo `Transport` inyectable (`transport.py`). Descartado `requests`:
   evita una dependencia en ejecución y permite tests completamente sin red
   (`ScriptedTransport` en `synthetic.py`). Un fallo de red devuelve `None`;
   un estado HTTP de error se devuelve como respuesta para que el llamador lo
   clasifique (429 → `NET`, sin reintentos).
2. **Ritmo de peticiones con `Pacer`.** Un objeto por driver garantiza un
   retardo mínimo entre llamadas; reloj y `sleep` inyectables para verificar el
   espaciado sin esperar. La primera llamada nunca espera.
3. **`User-Agent` con contacto por variable de entorno** (`CATASTRO_ENRICH_CONTACT`)
   o por configuración (`services.contact`), además de la URL del repositorio.
4. **Los tests bloquean la red** (`conftest.py` sustituye `urlopen`). Surgió
   de un error real: una ruta de la CLI ignoraba el transporte inyectado y
   llegó a llamar al servicio público durante un test.

## Catastros

5. **Capas y campos de Álava y Bizkaia** se tomaron de los ficheros
   `GetCapabilities` y `DescribeFeatureType` públicos de ambos servicios
   (noviembre de 2026): parcelas urbanas y rústicas con `REF_CATASTRAL` e
   `INFO` en Álava; capa `Parcelas` con `Codigo_Mun`, `Codigo_Pol`,
   `Codigo_Par` y `Area` en Bizkaia. Son argumentos del constructor.
6. **`numberMatched="unknown"` no implica ausencia.** Navarra lo devuelve con
   resultados; se considera `NOT_FOUND` solo cuando no hay ninguna
   `CadastralParcel` o `numberReturned`/`numberMatched` es `0`.
7. **Punto de referencia con respaldo en el centroide.** Si una `CadastralParcel`
   no publica `referencePoint`, se usa el centroide aritmético del anillo
   exterior; ambos pasan por el mismo intérprete de SRS (`cadastre/gml.py`).
8. **Orden de ejes por SRS.** Un `srsName` con código 25828–25831 se trata
   como UTM (`easting northing`); cualquier otro como `lat lon` con
   comprobación de rango español y permuta si no encaja.
9. **Datos físicos de una parcela (14 caracteres).** Verificado en vivo: el
   servicio responde con la lista de unidades (`lrcdnp/rcdnp`) y sin
   superficies. El driver expande cada unidad de 20 caracteres (tope
   `max_units`, 40 por defecto), suma `sfc`, toma el `ant` más antiguo y el
   primer `luso`, y anota en `raw` unidades expandidas, omitidas y si hubo
   truncado. Sin `sfc`, se suman las `stl` de las construcciones y se toma el
   `lcd` de la primera.
10. **Código 9 de la DGC se propaga como texto** (`error_code="9"`) tal como
    pide la spec; el lote lo cuenta como no encontrado y permite la cascada a
    geocoding mediante `CADASTRE_MISS_CODES`. En `rc_at_point` el código 16 se
    mapea a `NOT_FOUND`.
11. **Descripción foral.** Gipuzkoa y Navarra no publican dirección postal:
    la descripción se compone con el `localId` (o la referencia). Álava usa
    `INFO`; Bizkaia la clave `municipio-polígono-parcela`.
12. **Router.** Un único argumento posicional acepta código INE (2 o 5
    dígitos, texto o entero) o nombre; `ine_code` y `province_name` son las
    formas explícitas. Si el driver es la DGC y hay nombre de provincia pero
    no `dgc_territory`, el nombre se usa como `Provincia`.

## Geocoding

13. **Prefijo ruidoso como una sola expresión regular** que cubre «sita en
    la calle de», «barrio de», «avda.», «término municipal de»... Se aplica
    una vez. Si el resultado queda vacío se conserva el texto original.
14. **Clases desconocidas se etiquetan como `zone`** (conservador: no se
    persisten por defecto). Solo las siete clases concretas de la spec dan
    `exact`.
15. **`importance` se conserva como `confidence`** informativa; no decide.

## Imaginería

16. **Alias de ortofoto registrables** (`register_ortho_alias`) para
    infraestructuras regionales; `auto` equivale a `ign`.
17. **Mapillary: `BAD_RESPONSE`** para JSON no analizable o miniatura sin
    firma de imagen; `NET` para fallos de transporte.
18. **Ruta de imagen** `enrichment/<municipio>/<número de inventario>/<fuente>_<tipo>.<ext>`,
    con `municipality_id` (o la localidad) y el número de inventario pasados a
    *slug*.
19. **Enlaces Google** construidos con el esquema público de URLs de Maps,
    con seis decimales, sin clave ni descarga.

## Lotes y persistencia

20. **`limit` cuenta registros consultados**, no recorridos: los ya completos
    se saltan sin petición y no consumen el límite.
21. **Registros sin referencia ni dirección no cuentan como consultados**:
    van a la lista de pendientes con motivo.
22. **`would_write`** en las estadísticas para que el ensayo muestre cuántos
    registros cambiarían.
23. **Telemetría en la misma base SQLite** del inventario (tabla
    `batch_runs`), con `launched_by="cli"` o `"library"`; nunca el usuario del
    sistema operativo.
24. **Copia de seguridad con la API `backup` de SQLite** (snapshot
    consistente) con nombre `<db>.backup-<UTC>.<ext>`; falla si el destino ya
    existe o no se puede escribir, y la CLI aborta sin escribir.
25. **Subcomando extra `init-demo`** que crea un inventario sintético para
    poder reproducir el flujo completo sin datos reales.
26. **`--solo-coords`** se acepta como alias de `--only-coords` para respetar
    la opción nombrada en la spec sin romper la regla de identificadores en
    inglés.

## Datos sintéticos

27. **Referencias sintéticas con hoja `ZZ`** (`0000001ZZ0007S`): conservan la
    forma de 14 caracteres pero no corresponden a ninguna hoja real, de modo
    que un test que escapase a la red no acertaría una parcela existente.
28. **Los constructores de respuestas usan un namespace distinto del
    publicado** en el caso ArcGIS para ejercitar el análisis por nombre local.
