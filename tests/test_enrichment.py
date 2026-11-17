import json

from catastro_enrich import synthetic
from catastro_enrich.cadastre import DgcDriver
from catastro_enrich.enrichment import (
    STATUS_LOW_CONFIDENCE,
    STATUS_NET,
    STATUS_OK,
    STATUS_PENDING,
    STATUS_SKIPPED,
    STATUS_UNBUILT,
    STATUS_ZONE,
    enrich_batch,
    enrich_imagery_batch,
    enrich_property,
    physical_enrichment,
)
from catastro_enrich.geocoding import NominatimGeocoder
from catastro_enrich.imagery import ImageStore, MapillaryDriver, ign_pnoa_driver
from catastro_enrich.models import NOT_SUPPORTED, PropertyRecord
from catastro_enrich.telemetry import MemoryRunLog
from catastro_enrich.transport import HttpResponse, Pacer

NO_WAIT = Pacer(0, sleep=lambda s: None)
RC1, RC2 = synthetic.synthetic_rc(1), synthetic.synthetic_rc(2)


def dgc(transport):
    return DgcDriver(transport=transport, pacer=NO_WAIT)


def nominatim(transport):
    return NominatimGeocoder(transport=transport, pacer=NO_WAIT)


def hits_json(hits):
    return HttpResponse(200, json.dumps(hits).encode(), {"content-type": "application/json"})


def three_records():
    return [
        PropertyRecord(
            id="a", inventory_number="1", cadastral_reference=RC1, street=None, locality="Aldea"
        ),
        PropertyRecord(
            id="b",
            inventory_number="2",
            cadastral_reference=RC2,
            lat=40.0,
            lon=-3.0,
            street="Calle Vieja 1",
        ),
        PropertyRecord(id="c", inventory_number="3", cadastral_reference=None, street=None),
    ]


# --- cascade ------------------------------------------------------------------


def test_cadastre_hit_fills_coords_and_empty_street(transport):
    transport.add(
        "Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="CL OFICIAL 5", lon=-3.6, lat=40.7)
    )
    rec = PropertyRecord(id="x", cadastral_reference=RC1)
    diff = enrich_property(rec, cadastre=dgc(transport))
    assert diff.status == STATUS_OK and diff.changes == {
        "lat": 40.7,
        "lon": -3.6,
        "street": "CL OFICIAL 5",
    }


def test_street_is_not_overwritten_and_only_coords_flag(transport):
    transport.add(
        "Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="CL OFICIAL 5", lon=-3.6, lat=40.7)
    )
    rec = PropertyRecord(id="x", cadastral_reference=RC1, street="Mi calle 1")
    assert "street" not in enrich_property(rec, cadastre=dgc(transport)).changes
    empty = PropertyRecord(id="y", cadastral_reference=RC1, street="")
    assert "street" not in enrich_property(empty, cadastre=dgc(transport), only_coords=True).changes


def test_existing_coordinates_are_skipped_without_query(transport):
    rec = PropertyRecord(id="x", cadastral_reference=RC1, lat=1.0, lon=1.0)
    assert enrich_property(rec, cadastre=dgc(transport)).status == STATUS_SKIPPED
    assert transport.calls == 0


def test_not_found_rc_falls_back_to_geocoding(transport):
    transport.add("Consulta_CPMRC", synthetic.dgc_error_xml(9, "no existe"))
    transport.add(
        "search",
        hits_json(
            [
                synthetic.nominatim_hit(
                    lat=41.0, lon=-1.0, place_class="highway", place_type="residential"
                )
            ]
        ),
    )
    rec = PropertyRecord(id="x", cadastral_reference=RC1, street="Calle Mayor 2", locality="Aldea")
    diff = enrich_property(
        rec, cadastre=dgc(transport), geocoder=nominatim(transport), province="Provincia"
    )
    assert diff.status == STATUS_OK and diff.changes == {"lat": 41.0, "lon": -1.0}
    assert diff.source == "nominatim" and diff.precision == "exact" and "cadastre 9" in diff.reason
    assert transport.requests[1].params["q"] == "Mayor 2, Aldea, Provincia, Spain"


def test_net_error_does_not_fall_back(transport):
    transport.add("Consulta_CPMRC", None)
    rec = PropertyRecord(id="x", cadastral_reference=RC1, street="Calle Mayor 2")
    diff = enrich_property(rec, cadastre=dgc(transport), geocoder=nominatim(transport))
    assert diff.status == STATUS_NET and transport.calls == 1


def test_zone_hit_not_persisted_unless_accepted(transport):
    transport.add(
        "search",
        hits_json(
            [synthetic.nominatim_hit(lat=41.0, lon=-1.0, place_class="place", place_type="suburb")]
        ),
    )
    rec = PropertyRecord(id="x", street="Barrio de Arriba")
    diff = enrich_property(rec, cadastre=None, geocoder=nominatim(transport))
    assert diff.status == STATUS_ZONE and not diff.changes and diff.precision == "zone"
    diff = enrich_property(rec, cadastre=None, geocoder=nominatim(transport), accept_zone=True)
    assert diff.status == STATUS_OK and diff.changes == {"lat": 41.0, "lon": -1.0}


def test_administrative_only_is_low_confidence(transport):
    transport.add(
        "search",
        hits_json(
            [
                synthetic.nominatim_hit(
                    lat=41.0, lon=-1.0, place_class="boundary", place_type="administrative"
                )
            ]
        ),
    )
    diff = enrich_property(
        PropertyRecord(id="x", street="Villa"), cadastre=None, geocoder=nominatim(transport)
    )
    assert diff.status == STATUS_LOW_CONFIDENCE and not diff.changes


def test_nothing_to_resolve_is_pending(transport):
    diff = enrich_property(
        PropertyRecord(id="x"), cadastre=dgc(transport), geocoder=nominatim(transport)
    )
    assert diff.status == STATUS_PENDING and "no cadastral reference" in diff.reason
    assert transport.calls == 0


# --- physical -------------------------------------------------------------------


def test_physical_fills_each_empty_field_separately(transport):
    transport.add(
        "Consulta_DNPRC",
        synthetic.dgc_physical_xml(
            [{"sfc": 300, "ant": 1990, "luso": "Residencial"}, {"sfc": 200, "ant": 1975}]
        ),
    )
    rec = PropertyRecord(id="x", cadastral_reference=RC1, built_area_m2=0, construction_year=1980)
    diff = physical_enrichment(rec, cadastre=dgc(transport))
    assert diff.changes == {"built_area_m2": 500.0, "cadastral_use": "Residencial"}
    full = PropertyRecord(
        id="y", cadastral_reference=RC1, built_area_m2=1, construction_year=1, cadastral_use="u"
    )
    assert physical_enrichment(full, cadastre=dgc(transport)).status == STATUS_SKIPPED
    assert (
        physical_enrichment(full, cadastre=dgc(transport), overwrite=True).changes[
            "construction_year"
        ]
        == 1975
    )


def test_physical_unbuilt_and_not_supported(transport):
    transport.add("Consulta_DNPRC", synthetic.dgc_physical_xml([{"sfc": 0}]))
    rec = PropertyRecord(id="x", cadastral_reference=RC1)
    assert physical_enrichment(rec, cadastre=dgc(transport)).status == STATUS_UNBUILT
    from catastro_enrich.cadastre import GipuzkoaDriver

    diff = physical_enrichment(rec, cadastre=GipuzkoaDriver(transport=transport, pacer=NO_WAIT))
    assert diff.error_code == NOT_SUPPORTED and not diff.changes


# --- additive batch -------------------------------------------------------------


def test_additive_batch_dry_run_then_write_then_idempotent(transport):
    transport.add(
        "Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="CL OFICIAL 1", lon=-3.6, lat=40.7)
    )
    records = three_records()
    applied = []

    dry = enrich_batch(records, cadastre=dgc(transport), apply=applied.append)
    assert dry.dry_run and dry.queried == 1 and dry.skipped_existing == 1
    assert dry.written == 0 and dry.would_write == 1 and applied == []
    assert records[0].lat is None
    assert [p[0] for p in dry.pending] == ["c"]

    wet = enrich_batch(
        records,
        cadastre=dgc(transport),
        write=True,
        apply=lambda r, d: applied.append((r.id, d.changes)),
    )
    assert wet.written == 1 and not wet.dry_run
    assert records[0].lat == 40.7 and records[0].lon == -3.6 and records[0].street == "CL OFICIAL 1"
    assert applied == [("a", {"lat": 40.7, "lon": -3.6, "street": "CL OFICIAL 1"})]

    again = enrich_batch(records, cadastre=dgc(transport), write=True)
    assert again.written == 0 and again.queried == 0 and again.skipped_existing == 2


def test_batch_limit_counts_queried_records_only(transport):
    transport.add("Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="X", lon=-3.0, lat=40.0))
    records = [PropertyRecord(id="done", cadastral_reference=RC1, lat=1.0, lon=1.0)] + [
        PropertyRecord(id=f"p{i}", cadastral_reference=synthetic.synthetic_rc(i)) for i in range(5)
    ]
    stats = enrich_batch(records, cadastre=dgc(transport), limit=2)
    assert stats.queried == 2 and transport.calls == 2


def test_progress_callback_errors_do_not_stop_batch(transport):
    transport.add("Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="X", lon=-3.0, lat=40.0))
    seen = []

    def boom(index, total, diff):
        seen.append((index, total))
        raise RuntimeError("callback failure")

    stats = enrich_batch(three_records(), cadastre=dgc(transport), on_progress=boom)
    assert stats.queried == 1 and seen == [(0, 3), (1, 3), (2, 3)]


def test_batch_counters_and_telemetry(transport):
    transport.add(
        "Consulta_CPMRC",
        lambda url, params: synthetic.dgc_error_xml(9) if params["RC"] == RC1 else None,
    )
    log = MemoryRunLog()
    records = [
        PropertyRecord(id="nf", cadastral_reference=RC1),
        PropertyRecord(id="net", cadastral_reference=RC2),
        PropertyRecord(id="bad", cadastral_reference="123"),
    ]
    stats = enrich_batch(records, cadastre=dgc(transport), telemetry=log, municipality_id="m1")
    assert (stats.not_found, stats.net, stats.invalid, stats.errors) == (1, 1, 1, 1)
    assert stats.per_source == {"dgc": 3}
    run = log.list_runs("m1")[0]
    assert run.closed and run.queried == 3 and run.extra["not_found"] == 1 and run.dry_run


class BrokenLog:
    def open_run(self, **kwargs):
        raise OSError("disk full")

    def close_run(self, run_id, stats):
        raise OSError("disk full")

    def list_runs(self, municipality_id=None, limit=20):
        return []


def test_telemetry_failure_never_aborts(transport):
    transport.add("Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="X", lon=-3.0, lat=40.0))
    stats = enrich_batch(three_records(), cadastre=dgc(transport), telemetry=BrokenLog())
    assert stats.queried == 1


def test_geocoding_kind_ignores_cadastre(transport):
    transport.add(
        "search",
        hits_json(
            [
                synthetic.nominatim_hit(
                    lat=41.0, lon=-1.0, place_class="highway", place_type="residential"
                )
            ]
        ),
    )
    records = [PropertyRecord(id="g", cadastral_reference=RC1, street="Calle Mayor 1")]
    stats = enrich_batch(
        records,
        cadastre=dgc(transport),
        geocoder=nominatim(transport),
        kind="geocoding",
        write=True,
    )
    assert stats.written == 1 and stats.driver == "nominatim" and records[0].lat == 41.0
    assert all("search" in url for url in transport.urls())


# --- imagery batch ----------------------------------------------------------------


def test_imagery_batch_paths_dedup_dry_run_and_second_pass(transport, tmp_path):
    transport.add(
        "pnoa-ma", HttpResponse(200, synthetic.tiny_jpeg(1), {"content-type": "image/jpeg"})
    )
    drivers = [
        ign_pnoa_driver(transport=transport, pacer=NO_WAIT),
        MapillaryDriver("", transport=transport, pacer=NO_WAIT),
    ]
    records = [
        PropertyRecord(id="a", inventory_number="7", municipality_id="muni", lat=40.0, lon=-3.0),
        PropertyRecord(id="b", inventory_number="8", municipality_id="muni", lat=40.0, lon=-3.0),
        PropertyRecord(id="c", inventory_number="9", municipality_id="muni"),
    ]
    store = ImageStore(tmp_path)
    saved = []

    dry = enrich_imagery_batch(
        records, drivers=drivers, store=store, on_image=lambda r, d: saved.append(d)
    )
    assert dry.queried == 4 and dry.ok == 2 and dry.written == 0 and dry.would_write == 2
    assert dry.errors == 2 and not list(tmp_path.rglob("*.jpg")) and saved == []
    assert ("c", "9", "no coordinates") in dry.pending

    wet = enrich_imagery_batch(
        records, drivers=drivers, store=store, write=True, on_image=lambda r, d: saved.append(d)
    )
    assert wet.written == 1 and wet.duplicates == 1
    assert saved[0].relative_path == "enrichment/muni/7/ign_pnoa_ortho.jpg"
    assert (tmp_path / saved[0].relative_path).exists()

    again = enrich_imagery_batch(records, drivers=drivers[:1], store=store, write=True)
    assert again.queried == 1 and again.skipped_existing == 1 and again.written == 0


def test_imagery_batch_counts_no_coverage_and_net(transport, tmp_path):
    transport.add(
        "graph.mapillary.com",
        HttpResponse(200, b'{"data": []}', {"content-type": "application/json"}),
    )
    transport.add(
        "pnoa-ma", HttpResponse(200, b"<ServiceException/>", {"content-type": "text/xml"})
    )
    drivers = [
        ign_pnoa_driver(transport=transport, pacer=NO_WAIT),
        MapillaryDriver("tok", transport=transport, pacer=NO_WAIT),
    ]
    log = MemoryRunLog()
    stats = enrich_imagery_batch(
        [PropertyRecord(id="a", lat=40.0, lon=-3.0)],
        drivers=drivers,
        store=ImageStore(tmp_path),
        telemetry=log,
    )
    assert stats.no_coverage == 1 and stats.net == 1 and stats.ok == 0
    assert log.list_runs()[0].driver == "ign_pnoa+mapillary"
