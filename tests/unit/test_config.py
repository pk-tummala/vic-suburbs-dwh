from vic_suburbs.common import config


def test_entities_registered(config_dir):
    names = config.entity_names(config_dir)
    assert {
        "property",
        "demographics",
        "crime",
        "transport",
        "education",
        "suburb_ref",
        "lga_ref",
    } <= set(names)


def test_entity_config_merges(config_dir):
    cfg = config.load_entity_config("property", config_dir)
    assert cfg["source"]["connector"] == "ckan"
    assert cfg["schema"]["entity"] == "property"
    assert any(r["name"] == "sal_code_not_null" for r in cfg["dq_rules"])


def test_reference_entity_has_scd(config_dir):
    manifest = {e["name"]: e for e in config.load_entities(config_dir)}
    assert manifest["suburb_ref"]["kind"] == "reference"
    assert manifest["suburb_ref"]["scd"]["keys"] == ["sal_code"]


def test_unknown_entity_raises(config_dir):
    import pytest

    with pytest.raises(KeyError):
        config.load_entity_config("does_not_exist", config_dir)
