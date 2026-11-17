import pytest

from catastro_enrich import geo
from catastro_enrich.xmlutil import attr_of, find_first, float_of, parse_xml, text_of


@pytest.mark.parametrize(
    ("lat", "lon", "zone"),
    [(28.3, -16.5, 28), (42.9, -8.5, 29), (40.4, -3.7, 30), (41.4, 2.2, 31)],
)
def test_utm_round_trip_per_zone(lat, lon, zone):
    easting, northing, z = geo.wgs84_to_utm(lat, lon)
    assert z == zone
    back_lat, back_lon = geo.utm_to_wgs84(easting, northing, z)
    assert abs(back_lat - lat) < 1e-6
    assert abs(back_lon - lon) < 1e-6
    e2, n2, _ = geo.wgs84_to_utm(back_lat, back_lon)
    assert abs(e2 - easting) < 0.1 and abs(n2 - northing) < 0.1


def test_zone_outside_spain_rejected():
    with pytest.raises(ValueError):
        geo.utm_epsg(27)
    with pytest.raises(ValueError):
        geo.utm_to_wgs84(500000, 4000000, 33)


def test_ring_centroid_ignores_closing_vertex():
    square = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]
    assert geo.ring_centroid(square) == (5.0, 5.0)
    assert geo.ring_centroid([]) is None
    assert geo.parse_pos_list("0 0 0 10 10 10 10 0 0 0") == square


def test_axis_order_detection():
    assert geo.as_lat_lon((40.4, -3.7)) == (40.4, -3.7)
    assert geo.as_lat_lon((-3.7, 40.4)) == (40.4, -3.7)
    assert geo.as_lat_lon((5.0, 5.0)) == (5.0, 5.0)


def test_bbox_and_wms_axis_order():
    bbox = geo.bbox_around(40.0, -3.0, 80)
    min_lon, min_lat, max_lon, max_lat = bbox
    assert min_lat < 40.0 < max_lat and min_lon < -3.0 < max_lon
    assert pytest.approx(max_lat - min_lat, rel=1e-9) == 80 / 111_320
    assert (max_lon - min_lon) > (max_lat - min_lat)
    name, value = geo.wms_bbox("1.3.0", bbox)
    assert name == "CRS" and value.split(",")[0] == str(min_lat)
    name, value = geo.wms_bbox("1.1.1", bbox)
    assert name == "SRS" and value.split(",")[0] == str(min_lon)


def test_bbox_latitude_factor_floor():
    near_pole = geo.bbox_around(89.9, 0.0, 100)
    assert (near_pole[2] - near_pole[0]) <= 100 / (111_320 * 0.2) + 1e-9


def test_polygon_wkt_lon_lat_order():
    wkt = geo.polygon_wkt([(40.0, -3.0), (40.0, -2.0), (41.0, -2.0)])
    assert wkt.startswith("POLYGON((-3.0 40.0, -2.0 40.0, -2.0 41.0, -3.0 40.0))")


def test_xmlutil_local_names_and_namespaces():
    root = parse_xml(b'<a xmlns="urn:x" xmlns:y="urn:y"><y:b y:k="v">1,5</y:b><c/></a>')
    assert root is not None
    b = find_first(root, "b")
    assert b is not None and attr_of(b, "k") == "v"
    assert text_of(root, "b") == "1,5" and float_of(root, "b") == 1.5
    assert text_of(root, "missing", "d") == "d"
    assert parse_xml(b"<broken") is None
