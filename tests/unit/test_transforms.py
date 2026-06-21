import pytest

from vic_suburbs.common import transforms


def test_cast_plan_from_schema(config_dir):
    from vic_suburbs.common import config

    plan = transforms.build_cast_plan(config.load_schema("property", config_dir))
    cols = dict(plan)
    assert cols["median_house_price"] == "double"
    assert cols["sales_volume"] == "int"


def test_cast_plan_rejects_unknown_type():
    with pytest.raises(ValueError):
        transforms.build_cast_plan({"columns": [{"name": "x", "type": "frobnicate"}]})


def test_normalize_suburb_name():
    assert transforms.normalize_suburb_name("  st   kilda ") == "ST KILDA"
    assert transforms.normalize_suburb_name(None) == ""
