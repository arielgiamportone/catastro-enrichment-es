# catastro-enrichment-es

[![CI](https://github.com/arielgiamportone/catastro-enrichment-es/actions/workflows/ci.yml/badge.svg)](https://github.com/arielgiamportone/catastro-enrichment-es/actions/workflows/ci.yml)
![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)

Enrich a Spanish property inventory with **WGS84 coordinates, the official
address, built area, construction year, cadastral use and a location image**,
using only free public services: the five Spanish cadastres behind one
interface, Nominatim as fallback geocoder, PNOA orthophotos and Mapillary
street views stored with their attribution, and Google as live links only.

*Versión en español: [README.es.md](README.es.md).*

> Patterns developed during my work at Intervalor Data and published with permission. Independent clean-room implementation with synthetic data; it contains no code or data from the company or its clients.

## The problem

Asset inventories of public bodies and property portfolios arrive incomplete:
cadastral references without coordinates, free-text addresses without a
reference, empty areas and years, no pictures. Completing them by hand is
slow and error-prone, and Spain does not have one cadastre but **five**: the
Dirección General del Catastro (DGC) for the common regime, plus the foral
cadastres of Álava, Bizkaia, Gipuzkoa and Navarra, each with its own
protocol, reference format, filter dialect and coordinate system.

This library gives a single contract (`lookup_rc`, `physical_data`,
`batch`) for every territory, falls back to geocoding when the cadastre
cannot resolve a property, and runs **idempotent, dry-run-by-default
batches** with telemetry so that an operator can re-run a pass safely.

## Constraints

| Constraint | Consequence |
|---|---|
| Free, public, unauthenticated services (Mapillary needs a free token) | Fair-use delays per service, identifiable `User-Agent` with contact, no retries on HTTP 429 |
| Business errors arrive with HTTP 200 | Every driver parses the body and returns **error codes as data**; a batch never aborts because of one property |
| Inconsistent XML namespaces (ArcGIS Server) | All XML is parsed by local tag name |
| Licences differ per source | Store PNOA and Mapillary with attribution; Google is link-only |
| Inventories are owned by the caller | The library receives and returns records; a reference SQLite adapter shows how to persist diffs, images and runs |
| Operators must be able to re-run passes | Additive writes, backup before every write, explicit dry run |
| Public case study | Clean-room implementation, synthetic data only, leak test in CI |

## Architecture

```
            PropertyRecord (id, reference, street, locality, lat, lon, ...)
                                   |
                       enrich_batch / enrich_property
                                   |
      +----------------------------+-----------------------------+
      |                            |                             |
 resolve_cadastre_driver     geocode_address               fetch_imagery
 (INE province -> driver)    (Nominatim, precision         (WMS ortho,
      |                       by OSM class)                 Mapillary,
      |                                                     live_links)
 +----+----+----------+----------+----------+                    |
 | DGC     | Gipuzkoa | Navarra  | Álava    | Bizkaia  |      ImageStore
 | OVC XML | INSPIRE  | INSPIRE  | ArcGIS   | ArcGIS   |   (SHA-256 dedup,
 | +INSPIRE| stored q.| cql_filt.| FES XML  | FES XML  |    one per source)
 +---------+----------+----------+----------+----------+
                                   |
                     Transport (stdlib urllib, injectable) + Pacer
```

| Module | Responsibility |
|---|---|
| `catastro_enrich.cadastre` | `CadastreDriver` ABC, five drivers, `resolve_cadastre_driver` router (INE code wins over province name, synonyms, DGC fallback) |
| `catastro_enrich.geocoding` | Address normalisation, Nominatim client, hit selection by OSM class with `exact` / `zone` / `administrative` precision |
| `catastro_enrich.geo` | UTM zones 28–31 ↔ WGS84 with cached transformers, ring centroid, axis-order check, WMS bounding boxes |
| `catastro_enrich.imagery` | Generic WMS orthophoto driver (PNOA by default, regional aliases), Mapillary, Google live links, `ImageStore` |
| `catastro_enrich.enrichment` | Cascade, additive diffs, batches with counters, progress callback and telemetry |
| `catastro_enrich.telemetry` | One row per run (`batch_runs`), guarded so a telemetry failure never stops a batch |
| `catastro_enrich.storage` | Reference SQLite adapter: load records, apply diffs, image metadata, timestamped backup |
| `catastro_enrich.synthetic` | Generators of records, artificial references and service responses used by the tests |
| `catastro_enrich.cli` | `catastro-enrich` command |

**Cascade for coordinates.** Reference present → cadastre of the territory.
Reference unknown or invalid and address present → geocoding. Nothing
resolves → pending with a reason. Geocoding never overwrites a cadastral
coordinate, and only `exact` hits are persisted unless `--accept-zone`.

## Decisions and discarded alternatives

| Decision | Chosen | Discarded | Why |
|---|---|---|---|
| Multi-cadastre | Abstract driver + router by INE province | One parametrised client | ArcGIS ignores `cql_filter`; INSPIRE services have no `REF_CATASTRAL`; axis order and reference formats differ |
| Geocoder | Nominatim (ODbL, 1 req/s, own server configurable) | Google Geocoding | Cost, prohibition on storing results, sending public data to a commercial third party |
| Geocoding confidence | OSM class, with precision labels | `importance` threshold | Importance measures global relevance; rural streets score 0.05–0.10 and would be dropped |
| Zone-level hits | Not persisted by default | Accept `place`/`landuse` | A neighbourhood centroid is not the property |
| Imagery | Store PNOA/Mapillary, link Google | Link everything / store everything | Offline portability versus licence terms |
| Parcel centroid | Arithmetic mean of the exterior ring | Area-weighted centroid | Enough to place a parcel, no geometry dependency |
| WMS | 1.3.0 with inverted axes, 1.1.1 supported | 1.1.1 only | The national service publishes 1.3.0 |
| Physical data for a parcel | Expand the unit list and aggregate (sum of areas, oldest year) | Take the first unit | Verified live: a parcel answers with its units and no areas |
| HTTP | Standard library with an injectable transport | `requests` / `httpx` | Zero runtime dependencies besides `pyproj`; tests run fully offline |
| Errors | Codes in the result object | Exceptions | A batch must count and continue |

Further decisions taken during implementation are listed in
[docs/DECISIONES.md](docs/DECISIONES.md) (Spanish).

## What was measured and how to reproduce it

Everything below runs offline; the test suite blocks network access.

| Measure | Value | Command |
|---|---|---|
| Tests | 139 passed, about 1.3 s | `pytest` |
| Line coverage | 97 % (`pip install pytest-cov`) | `pytest --cov=catastro_enrich` |
| Lint and format | clean | `ruff check . && ruff format --check .` |
| Leak guard | tree, file names and `git log -p` clean | `pytest tests/test_no_leaks.py` |
| Round trip UTM ↔ WGS84 | error < 1e-6° and < 0.1 m in zones 28–31 | `pytest tests/test_geo.py` |

The public services were exercised once by hand during development (point →
reference, reference → coordinates, physical data with unit expansion, the
four foral cadastres, Nominatim, PNOA). Nothing from those calls is stored in
the repository.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```

## Quick start

Create a configuration file and a synthetic inventory, then run a dry pass:

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

The summary ends with `DRY RUN - ensayo: nada escrito`; add `--write` to
persist (a timestamped backup is taken first). Other commands: `physical`,
`geocoding --accept-zone`, `imagery --ortho auto|ign|<alias>|none
--mapillary-token ... --span-m 80`, `rc-at-point LAT LON`, `runs`.

As a library:

```python
from catastro_enrich import PropertyRecord, enrich_batch, resolve_cadastre_driver

cadastre = resolve_cadastre_driver("20069")  # Gipuzkoa by INE code
records = [PropertyRecord(id="1", cadastral_reference="069-REF-3232")]
stats = enrich_batch(records, cadastre=cadastre)  # dry run: nothing written
print(stats.ok, stats.not_found, stats.would_write)
```

Synthetic references from `catastro_enrich.synthetic` use an artificial
sheet code and will not resolve on the real services; they exist to exercise
the code paths offline.

## Known limits

- **Physical data only from the DGC.** Foral drivers return `NOT_SUPPORTED`.
- **Foral layers and fields** were read from the public capabilities of each
  service in November 2026; they are constructor arguments so they can be
  changed without a release if a service renames them.
- **DGC province and municipality** must use the official INE spelling in
  upper case; the config allows overriding both.
- **Parcel expansion** queries one request per unit (capped by `max_units`,
  40 by default) and is therefore slow on large blocks.
- **Nominatim public instance** is for light use only (≤ 1 request/s,
  hundreds of properties per municipality); point `services.nominatim_url`
  at your own instance for anything larger.
- **Arithmetic centroid**, not area-weighted: fine for placing a parcel, not
  for geometry work.
- **Street views may show people or plates**; Mapillary blurs them, but
  review before publishing.
- No Land Registry, no full parcel downloads, no paid geocoders.

## Compliance and attribution of sources

| Source | Licence | Attribution used |
|---|---|---|
| DGC (Sede Electrónica del Catastro) | Open data, non-massive identified use | "Dirección General del Catastro" |
| Foral cadastres (Álava, Bizkaia, Gipuzkoa, Navarra) | CC-BY / CC-BY 4.0 | Each Diputación Foral / Gobierno de Navarra |
| PNOA (IGN) | CC-BY 4.0 | "PNOA © Instituto Geográfico Nacional" |
| Mapillary | CC-BY-SA | "© Mapillary contributors" plus image id and capture date |
| Nominatim / OSM | ODbL, OSMF usage policy | "© OpenStreetMap contributors" |
| Google Maps | Terms forbid caching | Live links only |

Addresses and references of public buildings are not personal data; applied
to private portfolios they may be (reference plus owner). The library only
transmits references, addresses and coordinates.

## How it was built

Three-phase clean-room process: a written specification was produced from
the original work without code; this repository was implemented in a
separate session from that specification and the public documentation of
the services, with synthetic data only; a third review compares both for
similarity and leaks before publication. A leak test with an obfuscated word
list runs in CI alongside `gitleaks`.

## License

MIT. See [LICENSE](LICENSE) and [CHANGELOG.md](CHANGELOG.md).
