"""The synthetic generator must be deterministic and produce landing files."""

import pandas as pd

from vic_suburbs.generator import emit, seed

CONFIG = "config/synthetic/seed_config.yaml"
MUT = "config/synthetic/mutation_rules.yaml"


def test_seed_is_deterministic(tmp_path):
    db1 = str(tmp_path / "u1.db")
    db2 = str(tmp_path / "u2.db")
    seed.build_universe(CONFIG, db1)
    seed.build_universe(CONFIG, db2)
    import sqlite3

    for table in ["property_series", "demographics_series", "crime_series"]:
        a = pd.read_sql(f"SELECT * FROM {table}", sqlite3.connect(db1))
        b = pd.read_sql(f"SELECT * FROM {table}", sqlite3.connect(db2))
        pd.testing.assert_frame_equal(a, b)


def test_demographics_age_bands_sum_to_total(tmp_path):
    import sqlite3

    db = str(tmp_path / "u.db")
    seed.build_universe(CONFIG, db)
    d = pd.read_sql("SELECT * FROM demographics_series", sqlite3.connect(db))
    band_sum = d[["pop_0_14", "pop_15_24", "pop_25_44", "pop_45_64", "pop_65_plus"]].sum(axis=1)
    assert (band_sum == d["population_total"]).all()


def test_emit_full_writes_all_entities(tmp_path):
    import sqlite3

    db = str(tmp_path / "u.db")
    seed.build_universe(CONFIG, db)
    landing = tmp_path / "landing"
    con = sqlite3.connect(db)
    try:
        written = emit.emit_full(con, landing, "fullbase0000")
    finally:
        con.close()
    entities = {p.parent.name for p in written}
    assert {"property", "demographics", "crime", "transport", "education", "suburb_ref"} <= entities
    # measures are keyed by sal_code — no suburb/postcode columns
    prop_file = next(p for p in written if p.parent.name == "property")
    cols = pd.read_csv(prop_file).columns
    assert "sal_code" in cols and "suburb" not in cols and "postcode" not in cols
    assert "batch_id" in cols and "source_system" in cols and "effective_ts" in cols


def test_emit_update_produces_scd_changes(tmp_path):
    db = str(tmp_path / "u.db")
    seed.build_universe(CONFIG, db)
    landing = tmp_path / "landing"
    written = emit.emit("update", str(landing), db, MUT)
    # update mode should produce at least a suburb_ref change or a property restatement
    assert any(p.parent.name in ("suburb_ref", "property") for p in written)


def test_emit_new_creates_next_period_inserts(tmp_path):
    import pandas as pd

    db = str(tmp_path / "u.db")
    seed.build_universe(CONFIG, db)
    landing = tmp_path / "landing"
    written = emit.emit("new", str(landing), db, MUT)
    # only measure entities, each one period beyond history, marked _new
    assert {p.parent.name for p in written} == {
        "demographics",
        "property",
        "crime",
        "transport",
        "education",
    }
    prop = next(p for p in written if p.parent.name == "property")
    assert prop.name.endswith("_new.csv")
    df = pd.read_csv(prop)
    assert df["period"].nunique() == 1 and df["period"].iloc[0] == 2022


def test_emit_update_fires_all_mutation_branches(tmp_path):
    import yaml

    db = str(tmp_path / "u.db")
    seed.build_universe(CONFIG, db)
    mut = tmp_path / "mut.yaml"
    mut.write_text(
        yaml.safe_dump(
            {
                "seed": 1,
                "mutation_probabilities": {
                    "suburb_lga_reassignment": 1.0,
                    "suburb_rename": 1.0,
                    "boundary_revision": 1.0,
                    "price_shock": 1.0,
                },
            }
        )
    )
    written = emit.emit("update", str(tmp_path / "l"), db, str(mut))
    assert any(p.parent.name == "suburb_ref" for p in written)


def test_seed_rebuild_unlinks_existing_db(tmp_path):
    from pathlib import Path

    db = str(tmp_path / "u.db")
    seed.build_universe(CONFIG, db)
    seed.build_universe(CONFIG, db)  # second build hits the db.exists() -> unlink branch
    assert Path(db).exists()
