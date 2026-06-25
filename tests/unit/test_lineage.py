import re

import pytest

from vic_suburbs.common import lineage


def test_new_batch_id_is_unique_uuid():
    b = lineage.new_batch_id()
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", b)
    assert lineage.new_batch_id() != b


def test_utc_now_iso_is_utc():
    assert "+00:00" in lineage.utc_now_iso()


def test_validate_source_system_accepts_known():
    assert lineage.validate_source_system("SYNTHETIC") == "SYNTHETIC"


def test_validate_source_system_rejects_unknown():
    with pytest.raises(ValueError):
        lineage.validate_source_system("NOPE")
