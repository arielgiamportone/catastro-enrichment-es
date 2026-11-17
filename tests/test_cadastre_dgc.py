import pytest

from catastro_enrich import synthetic
from catastro_enrich.cadastre import DgcDriver, DgcTerritory
from catastro_enrich.models import INVALID_RC, NET, NOT_FOUND, PARSE
from catastro_enrich.transport import HttpResponse, Pacer

RC = synthetic.synthetic_rc(1)


def make(transport, **kw):
    return DgcDriver(
        DgcTerritory("Madrid", "Madrid"),
        transport=transport,
        pacer=Pacer(0, sleep=lambda s: None),
        **kw,
    )


def test_valid_rc_returns_coordinates_and_description(transport, no_wait):
    transport.add(
        "Consulta_CPMRC",
        synthetic.dgc_coordinates_xml(ldt="CL SINTETICA 1", lon=-3.6, lat=40.7, rc=RC),
    )
    result = make(transport).lookup_rc(RC)
    assert result.ok and result.error_code == ""
    assert (result.lat, result.lon) == (40.7, -3.6)
    assert result.description == "CL SINTETICA 1"
    assert result.source == "dgc"
    req = transport.requests[0]
    assert req.params["Provincia"] == "MADRID" and req.params["Municipio"] == "MADRID"
    assert req.params["SRS"] == "EPSG:4326" and req.params["RC"] == RC[:14]


def test_missing_rc_propagates_ovc_code(transport):
    transport.add("Consulta_CPMRC", synthetic.dgc_error_xml(9, "la referencia catastral no existe"))
    result = make(transport).lookup_rc(RC)
    assert not result.ok and result.error_code == "9"
    assert "no existe" in result.description


def test_short_rc_does_not_hit_transport(transport):
    result = make(transport).lookup_rc("ABC123")
    assert result.error_code == INVALID_RC
    assert transport.calls == 0


def test_rc_of_20_chars_is_truncated_for_coordinates(transport):
    transport.add("Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="X", lon=-1.0, lat=41.0))
    make(transport).lookup_rc(synthetic.synthetic_rc(5, length=20))
    assert len(transport.requests[0].params["RC"]) == 14


def test_network_failure_is_net(transport):
    transport.add("Consulta_CPMRC", None)
    assert make(transport).lookup_rc(RC).error_code == NET


def test_http_429_is_net_without_retry(transport):
    transport.add("Consulta_CPMRC", HttpResponse(429, b"slow down"))
    assert make(transport).lookup_rc(RC).error_code == NET
    assert transport.calls == 1


def test_malformed_xml_is_parse(transport):
    transport.add("Consulta_CPMRC", b"<not-xml")
    assert make(transport).lookup_rc(RC).error_code == PARSE


def test_physical_data_sums_area_and_takes_oldest_year(transport):
    transport.add(
        "Consulta_DNPRC",
        synthetic.dgc_physical_xml(
            [
                {"sfc": 300, "ant": 1990, "luso": "Residencial"},
                {"sfc": 200, "ant": 1975, "luso": "Almacen"},
            ]
        ),
    )
    result = make(transport).physical_data(RC)
    assert result.ok
    assert result.area_m2 == 500 and result.year == 1975 and result.use == "Residencial"


def test_unbuilt_parcel_is_not_found_and_flagged(transport):
    transport.add(
        "Consulta_DNPRC", synthetic.dgc_physical_xml([{"sfc": 0, "ant": "", "luso": "Suelo"}])
    )
    result = make(transport).physical_data(RC)
    assert not result.ok and result.error_code == NOT_FOUND
    assert result.raw.get("unbuilt") is True


def test_physical_falls_back_to_construction_areas(transport):
    transport.add(
        "Consulta_DNPRC",
        synthetic.dgc_physical_xml(
            [], constructions=[{"lcd": "VIVIENDA", "stl": 80}, {"lcd": "ALMACEN", "stl": 20}]
        ),
    )
    result = make(transport).physical_data(RC)
    assert result.ok and result.area_m2 == 100 and result.use == "VIVIENDA" and result.year is None


def test_physical_accepts_14_or_20_only(transport):
    assert make(transport).physical_data("123").error_code == INVALID_RC
    assert transport.calls == 0


def test_rc_at_point(transport):
    transport.add(
        "Consulta_RCCOOR", synthetic.dgc_point_xml(pc1=RC[:7], pc2=RC[7:], ldt="CL PUNTO 1")
    )
    lookup = make(transport).rc_at_point(40.7, -3.6)
    assert lookup.ok and lookup.rc == RC and len(lookup.rc) == 14
    assert lookup.description == "CL PUNTO 1"
    params = transport.requests[0].params
    assert params["Coordenada_X"].startswith("-3.6") and params["Coordenada_Y"].startswith("40.7")


def test_rc_at_point_code_16_is_not_found(transport):
    transport.add("Consulta_RCCOOR", synthetic.dgc_error_xml(16, "no hay referencia"))
    lookup = make(transport).rc_at_point(40.7, -3.6)
    assert not lookup.ok and lookup.error_code == NOT_FOUND


def test_rcs_by_address(transport):
    refs = [synthetic.synthetic_rc(1), synthetic.synthetic_rc(2)]
    transport.add("ConsultaNumero", synthetic.dgc_address_xml(refs))
    assert make(transport).rcs_by_address("cl", "mayor", 3) == refs
    params = transport.requests[0].params
    assert params["TipoVia"] == "CL" and params["NomVia"] == "MAYOR" and params["Numero"] == "3"


def test_rcs_by_address_error_is_empty_list(transport):
    transport.add("ConsultaNumero", synthetic.dgc_error_xml(3, "no hay numeros"))
    assert make(transport).rcs_by_address("CL", "X", 1) == []


def test_inspire_parcel_area_and_polygon_with_axis_check(transport):
    ring = [(40.0, -3.0), (40.0, -2.999), (40.001, -2.999), (40.001, -3.0)]
    transport.add("wfsCP.aspx", synthetic.inspire_parcel_gml(reference=RC, ring=ring, area=777.0))
    driver = make(transport)
    assert driver.parcel_area(RC) == 777.0
    wkt = driver.parcel_polygon_wkt(RC)
    assert wkt is not None and wkt.startswith("POLYGON((-3.0 40.0, -2.999 40.0")
    assert transport.requests[0].params["STOREDQUERIE_ID"] == "GetParcel"
    assert transport.requests[0].params["REFCAT"] == RC


def test_inspire_parcel_swaps_lon_lat_when_needed(transport):
    swapped = [(-3.0, 40.0), (-2.999, 40.0), (-2.999, 40.001), (-3.0, 40.001)]
    transport.add("wfsCP.aspx", synthetic.inspire_parcel_gml(reference=RC, ring=swapped))
    wkt = make(transport).parcel_polygon_wkt(RC)
    assert wkt is not None and wkt.startswith("POLYGON((-3.0 40.0")


def test_inspire_not_found_returns_none(transport):
    transport.add(
        "wfsCP.aspx",
        synthetic.inspire_parcel_gml(reference=RC, number_returned="0", number_matched="0"),
    )
    assert make(transport).parcel_area(RC) is None


def test_batch_yields_one_result_per_rc(transport):
    transport.add("Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="X", lon=-3.0, lat=40.0))
    results = list(make(transport).batch([RC, "short", synthetic.synthetic_rc(2)]))
    assert [r.ok for r in results] == [True, False, True]
    assert transport.calls == 2


def test_pacer_enforces_delay_between_calls(transport, clock):
    transport.add("Consulta_CPMRC", synthetic.dgc_coordinates_xml(ldt="X", lon=-3.0, lat=40.0))
    driver = DgcDriver(transport=transport, pacer=Pacer(0.4, clock=clock, sleep=clock.sleep))
    driver.lookup_rc(RC)
    clock.now += 0.1
    driver.lookup_rc(RC)
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(0.3)
    clock.now += 1.0
    driver.lookup_rc(RC)
    assert len(clock.sleeps) == 1


def test_territory_override_is_upper_cased():
    assert DgcTerritory("Las Palmas", "Ingenio").params() == {
        "Provincia": "LAS PALMAS",
        "Municipio": "INGENIO",
    }


def test_physical_expands_parcel_units_and_aggregates(transport):
    parcel = synthetic.synthetic_rc(9)
    unit_a, unit_b = parcel + "0001AB", parcel + "0002CD"
    transport.add(
        unit_a,
        synthetic.dgc_physical_xml(
            [{"sfc": 300, "ant": 1990, "luso": "Residencial"}], ldt="CL UNIDAD 1"
        ),
    )
    transport.add(
        unit_b, synthetic.dgc_physical_xml([{"sfc": 200, "ant": 1975, "luso": "Almacen"}])
    )
    transport.add("Consulta_DNPRC", synthetic.dgc_unit_list_xml([unit_a, unit_b]))
    result = make(transport).physical_data(parcel)
    assert (
        result.ok and result.area_m2 == 500 and result.year == 1975 and result.use == "Residencial"
    )
    assert result.description == "CL UNIDAD 1"
    assert (
        result.raw["units"] == 2
        and result.raw["expanded_units"] == 2
        and not result.raw["truncated"]
    )
    assert [r.params["RC"] for r in transport.requests] == [parcel, unit_a, unit_b]


def test_physical_unit_expansion_is_capped_and_tolerates_unit_errors(transport):
    parcel = synthetic.synthetic_rc(10)
    units = [parcel + f"{i:04d}ZZ" for i in range(1, 6)]
    transport.add(units[0], synthetic.dgc_error_xml(5, "unit error"))
    transport.add(units[1], synthetic.dgc_physical_xml([{"sfc": 50, "ant": 2001}]))
    transport.add(units[2], None)
    transport.add("Consulta_DNPRC", synthetic.dgc_unit_list_xml(units))
    result = make(transport, max_units=3).physical_data(parcel)
    assert result.ok and result.area_m2 == 50 and result.year == 2001
    assert result.raw["truncated"] is True and result.raw["skipped_units"] == [units[0], units[2]]
    assert transport.calls == 4
