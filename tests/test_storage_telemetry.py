from datetime import UTC, datetime, timedelta

import pytest

from catastro_enrich import synthetic
from catastro_enrich.models import EnrichmentDiff, ImageDocument, RunStats
from catastro_enrich.storage import BackupError, SqliteInventory
from catastro_enrich.telemetry import MemoryRunLog, SqliteRunLog, guarded_close, guarded_open


class Ticker:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self):
        self.now += timedelta(seconds=1.5)
        return self.now


@pytest.mark.parametrize(
    "factory",
    [lambda p: MemoryRunLog(clock=Ticker()), lambda p: SqliteRunLog(p / "runs.db", clock=Ticker())],
)
def test_run_log_opens_closes_filters_and_orders(tmp_path, factory):
    log = factory(tmp_path)
    first = log.open_run(
        municipality_id="m1", kind="cadastre", driver="dgc", dry_run=True, launched_by="tests"
    )
    second = log.open_run(
        municipality_id="m2", kind="physical", driver="dgc", dry_run=False, launched_by="tests"
    )
    third = log.open_run(
        municipality_id="m1", kind="imagery", driver="ign_pnoa", dry_run=True, launched_by="tests"
    )
    stats = RunStats(queried=5, ok=3, errors=1, written=2, not_found=1, per_source={"dgc": 5})
    for run_id in (first, second, third):
        log.close_run(run_id, stats)
    runs = log.list_runs("m1")
    assert [r.id for r in runs] == [third, first]
    assert all(r.closed and r.duration_s > 0 for r in runs)
    assert runs[0].extra["not_found"] == 1 and runs[0].extra["per_source"] == {"dgc": 5}
    assert runs[0].started_at.endswith("Z") and runs[0].finished_at > runs[0].started_at
    assert len(log.list_runs()) == 3 and len(log.list_runs(limit=1)) == 1
    assert log.list_runs("m2")[0].dry_run is False and log.list_runs("m2")[0].launched_by == "tests"


def test_sqlite_run_log_survives_reopen(tmp_path):
    path = tmp_path / "runs.db"
    run_id = SqliteRunLog(path).open_run(
        municipality_id="m", kind="cadastre", driver="dgc", dry_run=True, launched_by="x"
    )
    SqliteRunLog(path).close_run(run_id, RunStats(queried=1))
    run = SqliteRunLog(path).list_runs()[0]
    assert run.closed and run.queried == 1 and run.duration_s >= 0


def test_guarded_helpers_swallow_errors():
    class Broken:
        def open_run(self, **kw):
            raise RuntimeError("x")

        def close_run(self, run_id, stats):
            raise RuntimeError("x")

    assert (
        guarded_open(
            Broken(), municipality_id="m", kind="k", driver="d", dry_run=True, launched_by="l"
        )
        is None
    )
    guarded_close(Broken(), 1, RunStats())
    assert guarded_open(None) is None


def test_sqlite_inventory_round_trip_apply_and_images(tmp_path):
    db = SqliteInventory(tmp_path / "inventory.db")
    records = synthetic.synthetic_records(5, seed=11, municipality_id="m-a")
    records[0].municipality_id = "m-b"
    assert db.insert(records) == 5
    assert len(db.load()) == 5 and len(db.load("m-a")) == 4
    loaded = db.get(records[1].id)
    assert loaded == records[1]

    diff = EnrichmentDiff(
        records[1].id, "ok", {"lat": 40.5, "lon": -3.5, "street": "CL NUEVA 1", "ignored": 1}
    )
    assert db.apply(records[1], diff) == 3
    after = db.get(records[1].id)
    assert (after.lat, after.lon, after.street) == (40.5, -3.5, "CL NUEVA 1")
    assert db.apply(records[1], EnrichmentDiff(records[1].id, "ok", {})) == 0

    doc = ImageDocument(
        records[1].id,
        "a.jpg",
        "enrichment/m-a/1/ign_pnoa_ortho.jpg",
        "image/jpeg",
        "ff",
        2,
        "ign_pnoa",
        "ORTHO",
        "PNOA",
        None,
    )
    db.add_image(doc)
    db.add_image(doc)
    assert db.images() == [doc] and db.images(records[1].id) == [doc] and db.images("nope") == []


def test_backup_creates_timestamped_snapshot_and_fails_loudly(tmp_path):
    db = SqliteInventory(tmp_path / "inventory.db")
    db.insert(synthetic.synthetic_records(2))
    when = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    snapshot = db.backup(now=when)
    assert snapshot.name == "inventory.backup-20260304T050607Z.db"
    assert len(SqliteInventory(snapshot).load()) == 2
    with pytest.raises(BackupError):
        db.backup(now=when)
    with pytest.raises(BackupError):
        db.backup(tmp_path / "inventory.db" / "impossible", now=when)
