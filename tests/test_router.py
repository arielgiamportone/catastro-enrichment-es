import pytest

from catastro_enrich.cadastre import (
    AlavaDriver,
    BizkaiaDriver,
    DgcDriver,
    GipuzkoaDriver,
    NavarraDriver,
    driver_name_for,
    province_code,
    resolve_cadastre_driver,
)


@pytest.mark.parametrize(
    ("ine", "expected"),
    [
        ("20069", "gipuzkoa"),
        ("48020", "bizkaia"),
        ("31201", "navarra"),
        ("01059", "alava"),
        ("28079", "dgc"),
        ("99999", "dgc"),
    ],
)
def test_router_by_ine_code(ine, expected):
    assert resolve_cadastre_driver(ine).name == expected
    assert resolve_cadastre_driver(ine_code=ine).name == expected


def test_router_accepts_two_digit_and_int_codes():
    assert resolve_cadastre_driver(20).name == "gipuzkoa"
    assert resolve_cadastre_driver("48").name == "bizkaia"
    assert resolve_cadastre_driver(1).name == "alava"
    assert (
        province_code("31201") == "31" and province_code(48) == "48" and province_code("xx") is None
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Guipuzcoa", "gipuzkoa"),
        ("Guipúzcoa", "gipuzkoa"),
        ("Vizcaya", "bizkaia"),
        ("Araba", "alava"),
        ("Álava", "alava"),
        ("Nafarroa", "navarra"),
        ("Madrid", "dgc"),
    ],
)
def test_router_by_name_with_synonyms(name, expected):
    assert resolve_cadastre_driver(name).name == expected
    assert resolve_cadastre_driver(province_name=name).name == expected


def test_ine_code_wins_over_contradicting_name():
    driver = resolve_cadastre_driver(ine_code="28079", province_name="Vizcaya")
    assert isinstance(driver, DgcDriver)
    assert resolve_cadastre_driver(ine_code="48020", province_name="Madrid").name == "bizkaia"


def test_unknown_province_falls_back_to_dgc_without_raising():
    driver = resolve_cadastre_driver("Provincia Inexistente")
    assert isinstance(driver, DgcDriver)
    assert driver.territory.province == "Provincia Inexistente"
    assert isinstance(resolve_cadastre_driver(None), DgcDriver)
    assert isinstance(resolve_cadastre_driver(""), DgcDriver)


def test_driver_classes_and_options():
    assert isinstance(resolve_cadastre_driver("01"), AlavaDriver)
    assert isinstance(resolve_cadastre_driver("20"), GipuzkoaDriver)
    assert isinstance(resolve_cadastre_driver("31"), NavarraDriver)
    assert isinstance(resolve_cadastre_driver("48"), BizkaiaDriver)
    driver = resolve_cadastre_driver("28079", dgc_territory=("Madrid", "Madrid"), delay=0.9)
    assert driver.delay == 0.9 and driver.territory.params()["Municipio"] == "MADRID"
    assert driver_name_for("20") == "gipuzkoa" and driver_name_for(None) == "dgc"
