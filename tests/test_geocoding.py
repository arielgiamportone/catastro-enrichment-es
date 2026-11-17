from catastro_enrich import synthetic
from catastro_enrich.geocoding import (
    NominatimGeocoder,
    build_query,
    classify_precision,
    geocode_address,
    normalize_address,
    pick_hit,
)
from catastro_enrich.models import EMPTY_QUERY, LOW_CONFIDENCE, NET, NOT_FOUND, PARSE
from catastro_enrich.transport import HttpResponse, Pacer

NO_WAIT = Pacer(0, sleep=lambda s: None)


def nominatim(transport):
    return NominatimGeocoder(transport=transport, pacer=NO_WAIT)


def as_json(hits):
    import json

    return HttpResponse(200, json.dumps(hits).encode(), {"content-type": "application/json"})


# --- address cleaning -------------------------------------------------------


def test_registry_prefix_is_removed_once():
    assert normalize_address("sita en la calle de X, tres") == "X, tres"
    assert normalize_address("Sita en la Calle de X, tres") == "X, tres"
    assert normalize_address("Barrio de Arriba, 4") == "Arriba, 4"
    assert normalize_address("Avda. Principal 2") == "Principal 2"
    assert normalize_address("término municipal de Villaficticia") == "Villaficticia"


def test_only_first_line_is_kept():
    assert normalize_address("Plaza Mayor 1\nsegunda planta\nmas texto") == "Mayor 1"


def test_address_is_capped_at_200_chars():
    long = "Camino " + "x" * 500
    assert len(normalize_address(long)) == 200


def test_empty_and_degenerate_addresses():
    assert normalize_address(None) == "" and normalize_address("   ") == ""
    assert normalize_address("calle") == "calle"


def test_query_skips_empty_parts():
    assert build_query("Mayor 1", "Aldea", "Provincia") == "Mayor 1, Aldea, Provincia, Spain"
    assert build_query("Mayor 1", None, "") == "Mayor 1, Spain"
    assert build_query("", None, None) == ""


# --- hit selection ----------------------------------------------------------


def test_concrete_class_beats_more_important_boundary(transport):
    hits = [
        synthetic.nominatim_hit(
            lat=40.0, lon=-3.0, place_class="boundary", place_type="administrative", importance=0.9
        ),
        synthetic.nominatim_hit(
            lat=40.5, lon=-3.5, place_class="highway", place_type="residential", importance=0.3
        ),
    ]
    transport.add("search", as_json(hits))
    result = geocode_address("Calle Mayor 1", geocoder=nominatim(transport), locality="Aldea")
    assert result.ok and result.precision == "exact"
    assert (result.lat, result.lon) == (40.5, -3.5)
    assert result.place_class == "highway" and result.confidence == 0.3
    assert result.query == "Mayor 1, Aldea, Spain" and result.source == "nominatim"
    req = transport.requests[0]
    assert (
        req.params["format"] == "json"
        and req.params["countrycodes"] == "es"
        and req.params["limit"] == "5"
    )
    assert req.headers["Accept-Language"].startswith("es")


def test_suburb_hit_is_zone_precision(transport):
    transport.add(
        "search",
        as_json(
            [synthetic.nominatim_hit(lat=41.0, lon=-1.0, place_class="place", place_type="suburb")]
        ),
    )
    result = geocode_address("Barrio de Arriba", geocoder=nominatim(transport))
    assert result.ok and result.precision == "zone" and result.error_code == ""


def test_administrative_only_is_low_confidence_with_coordinates(transport):
    transport.add(
        "search",
        as_json(
            [
                synthetic.nominatim_hit(
                    lat=42.0, lon=-2.0, place_class="boundary", place_type="administrative"
                )
            ]
        ),
    )
    result = geocode_address("Villaficticia", geocoder=nominatim(transport))
    assert not result.ok and result.error_code == LOW_CONFIDENCE
    assert (result.lat, result.lon) == (42.0, -2.0) and result.precision == "administrative"


def test_empty_response_is_not_found(transport):
    transport.add("search", as_json([]))
    assert geocode_address("Nowhere 9", geocoder=nominatim(transport)).error_code == NOT_FOUND


def test_empty_query_does_not_call(transport):
    assert geocode_address("", geocoder=nominatim(transport)).error_code == EMPTY_QUERY
    assert transport.calls == 0


def test_rate_limit_is_net_without_retries(transport):
    transport.add("search", HttpResponse(429, b"Too Many Requests"))
    assert geocode_address("Mayor 1", geocoder=nominatim(transport)).error_code == NET
    assert transport.calls == 1


def test_network_failure_and_bad_json(transport):
    transport.add("down", None)
    transport.add("search", HttpResponse(200, b"<html>", {"content-type": "text/html"}))
    geocoder = NominatimGeocoder(
        transport=transport, pacer=NO_WAIT, url="https://example.invalid/down"
    )
    assert geocode_address("Mayor 1", geocoder=geocoder).error_code == NET
    assert geocode_address("Mayor 1", geocoder=nominatim(transport)).error_code == PARSE


def test_pacer_spaces_requests(transport, clock):
    transport.add("search", as_json([]))
    geocoder = NominatimGeocoder(
        transport=transport, pacer=Pacer(1.1, clock=clock, sleep=clock.sleep)
    )
    geocode_address("A 1", geocoder=geocoder)
    geocode_address("B 2", geocoder=geocoder)
    assert clock.sleeps == [1.1]


def test_precision_labels():
    assert classify_precision("highway") == "exact"
    assert classify_precision("place", "village") == "zone"
    assert classify_precision("landuse") == "zone"
    assert classify_precision("boundary", "administrative") == "administrative"
    assert classify_precision("railway", "station") == "zone"
    assert pick_hit([]) == (None, "")
