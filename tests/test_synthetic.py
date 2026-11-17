import json
import xml.etree.ElementTree as ET

from catastro_enrich import synthetic
from catastro_enrich.models import PropertyRecord
from catastro_enrich.transport import HttpResponse


def test_synthetic_rc_shape_is_artificial():
    rc = synthetic.synthetic_rc(42)
    assert len(rc) == 14
    assert synthetic.SYNTHETIC_SHEET in rc
    assert synthetic.is_synthetic_rc(rc)
    assert len(synthetic.synthetic_rc(42, length=20)) == 20
    assert synthetic.is_synthetic_rc(synthetic.synthetic_rc(42, length=20))
    assert not synthetic.is_synthetic_rc("1234567AB1234C")


def test_records_are_deterministic_and_respect_ratios():
    a = synthetic.synthetic_records(50, seed=1)
    b = synthetic.synthetic_records(50, seed=1)
    assert [r.to_dict() for r in a] == [r.to_dict() for r in b]
    assert all(isinstance(r, PropertyRecord) for r in a)
    none_coords = synthetic.synthetic_records(30, seed=2, with_coordinates=0.0)
    assert not any(r.has_coordinates for r in none_coords)
    all_coords = synthetic.synthetic_records(30, seed=2, with_coordinates=1.0)
    assert all(r.has_coordinates for r in all_coords)
    assert all(36.0 <= r.lat <= 43.5 for r in all_coords)
    ids = [r.id for r in a]
    assert len(set(ids)) == len(ids)


def test_record_round_trip_dict():
    rec = synthetic.synthetic_records(1, seed=3)[0]
    again = PropertyRecord.from_dict({**rec.to_dict(), "unknown_column": 1})
    assert again == rec


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def test_dgc_xml_builders_parse():
    root = ET.fromstring(synthetic.dgc_coordinates_xml(ldt="CL SYNTH 1", lon=-3.6, lat=40.7))
    tags = {_local(e.tag): (e.text or "") for e in root.iter()}
    assert tags["xcen"] == "-3.6" and tags["ycen"] == "40.7" and tags["ldt"] == "CL SYNTH 1"
    err = ET.fromstring(synthetic.dgc_error_xml(9, "no existe"))
    assert {_local(e.tag) for e in err.iter()} >= {"lerr", "err", "cod", "des"}
    phys = ET.fromstring(
        synthetic.dgc_physical_xml(
            [{"sfc": 300, "ant": 1990, "luso": "Residencial"}, {"sfc": 200, "ant": 1975}],
            constructions=[{"lcd": "VIVIENDA", "pt": "01", "stl": 120}],
        )
    )
    assert [e.text for e in phys.iter() if _local(e.tag) == "sfc"] == ["300", "200"]
    assert [e.text for e in phys.iter() if _local(e.tag) == "stl"] == ["120"]
    point = ET.fromstring(synthetic.dgc_point_xml(pc1="0000001", pc2="ZZ0007S", ldt="X"))
    assert [e.text for e in point.iter() if _local(e.tag) in ("pc1", "pc2")] == [
        "0000001",
        "ZZ0007S",
    ]
    addr = ET.fromstring(
        synthetic.dgc_address_xml([synthetic.synthetic_rc(1), synthetic.synthetic_rc(2)])
    )
    assert len([e for e in addr.iter() if _local(e.tag) == "pc1"]) == 2


def test_inspire_and_arcgis_builders():
    gml = ET.fromstring(
        synthetic.inspire_parcel_gml(
            reference="123456789",
            ring=[(0, 0), (0, 10), (10, 10), (10, 0)],
            patches=True,
            area=99.0,
        )
    )
    assert gml.get("numberReturned") == "1"
    pos = next(e.text for e in gml.iter() if _local(e.tag) == "posList")
    assert pos.split() == ["0", "0", "0", "10", "10", "10", "10", "0", "0", "0"]
    assert any(_local(e.tag) == "PolygonPatch" for e in gml.iter())
    empty = ET.fromstring(
        synthetic.inspire_parcel_gml(reference="x", number_returned="0", number_matched="0")
    )
    assert not any(_local(e.tag) == "CadastralParcel" for e in empty.iter())
    arc = ET.fromstring(
        synthetic.arcgis_parcel_xml(
            layer="Parcelas",
            fields={"REF_CATASTRAL": "01-001-00001", "INFO": "synthetic"},
            ring_utm=[(500000, 4700000), (500010, 4700000), (500010, 4700010), (500000, 4700010)],
        )
    )
    assert any(_local(e.tag) == "REF_CATASTRAL" for e in arc.iter())
    assert "25830" in next(e.get("srsName") for e in arc.iter() if _local(e.tag) == "MultiSurface")


def test_json_and_image_builders():
    hit = synthetic.nominatim_hit(
        lat=40.1, lon=-3.1, place_class="highway", place_type="residential"
    )
    assert json.loads(json.dumps(hit))["class"] == "highway"
    assert synthetic.mapillary_json([])["data"] == []
    assert synthetic.tiny_jpeg(1).startswith(b"\xff\xd8\xff")
    assert synthetic.tiny_jpeg(1) != synthetic.tiny_jpeg(2)
    assert synthetic.tiny_png(3).startswith(b"\x89PNG")


def test_scripted_transport_rules_and_recording():
    t = synthetic.ScriptedTransport()
    t.add("down", None).add("json", HttpResponse(200, b"{}", {"content-type": "application/json"}))
    t.add("echo", lambda url, params: HttpResponse(200, params["q"].encode()))
    t.add("", b"<x/>")
    assert t.get("https://example.invalid/down") is None
    assert t.get("https://example.invalid/json").content_type == "application/json"
    assert t.get("https://example.invalid/echo", {"q": "hola"}).text == "hola"
    assert t.get("https://example.invalid/other").text == "<x/>"
    assert t.calls == 4
    assert t.urls()[2].endswith("echo?q=hola")
    assert t.requests[0].timeout > 0
