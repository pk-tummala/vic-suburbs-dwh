import csv
import sys
import types

import pytest

from vic_suburbs.extract import run_extract as rex


def test_unknown_connector_raises(monkeypatch):
    monkeypatch.setattr(rex, "load_source", lambda e: {"connector": "bogus"})
    with pytest.raises(KeyError):
        rex.run_extract("x", "/tmp/landing", batch_id="b")


def test_abs_connector_not_implemented(monkeypatch):
    monkeypatch.setattr(
        rex, "load_source", lambda e: {"connector": "abs_sdmx", "abs": {"dataflow": "ABS_X"}}
    )
    with pytest.raises(NotImplementedError):
        rex.run_extract("demographics", "/tmp/landing", batch_id="b")


def test_ckan_unpinned_resource_raises(monkeypatch, tmp_path):
    cfg = {"connector": "ckan", "ckan": {"resource_id": "REPLACE_ME", "base_url": "https://x"}}
    monkeypatch.setattr(rex, "load_source", lambda e: cfg)
    with pytest.raises(RuntimeError):
        rex.run_extract("property", str(tmp_path), batch_id="abcd1234")


def test_ckan_paginates_and_writes_csv(monkeypatch, tmp_path):
    pages = [
        {"result": {"records": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]}},
        {"result": {"records": []}},
    ]
    state = {"i": 0}

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    def fake_get(url, params=None, headers=None, timeout=None):
        resp = FakeResp(pages[state["i"]])
        state["i"] += 1
        return resp

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    cfg = {
        "connector": "ckan",
        "ckan": {"resource_id": "abc", "base_url": "https://x", "page_size": 1000},
    }
    monkeypatch.setattr(rex, "load_source", lambda e: cfg)
    out = rex.run_extract("property", str(tmp_path), batch_id="abcd1234")
    assert out[0].exists()
    rows = list(csv.DictReader(open(out[0])))
    assert len(rows) == 2 and rows[0]["a"] == "1"


def test_synthetic_connector_delegates(monkeypatch, tmp_path):
    from vic_suburbs.generator import emit, seed

    sentinel = [tmp_path / "stamp"]
    monkeypatch.setattr(rex, "load_source", lambda e: {"connector": "synthetic"})
    monkeypatch.setattr(seed, "build_universe", lambda cfg, db: db)
    monkeypatch.setattr(emit, "emit", lambda **kw: sentinel)
    assert rex.run_extract("property", str(tmp_path), batch_id="b") == sentinel


def test_ckan_stops_on_empty_page(monkeypatch, tmp_path):
    # full page (== page_size) forces another request; the empty page then breaks the loop
    pages = [
        {"result": {"records": [{"a": 1}, {"a": 2}]}},  # full page (page_size=2)
        {"result": {"records": []}},  # empty -> `if not records: break`
    ]
    state = {"i": 0}

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    def fake_get(url, params=None, headers=None, timeout=None):
        resp = FakeResp(pages[state["i"]])
        state["i"] += 1
        return resp

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    cfg = {
        "connector": "ckan",
        "ckan": {"resource_id": "abc", "base_url": "https://x", "page_size": 2},
    }
    monkeypatch.setattr(rex, "load_source", lambda e: cfg)
    out = rex.run_extract("crime", str(tmp_path), batch_id="abcd1234")
    assert out[0].exists()
