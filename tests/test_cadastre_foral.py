import urllib.parse

import pytest

from catastro_enrich import synthetic
from catastro_enrich.cadastre import (
    AlavaDriver,
    BizkaiaDriver,
    GipuzkoaDriver,
    NavarraDriver,
    gipuzkoa_reference,
    navarra_reference,
    split_bizkaia_rc,
)
from catastro_enrich.cadastre.arcgis import equals_filter
from catastro_enrich.geo import utm_to_wgs84
from catastro_enrich.models import INVALID_RC, NET, NOT_FOUND
from catastro_enrich.transport import Pacer

NO_WAIT = {"pacer": Pacer(0, sleep=lambda s: None)}


# --- Gipuzkoa ---------------------------------------------------------------


def test_gipuzkoa_reference_point_and_area(transport):
    transport.add(
        "GetNationalRefParcel",
        synthetic.inspire_parcel_gml(
            reference="3232", reference_point=(43.31, -1.93), area=512.0, local_id="069-3232"
        ),
    )
    result = GipuzkoaDriver(transport=transport, **NO_WAIT).lookup_rc("069-REF-3232")
    assert result.ok and (result.lat, result.lon) == (43.31, -1.93) and result.area_m2 == 512.0
    assert "069-3232" in result.description
    params = transport.requests[0].params
    assert params["nationalcadastralreference"] == "REF" and params["count"] == "1"
    assert params["storedquery_id"].endswith("GetNationalRefParcel")


def test_gipuzkoa_short_numeric_reference_is_sent_as_is(transport):
    transport.add("", synthetic.inspire_parcel_gml(reference="12345", reference_point=(43.2, -2.0)))
    GipuzkoaDriver(transport=transport, **NO_WAIT).lookup_rc("12345")
    assert transport.requests[0].params["nationalcadastralreference"] == "12345"
    assert gipuzkoa_reference(" 069-ab12-0001 ") == "AB12"


def test_gipuzkoa_number_matched_zero_is_not_found(transport):
    transport.add(
        "", synthetic.inspire_parcel_gml(reference="x", number_matched="0", number_returned="0")
    )
    assert GipuzkoaDriver(transport=transport, **NO_WAIT).lookup_rc("1").error_code == NOT_FOUND


def test_gipuzkoa_falls_back_to_polygon_centroid(transport):
    ring = [(43.0, -2.0), (43.0, -1.0), (44.0, -1.0), (44.0, -2.0)]
    transport.add("", synthetic.inspire_parcel_gml(reference="7", ring=ring))
    result = GipuzkoaDriver(transport=transport, **NO_WAIT).lookup_rc("7")
    assert result.ok and (result.lat, result.lon) == (43.5, -1.5)


# --- Navarra ----------------------------------------------------------------


def test_navarra_centroid_from_closed_ring(transport):
    ring = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0)]
    transport.add(
        "CadastralParcel",
        synthetic.inspire_parcel_gml(reference="123456789", ring=ring, patches=True, area=88.0),
    )
    result = NavarraDriver(transport=transport, **NO_WAIT).lookup_rc("ES.3100.CP.123456789")
    assert result.ok and (result.lat, result.lon) == (5.0, 5.0) and result.area_m2 == 88.0
    params = transport.requests[0].params
    assert params["cql_filter"] == "nationalCadastralReference='123456789'"
    assert params["typeNames"] == "CP:CadastralParcel" and params["count"] == "1"


def test_navarra_number_returned_zero_is_not_found(transport):
    transport.add(
        "",
        synthetic.inspire_parcel_gml(reference="1", number_returned="0", number_matched="unknown"),
    )
    assert (
        NavarraDriver(transport=transport, **NO_WAIT).lookup_rc("123456789").error_code == NOT_FOUND
    )


def test_navarra_reference_forms():
    assert navarra_reference("ES.3100.CP.000123456") == "000123456"
    assert navarra_reference("000123456") == "000123456"
    assert (
        NavarraDriver(transport=synthetic.ScriptedTransport(), **NO_WAIT).lookup_rc("AB").error_code
        == INVALID_RC
    )


# --- Álava ------------------------------------------------------------------

UTM_RING = [
    (526000.0, 4744000.0),
    (526010.0, 4744000.0),
    (526010.0, 4744010.0),
    (526000.0, 4744010.0),
]


def test_alava_uses_fes_filter_and_tries_urban_before_rural(transport):
    rc = synthetic.synthetic_alava_rc(3)
    transport.add(
        "ParcelasUrbanas",
        synthetic.arcgis_parcel_xml(
            layer="PartzelaHiritarrak_ParcelasUrbanas", fields={}, ring_utm=None, number_matched="0"
        ),
    )
    transport.add(
        "ParcelasRusticas",
        synthetic.arcgis_parcel_xml(
            layer="PartzelaLandatarrak_ParcelasRusticas",
            fields={"REF_CATASTRAL": rc, "INFO": "Synthetic rural parcel", "MUNICIPIO": "1"},
            ring_utm=UTM_RING,
        ),
    )
    result = AlavaDriver(transport=transport, **NO_WAIT).lookup_rc(rc)
    assert result.ok and result.description == "Synthetic rural parcel"
    lat, lon = utm_to_wgs84(526005.0, 4744005.0, 30)
    assert result.lat == pytest.approx(lat) and result.lon == pytest.approx(lon)
    urls = [urllib.parse.unquote(u) for u in transport.urls()]
    assert len(urls) == 2
    assert "ParcelasUrbanas" in urls[0] and "ParcelasRusticas" in urls[1]
    for url in urls:
        assert "PropertyIsEqualTo" in url and "REF_CATASTRAL" in url and rc in url
        assert "cql_filter" not in url


def test_alava_not_found_in_both_layers(transport):
    transport.add(
        "", synthetic.arcgis_parcel_xml(layer="x", fields={}, ring_utm=None, number_matched="0")
    )
    assert (
        AlavaDriver(transport=transport, **NO_WAIT).lookup_rc("01-001-00001").error_code
        == NOT_FOUND
    )
    assert transport.calls == 2


def test_alava_network_failure_is_net(transport):
    transport.add("", None)
    assert AlavaDriver(transport=transport, **NO_WAIT).lookup_rc("01-001-00001").error_code == NET


# --- Bizkaia ----------------------------------------------------------------


@pytest.mark.parametrize("rc", ["94-1055-99082", "094105599082", "94/1055/99082"])
def test_bizkaia_reference_forms_are_equivalent(rc):
    assert split_bizkaia_rc(rc) == (94, 1055, 99082)


def test_bizkaia_nine_digit_form_and_invalid():
    assert split_bizkaia_rc("020330123") == (2, 33, 123)
    assert split_bizkaia_rc("ABC") is None
    driver = BizkaiaDriver(transport=synthetic.ScriptedTransport(), **NO_WAIT)
    assert driver.lookup_rc("ABC").error_code == INVALID_RC
    assert driver.transport.calls == 0


def test_bizkaia_filter_joins_three_conditions_and_reads_area(transport):
    transport.add(
        "Parcelas",
        synthetic.arcgis_parcel_xml(
            layer="Parcelas",
            fields={"Codigo_Mun": 94, "Codigo_Pol": 1055, "Codigo_Par": 99082, "Area": 321.5},
            ring_utm=UTM_RING,
        ),
    )
    result = BizkaiaDriver(transport=transport, **NO_WAIT).lookup_rc("094105599082")
    assert result.ok and result.area_m2 == 321.5 and "94-1055-99082" in result.description
    url = urllib.parse.unquote(transport.urls()[0])
    assert url.count("<fes:PropertyIsEqualTo>") == 3 and "<fes:And>" in url
    for field, value in (("Codigo_Mun", "94"), ("Codigo_Pol", "1055"), ("Codigo_Par", "99082")):
        assert (
            f"<fes:ValueReference>{field}</fes:ValueReference><fes:Literal>{value}</fes:Literal>"
            in url
        )
    assert transport.requests[0].timeout == 15.0


def test_fes_filter_single_condition_has_no_and():
    xml = equals_filter({"A": "1"})
    assert "<fes:And>" not in xml and "PropertyIsEqualTo" in xml
