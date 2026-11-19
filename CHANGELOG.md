# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-19

### Added

- Cadastre drivers behind one `CadastreDriver` interface: DGC (OVC XML and
  INSPIRE WFS), Gipuzkoa, Navarra, Álava and Bizkaia, with a router by INE
  province code and province-name synonyms.
- DGC extras: reference at a point, references by address, parcel area and
  polygon (WKT), physical data with unit expansion and aggregation.
- Fallback geocoding with Nominatim; precision by OSM class
  (`exact` / `zone` / `administrative`); address normalisation.
- UTM zones 28–31 ↔ WGS84 conversions, ring centroid, WMS bounding boxes.
- Imagery: generic WMS orthophoto driver (PNOA by default, regional aliases),
  Mapillary street views, Google live links, `ImageStore` with SHA-256
  de-duplication.
- Enrichment cascade and idempotent batches: dry run by default, additive
  writes, `overwrite`, limit, guarded progress callback, counters.
- Telemetry (`MemoryRunLog`, `SqliteRunLog`) and a reference SQLite adapter
  with timestamped backups.
- `catastro-enrich` CLI: `cadastre`, `physical`, `geocoding`, `imagery`,
  `rc-at-point`, `runs`, `init-demo`.
- Synthetic data generators and offline `ScriptedTransport` for tests.
- Project skeleton: packaging metadata, MIT licence, CI (tests on Python 3.11
  and 3.12, `ruff`, `gitleaks`) and the leak test that guards the clean-room
  rules.
- Bilingual case-study README (`README.md`, `README.es.md`) and design notes
  in `docs/`.
- Packaging for PyPI: distribution name `catastro-enrich` (import stays
  `catastro_enrich`), SPDX licence expression, `publish.yml` with trusted
  publishing, absolute README links.
