import json

from catastro_enrich import synthetic
from catastro_enrich.imagery import (
    ImageStore,
    MapillaryDriver,
    WmsOrthoDriver,
    fetch_imagery,
    ign_pnoa_driver,
    live_links,
    ortho_driver,
    register_ortho_alias,
    sniff_image,
)
from catastro_enrich.models import BAD_RESPONSE, NET, NO_COVERAGE, NO_TOKEN, ImageDocument
from catastro_enrich.transport import HttpResponse, Pacer

NO_WAIT = Pacer(0, sleep=lambda s: None)
JPEG = synthetic.tiny_jpeg(1)


def jpeg_response(data=JPEG, content_type="image/jpeg"):
    return HttpResponse(200, data, {"content-type": content_type})


def test_pnoa_wms_request_uses_1_3_0_axis_order(transport):
    transport.add("pnoa-ma", jpeg_response())
    driver = ign_pnoa_driver(transport=transport, pacer=NO_WAIT, span_m=80)
    result = driver.fetch(40.0, -3.0)
    assert result.ok and result.mime == "image/jpeg" and result.bytes == JPEG
    assert "Instituto Geogr" in result.attribution and result.kind == "ORTHO"
    params = transport.requests[0].params
    assert params["VERSION"] == "1.3.0" and params["CRS"] == "EPSG:4326" and "SRS" not in params
    assert params["LAYERS"] == "OI.OrthoimageCoverage" and params["FORMAT"] == "image/jpeg"
    min_lat = float(params["BBOX"].split(",")[0])
    assert 39.99 < min_lat < 40.0


def test_wms_1_1_1_uses_srs_and_lon_first(transport):
    transport.add("", jpeg_response())
    driver = WmsOrthoDriver(
        url="https://example.invalid/wms",
        layer="L",
        name="regional",
        attribution="x",
        version="1.1.1",
        transport=transport,
        pacer=NO_WAIT,
    )
    driver.fetch(40.0, -3.0)
    params = transport.requests[0].params
    assert params["SRS"] == "EPSG:4326" and "CRS" not in params
    assert float(params["BBOX"].split(",")[0]) < -3.0


def test_wms_service_exception_with_200_is_net(transport):
    transport.add("", HttpResponse(200, b"<ServiceException/>", {"content-type": "text/xml"}))
    assert ign_pnoa_driver(transport=transport, pacer=NO_WAIT).fetch(40.0, -3.0).error_code == NET
    assert sniff_image(HttpResponse(200, b"abc", {"content-type": "image/png"})) == "image/png"
    assert sniff_image(HttpResponse(200, synthetic.tiny_png(), {})) == "image/png"
    assert sniff_image(None) is None


def test_mapillary_requires_token(transport):
    assert (
        MapillaryDriver("", transport=transport, pacer=NO_WAIT).fetch(40.0, -3.0).error_code
        == NO_TOKEN
    )
    assert transport.calls == 0


def test_mapillary_no_coverage(transport):
    transport.add(
        "graph.mapillary.com",
        HttpResponse(
            200,
            json.dumps(synthetic.mapillary_json([])).encode(),
            {"content-type": "application/json"},
        ),
    )
    result = MapillaryDriver("synthetic-token", transport=transport, pacer=NO_WAIT).fetch(
        40.0, -3.0
    )
    assert result.error_code == NO_COVERAGE
    params = transport.requests[0].params
    assert params["limit"] == "1" and params["fields"].startswith("id,thumb_1024_url")
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in params["bbox"].split(","))
    assert min_lon < -3.0 < max_lon and min_lat < 40.0 < max_lat


def test_mapillary_downloads_thumbnail_with_attribution(transport):
    payload = synthetic.mapillary_json(
        [
            {
                "id": "123",
                "thumb_1024_url": "https://example.invalid/thumb.jpg",
                "compass_angle": 90,
                "captured_at": 1_700_000_000_000,
            }
        ]
    )
    transport.add(
        "graph.mapillary.com",
        HttpResponse(200, json.dumps(payload).encode(), {"content-type": "application/json"}),
    )
    transport.add("thumb.jpg", jpeg_response())
    result = MapillaryDriver("synthetic-token", transport=transport, pacer=NO_WAIT).fetch(
        40.0, -3.0
    )
    assert result.ok and result.kind == "STREET" and result.image_id == "123"
    assert "Mapillary" in result.attribution and "123" in result.attribution
    assert result.captured_at is not None and result.captured_at.startswith("2023-11-14")


def test_mapillary_bad_payloads(transport):
    transport.add(
        "graph.mapillary.com", HttpResponse(200, b"not json", {"content-type": "text/plain"})
    )
    assert (
        MapillaryDriver("t", transport=transport, pacer=NO_WAIT).fetch(40.0, -3.0).error_code
        == BAD_RESPONSE
    )
    transport.rules.clear()
    transport.add("graph.mapillary.com", None)
    assert (
        MapillaryDriver("t", transport=transport, pacer=NO_WAIT).fetch(40.0, -3.0).error_code == NET
    )


def test_fetch_imagery_and_aliases(transport):
    transport.add("pnoa-ma", jpeg_response())
    results = fetch_imagery(
        40.0,
        -3.0,
        drivers=[ign_pnoa_driver(transport=transport, pacer=NO_WAIT), MapillaryDriver(None)],
    )
    assert [r.ok for r in results] == [True, False]
    assert ortho_driver("none") is None
    assert ortho_driver("auto").name == "ign_pnoa"
    register_ortho_alias(
        "regional",
        lambda **kw: WmsOrthoDriver(
            url="https://example.invalid/wms",
            layer="x",
            name="regional",
            attribution="Regional IDE",
            **kw,
        ),
    )
    assert ortho_driver("regional").name == "regional"


def test_live_links_are_deterministic_six_decimals():
    links = live_links(40.123456789, -3.987654321)
    assert set(links) == {"street_view", "maps", "satellite"}
    assert all("40.123457,-3.987654" in url for url in links.values())
    assert links == live_links(40.1234568, -3.9876543)


def test_image_store_paths_dedup_and_dry_run(tmp_path):
    record = synthetic.synthetic_records(1, municipality_id="M1")[0]
    record.inventory_number = "00042"
    ortho = synthetic.ScriptedTransport()
    ortho.add("", jpeg_response())
    image = ign_pnoa_driver(transport=ortho, pacer=NO_WAIT).fetch(40.0, -3.0)

    store = ImageStore(tmp_path)
    status, doc = store.store(record, image, write=False)
    assert status == "dry_run" and doc is not None
    assert doc.relative_path == "enrichment/m1/00042/ign_pnoa_ortho.jpg"
    assert not (tmp_path / doc.relative_path).exists()

    status, doc = store.store(record, image, write=True)
    assert status == "written" and (tmp_path / doc.relative_path).read_bytes() == JPEG
    assert doc.sha256 and doc.size == len(JPEG) and doc.kind == "ORTHO"

    assert store.store(record, image, write=True)[0] == "skipped_existing"
    other = synthetic.synthetic_records(2, municipality_id="M1")[1]
    assert store.store(other, image, write=True)[0] == "duplicate"

    rebuilt = ImageStore(tmp_path, existing=[doc])
    assert rebuilt.has(record.id, "ign_pnoa", "ORTHO")
    assert rebuilt.store(record, image, write=True)[0] == "skipped_existing"
    assert rebuilt.store(record, image, write=True, overwrite=True)[0] == "written"


def test_image_document_round_trip():
    doc = ImageDocument(
        "p", "f.jpg", "enrichment/x/y/f.jpg", "image/jpeg", "abc", 3, "ign_pnoa", "ORTHO", "attr"
    )
    assert doc.captured_at is None
