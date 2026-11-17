"""Command-line interface.

Every batch command is a dry run unless ``--write`` is given; with
``--write`` a timestamped backup of the database is taken first and the run
is recorded in the ``batch_runs`` table of the same database.

Configuration (TOML)::

    [inventory]
    database = "inventory.db"     # relative to the config file
    municipality_id = "m-demo"
    ine_code = "28079"            # decides the cadastre driver
    province = "Madrid"           # DGC "Provincia" and geocoding context
    municipality = "Madrid"       # DGC "Municipio"

    [images]
    root = "."                    # images go under <root>/enrichment/...

    [services]
    contact = "https://example.org/contact"   # appended to the User-Agent
    mapillary_token_env = "MAPILLARY_TOKEN"
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .cadastre import DgcDriver, DgcTerritory, resolve_cadastre_driver
from .enrichment import enrich_batch, enrich_imagery_batch
from .geocoding import NominatimGeocoder
from .imagery import ImageStore, MapillaryDriver, ortho_driver
from .models import RunStats
from .storage import BackupError, SqliteInventory
from .synthetic import synthetic_records
from .telemetry import SqliteRunLog
from .transport import Transport, UrllibTransport, default_user_agent

DRY_RUN_NOTICE = "DRY RUN - ensayo: nada escrito (add --write to persist)"


@dataclass
class Config:
    database: Path
    municipality_id: str = ""
    ine_code: str | None = None
    province: str | None = None
    municipality: str | None = None
    images_root: Path = Path(".")
    contact: str = ""
    mapillary_token_env: str = "MAPILLARY_TOKEN"
    nominatim_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        path = Path(path)
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        inv = data.get("inventory", {})
        images = data.get("images", {})
        services = data.get("services", {})
        base = path.parent
        return cls(
            database=base / inv.get("database", "inventory.db"),
            municipality_id=str(inv.get("municipality_id", "")),
            ine_code=str(inv["ine_code"]) if inv.get("ine_code") is not None else None,
            province=inv.get("province"),
            municipality=inv.get("municipality"),
            images_root=base / images.get("root", "."),
            contact=str(services.get("contact", "")),
            mapillary_token_env=str(services.get("mapillary_token_env", "MAPILLARY_TOKEN")),
            nominatim_url=services.get("nominatim_url"),
            raw=data,
        )


@dataclass
class Services:
    """Factories bound to one config and one transport (injectable in tests)."""

    config: Config
    transport: Transport

    def cadastre(self, delay: float | None):
        territory = DgcTerritory(self.config.province or "", self.config.municipality or "")
        return resolve_cadastre_driver(
            ine_code=self.config.ine_code,
            province_name=self.config.province,
            dgc_territory=territory,
            delay=delay,
            transport=self.transport,
        )

    def geocoder(self, delay: float | None) -> NominatimGeocoder:
        kwargs: dict[str, Any] = {"transport": self.transport}
        if delay is not None:
            kwargs["delay"] = delay
        if self.config.nominatim_url:
            kwargs["url"] = self.config.nominatim_url
        return NominatimGeocoder(**kwargs)


def _print_stats(stats: RunStats, out) -> None:
    rows = [
        ("kind", stats.kind),
        ("driver", stats.driver),
        ("municipality", stats.municipality_id or "-"),
        ("queried", stats.queried),
        ("ok", stats.ok),
        ("errors", stats.errors),
        ("written", stats.written),
        ("would write", stats.would_write),
        ("skipped (existing)", stats.skipped_existing),
        ("not found", stats.not_found),
        ("invalid", stats.invalid),
        ("network", stats.net),
        ("low confidence", stats.low_confidence),
        ("zone-level", stats.zone),
        ("unbuilt", stats.unbuilt),
        ("no coverage", stats.no_coverage),
        ("duplicates", stats.duplicates),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label:<{width}}  {value}", file=out)
    if stats.per_source:
        print(
            "per source        " + ", ".join(f"{k}={v}" for k, v in stats.per_source.items()),
            file=out,
        )
    if stats.pending:
        print("pending:", file=out)
        for pid, key, reason in stats.pending:
            print(f"  {pid}  {key or '-'}  {reason}", file=out)
    if stats.dry_run:
        print(DRY_RUN_NOTICE, file=out)


def _prepare_write(db: SqliteInventory, write: bool, out) -> bool:
    if not write:
        return True
    try:
        snapshot = db.backup()
    except BackupError as exc:
        print(f"ABORT: backup failed, nothing written ({exc})", file=out)
        return False
    print(f"backup: {snapshot}", file=out)
    return True


def _common_batch(args: argparse.Namespace, services: Services, out) -> int:
    config = services.config
    db = SqliteInventory(config.database)
    records = db.load(config.municipality_id or None)
    if not _prepare_write(db, args.write, out):
        return 2
    telemetry = SqliteRunLog(config.database)
    kind = args.command
    stats = enrich_batch(
        records,
        cadastre=services.cadastre(args.delay) if kind != "geocoding" else None,
        geocoder=services.geocoder(args.delay)
        if kind != "physical" and not args.no_geocoding
        else None,
        kind=kind,
        write=args.write,
        overwrite=args.overwrite,
        limit=args.limit,
        telemetry=telemetry,
        apply=db.apply,
        accept_zone=getattr(args, "accept_zone", False),
        only_coords=getattr(args, "only_coords", False),
        province=config.province,
        municipality_id=config.municipality_id,
        launched_by="cli",
    )
    _print_stats(stats, out)
    return 0


def _imagery(args: argparse.Namespace, services: Services, out) -> int:
    config = services.config
    db = SqliteInventory(config.database)
    records = db.load(config.municipality_id or None)
    if not _prepare_write(db, args.write, out):
        return 2
    drivers: list[Any] = []
    try:
        ortho = ortho_driver(
            args.ortho, transport=services.transport, span_m=args.span_m, delay=args.delay
        )
    except KeyError:
        print(f"unknown orthophoto alias: {args.ortho}", file=out)
        return 2
    if ortho is not None:
        drivers.append(ortho)
    token = args.mapillary_token or os.environ.get(config.mapillary_token_env, "")
    if not args.no_mapillary:
        drivers.append(MapillaryDriver(token, transport=services.transport, delay=args.delay))
    store = ImageStore(config.images_root, existing=db.images())
    stats = enrich_imagery_batch(
        records,
        drivers=drivers,
        store=store,
        write=args.write,
        overwrite=args.overwrite,
        limit=args.limit,
        telemetry=SqliteRunLog(config.database),
        on_image=lambda record, doc: db.add_image(doc),
        municipality_id=config.municipality_id,
        launched_by="cli",
    )
    _print_stats(stats, out)
    return 0


def _rc_at_point(
    args: argparse.Namespace, services: Services | None, transport: Transport, out
) -> int:
    territory = None
    if services is not None:
        territory = DgcTerritory(services.config.province or "", services.config.municipality or "")
    driver = DgcDriver(territory, transport=transport, delay=args.delay)
    lookup = driver.rc_at_point(args.lat, args.lon)
    if lookup.ok:
        print(f"rc           {lookup.rc}", file=out)
        print(f"description  {lookup.description}", file=out)
        return 0
    print(f"error        {lookup.error_code}  {lookup.description}".rstrip(), file=out)
    return 1


def _runs(args: argparse.Namespace, services: Services, out) -> int:
    log = SqliteRunLog(services.config.database)
    runs = log.list_runs(args.municipality or (services.config.municipality_id or None), args.limit)
    columns = ("id", "started_at", "kind", "driver", "q", "ok", "err", "wr", "dry")
    widths = (4, 20, 10, 18, 5, 5, 5, 5, 3)
    print("  ".join(f"{c:<{w}}" for c, w in zip(columns, widths, strict=True)), file=out)
    for run in runs:
        values = (
            run.id,
            run.started_at,
            run.kind,
            run.driver[:18],
            run.queried,
            run.ok,
            run.errors,
            run.written,
            "yes" if run.dry_run else "no",
        )
        print("  ".join(f"{v!s:<{w}}" for v, w in zip(values, widths, strict=True)), file=out)
    if not runs:
        print("(no runs recorded)", file=out)
    return 0


def _init_demo(args: argparse.Namespace, services: Services, out) -> int:
    config = services.config
    if config.database.exists() and not args.force:
        print(f"refusing to overwrite existing database {config.database} (use --force)", file=out)
        return 2
    if config.database.exists():
        config.database.unlink()
    db = SqliteInventory(config.database)
    count = db.insert(
        synthetic_records(
            args.count, seed=args.seed, municipality_id=config.municipality_id or "m-demo"
        )
    )
    print(f"created {config.database} with {count} synthetic records", file=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catastro-enrich",
        description="Enrich a property inventory with Spanish cadastres, geocoding and imagery.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, batch: bool = True) -> None:
        p.add_argument("--config", required=True, help="TOML configuration file")
        if batch:
            p.add_argument("--limit", type=int, default=None, help="max records to query")
            p.add_argument(
                "--write", action="store_true", help="persist changes (default: dry run)"
            )
            p.add_argument("--overwrite", action="store_true", help="overwrite filled fields")
        p.add_argument("--delay", type=float, default=None, help="seconds between requests")

    cadastre = sub.add_parser("cadastre", help="coordinates and official street from the cadastre")
    common(cadastre)
    cadastre.add_argument(
        "--only-coords",
        "--solo-coords",
        dest="only_coords",
        action="store_true",
        help="never touch the street",
    )
    cadastre.add_argument(
        "--no-geocoding", action="store_true", help="disable the geocoding fallback"
    )
    cadastre.add_argument(
        "--accept-zone", action="store_true", help="persist zone-level geocoding hits"
    )

    physical = sub.add_parser("physical", help="built area, construction year and use (DGC only)")
    common(physical)
    physical.set_defaults(no_geocoding=True)

    geocoding = sub.add_parser("geocoding", help="coordinates from the address (Nominatim)")
    common(geocoding)
    geocoding.add_argument("--accept-zone", action="store_true", help="persist zone-level hits")
    geocoding.set_defaults(no_geocoding=False)

    imagery = sub.add_parser("imagery", help="orthophoto and street view per property")
    common(imagery)
    imagery.add_argument("--ortho", default="auto", help="auto | ign | <alias> | none")
    imagery.add_argument("--mapillary-token", default="", help="or the env var from the config")
    imagery.add_argument("--no-mapillary", action="store_true")
    imagery.add_argument("--span-m", type=float, default=80.0, help="side of the orthophoto crop")
    imagery.set_defaults(delay=0.2)

    point = sub.add_parser("rc-at-point", help="cadastral reference at a WGS84 point (DGC)")
    point.add_argument("lat", type=float)
    point.add_argument("lon", type=float)
    point.add_argument("--config", default=None)
    point.add_argument("--delay", type=float, default=None)

    runs = sub.add_parser("runs", help="telemetry of previous batches, most recent first")
    runs.add_argument("--config", required=True)
    runs.add_argument("--municipality", default=None)
    runs.add_argument("--limit", type=int, default=20)

    demo = sub.add_parser(
        "init-demo", help="create a synthetic inventory database for trying the tool"
    )
    demo.add_argument("--config", required=True)
    demo.add_argument("--count", type=int, default=25)
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, transport: Transport | None = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    config = Config.load(args.config) if args.config else None
    if transport is None:
        transport = UrllibTransport(default_user_agent(config.contact if config else None))
    services = Services(config, transport) if config else None
    cadastre_default_delay = getattr(args, "delay", None)
    if args.command == "rc-at-point":
        return _rc_at_point(args, services, transport, out)
    assert services is not None
    if args.command in ("cadastre", "physical", "geocoding"):
        args.delay = cadastre_default_delay
        return _common_batch(args, services, out)
    if args.command == "imagery":
        return _imagery(args, services, out)
    if args.command == "runs":
        return _runs(args, services, out)
    if args.command == "init-demo":
        return _init_demo(args, services, out)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
