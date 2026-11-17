import io
import json

import pytest

from catastro_enrich import cli, synthetic
from catastro_enrich.storage import SqliteInventory
from catastro_enrich.transport import HttpResponse


def write_config(tmp_path, **overrides):
    values = {
        "ine_code": "28079",
        "province": "Madrid",
        "municipality": "Madrid",
        "municipality_id": "m-demo",
    }
    values.update(overrides)
    lines = ["[inventory]", 'database = "inventory.db"']
    lines += [f'{k} = "{v}"' for k, v in values.items()]
    lines += [
        "",
        "[images]",
        'root = "images"',
        "",
        "[services]",
        'contact = "https://example.invalid/contact"',
    ]
    path = tmp_path / "config.toml"
    path.write_text("\n".join(lines) + "\n")
    return path


def run(argv, transport=None):
    out = io.StringIO()
    code = cli.main(argv, transport=transport or synthetic.ScriptedTransport(), out=out)
    return code, out.getvalue()


def test_init_demo_and_runs_empty(tmp_path):
    cfg = write_config(tmp_path)
    code, text = run(["init-demo", "--config", str(cfg), "--count", "10"])
    assert code == 0 and "10 synthetic records" in text
    assert len(SqliteInventory(tmp_path / "inventory.db").load()) == 10
    assert run(["init-demo", "--config", str(cfg)])[0] == 2
    assert run(["init-demo", "--config", str(cfg), "--force"])[0] == 0
    code, text = run(["runs", "--config", str(cfg)])
    assert code == 0 and "(no runs recorded)" in text


def test_cadastre_dry_run_then_write_with_backup(tmp_path):
    cfg = write_config(tmp_path)
    run(["init-demo", "--config", str(cfg), "--count", "6", "--seed", "3"])
    transport = synthetic.ScriptedTransport()
    transport.add(
        "Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="CL DEMO 1", lon=-3.6, lat=40.7)
    )
    transport.add("search", HttpResponse(200, b"[]", {"content-type": "application/json"}))

    code, text = run(["cadastre", "--config", str(cfg), "--delay", "0"], transport)
    assert code == 0 and cli.DRY_RUN_NOTICE in text and "written" in text
    assert not list(tmp_path.glob("inventory.backup-*.db"))
    before = SqliteInventory(tmp_path / "inventory.db").load()
    assert all(r.lat is None for r in before if r.cadastral_reference and not r.has_coordinates)

    code, text = run(["cadastre", "--config", str(cfg), "--write", "--delay", "0"], transport)
    assert code == 0 and cli.DRY_RUN_NOTICE not in text and "backup:" in text
    assert len(list(tmp_path.glob("inventory.backup-*.db"))) == 1
    after = SqliteInventory(tmp_path / "inventory.db").load()
    assert all(r.has_coordinates for r in after if r.cadastral_reference)

    code, text = run(["runs", "--config", str(cfg)], transport)
    assert code == 0 and text.count("cadastre") == 2 and "yes" in text and "no" in text


def test_geocoding_accept_zone_and_only_coords_flags(tmp_path):
    cfg = write_config(tmp_path)
    run(["init-demo", "--config", str(cfg), "--count", "4", "--seed", "5"])
    hits = [synthetic.nominatim_hit(lat=41.0, lon=-1.0, place_class="place", place_type="suburb")]
    transport = synthetic.ScriptedTransport()
    transport.add(
        "search", HttpResponse(200, json.dumps(hits).encode(), {"content-type": "application/json"})
    )
    code, text = run(["geocoding", "--config", str(cfg), "--delay", "0"], transport)
    assert code == 0 and "zone-level" in text
    code, text = run(
        ["geocoding", "--config", str(cfg), "--delay", "0", "--accept-zone", "--write"], transport
    )
    assert code == 0 and "backup:" in text
    parser = cli.build_parser()
    args = parser.parse_args(["cadastre", "--config", "x", "--solo-coords"])
    assert args.only_coords is True


def test_physical_uses_dnprc(tmp_path):
    cfg = write_config(tmp_path)
    run(["init-demo", "--config", str(cfg), "--count", "3", "--seed", "9"])
    transport = synthetic.ScriptedTransport()
    transport.add(
        "Consulta_DNPRC",
        synthetic.dgc_physical_xml([{"sfc": 120, "ant": 1966, "luso": "Oficinas"}]),
    )
    code, _ = run(["physical", "--config", str(cfg), "--write", "--delay", "0"], transport)
    assert code == 0
    records = SqliteInventory(tmp_path / "inventory.db").load()
    assert all(r.built_area_m2 for r in records if r.cadastral_reference)
    assert all("Consulta_DNPRC" in u for u in transport.urls())


def test_imagery_command_writes_under_images_root(tmp_path):
    cfg = write_config(tmp_path)
    run(["init-demo", "--config", str(cfg), "--count", "5", "--seed", "2"])
    transport = synthetic.ScriptedTransport()
    transport.add(
        "pnoa-ma", HttpResponse(200, synthetic.tiny_jpeg(4), {"content-type": "image/jpeg"})
    )
    code, text = run(["imagery", "--config", str(cfg), "--no-mapillary"], transport)
    assert code == 0 and cli.DRY_RUN_NOTICE in text
    assert not (tmp_path / "images").exists()
    code, text = run(["imagery", "--config", str(cfg), "--no-mapillary", "--write"], transport)
    assert code == 0
    written = list((tmp_path / "images" / "enrichment").rglob("*.jpg"))
    assert len(written) >= 1
    assert SqliteInventory(tmp_path / "inventory.db").images()
    assert transport.requests[0].params["WIDTH"] == "512"
    assert run(["imagery", "--config", str(cfg), "--ortho", "nope"], transport)[0] == 2


def test_imagery_token_from_env(tmp_path, monkeypatch):
    cfg = write_config(tmp_path)
    run(["init-demo", "--config", str(cfg), "--count", "3", "--seed", "2"])
    monkeypatch.setenv("MAPILLARY_TOKEN", "synthetic-token")
    transport = synthetic.ScriptedTransport()
    transport.add(
        "graph.mapillary.com",
        HttpResponse(200, b'{"data": []}', {"content-type": "application/json"}),
    )
    code, text = run(["imagery", "--config", str(cfg), "--ortho", "none"], transport)
    assert code == 0 and "no coverage" in text
    assert all(r.params.get("access_token") == "synthetic-token" for r in transport.requests)


def test_rc_at_point(tmp_path):
    rc = synthetic.synthetic_rc(3)
    transport = synthetic.ScriptedTransport()
    transport.add(
        "Consulta_RCCOOR", synthetic.dgc_point_xml(pc1=rc[:7], pc2=rc[7:], ldt="CL PUNTO")
    )
    code, text = run(["rc-at-point", "40.7", "-3.6", "--delay", "0"], transport)
    assert code == 0 and rc in text
    transport.rules.clear()
    transport.add("Consulta_RCCOOR", synthetic.dgc_error_xml(16, "no hay referencia"))
    code, text = run(
        ["rc-at-point", "40.7", "-3.6", "--config", str(write_config(tmp_path)), "--delay", "0"],
        transport,
    )
    assert code == 1 and "NOT_FOUND" in text


def test_backup_failure_aborts_write(tmp_path, monkeypatch):
    cfg = write_config(tmp_path)
    run(["init-demo", "--config", str(cfg), "--count", "2"])

    def broken(self, *a, **k):
        raise cli.BackupError("simulated")

    monkeypatch.setattr(SqliteInventory, "backup", broken)
    code, text = run(["cadastre", "--config", str(cfg), "--write"])
    assert code == 2 and "ABORT" in text


def test_version_flag():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
