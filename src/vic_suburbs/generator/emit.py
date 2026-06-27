"""Phase 2 of the synthetic universe: emit incremental landing batches (repeatable).

Reads the SQLite universe built by ``seed.py`` and writes per-entity CSV files into the
landing directory, stamped with one ``batch_id``, ``source_system = SYNTHETIC`` and a
per-row ``effective_ts``. These are exactly the files Auto Loader ingests into Bronze.

``seed`` writes the full 50-year baseline; these modes layer changes on top of it:
  new      the next period (max + 1) for every measure entity — pure inserts
  update   mutations so SCD2/CDC has changes to capture (new suburb_ref versions,
           price shocks on recent property rows)
  mixed    new + update together (the realistic default)

Run:  python -m vic_suburbs.generator.emit --mode mixed --landing .local/landing
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from vic_suburbs.common.config import load_yaml
from vic_suburbs.common.lineage import new_batch_id

DEFAULT_DB = "synthetic_universe.db"

MEASURE_TABLES = {
    "demographics": "demographics_series",
    "property": "property_series",
    "crime": "crime_series",
    "transport": "transport_series",
    "education": "education_series",
}


def _effective_ts(period: int) -> str:
    # treat each annual/census period as effective mid-year
    return f"{int(period)}-07-01T00:00:00"


def _write(
    df: pd.DataFrame, landing: Path, entity: str, batch_id: str, part: str | None = None
) -> Path:
    out_dir = landing / entity
    out_dir.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["source_system"] = "SYNTHETIC"
    df["batch_id"] = batch_id
    if "effective_ts" not in df.columns and "period" in df.columns:
        df["effective_ts"] = df["period"].map(_effective_ts)
    suffix = f"_{part}" if part else ""
    path = out_dir / f"{entity}_{batch_id[:8]}{suffix}.csv"
    df.to_csv(path, index=False)
    return path


def emit_full(con, landing: Path, batch_id: str) -> list[Path]:
    """Write the full 50-year baseline: every period of every measure entity plus the initial
    reference versions. Called by ``seed`` as the one-time initial landing load."""
    written = []
    for entity, table in MEASURE_TABLES.items():
        df = pd.read_sql(f"SELECT * FROM {table}", con)
        written.append(_write(df, landing, entity, batch_id))
    ref = pd.read_sql("SELECT * FROM suburb_ref_versions", con)
    written.append(_write(ref, landing, "suburb_ref", batch_id))
    lga = pd.read_sql("SELECT * FROM lga_ref_versions", con)
    written.append(_write(lga, landing, "lga_ref", batch_id))
    return written


def emit_update(con, landing: Path, batch_id: str, mutation_cfg: dict) -> list[Path]:
    """Produce a small change batch that exercises SCD2 and restatements."""
    rng = np.random.default_rng(mutation_cfg["seed"])
    probs = mutation_cfg["mutation_probabilities"]
    written = []

    ref = pd.read_sql("SELECT * FROM suburb_ref_versions", con)
    new_versions = []
    now_edition = "ASGS2026"
    eff = "2026-08-10T00:00:00"
    for _, r in ref.iterrows():
        changed = False
        rec = r.to_dict()
        if rng.random() < probs["suburb_rename"]:
            rec["suburb_name"] = rec["suburb_name"] + " North"
            changed = True
        if rng.random() < probs["suburb_lga_reassignment"]:
            rec["lga_code"] = "LGA99999"
            changed = True
        if rng.random() < probs["boundary_revision"]:
            rec["asgs_edition"] = now_edition
            rec["area_sqkm"] = round(float(rec["area_sqkm"]) * 1.05, 2)
            changed = True
        if changed:
            rec["effective_ts"] = eff
            new_versions.append(rec)
    if new_versions:
        written.append(
            _write(pd.DataFrame(new_versions), landing, "suburb_ref", batch_id, part="upd")
        )

    # price shocks on the most recent property year -> restatement rows
    prop = pd.read_sql("SELECT * FROM property_series", con)
    latest = prop[prop["period"] == prop["period"].max()].copy()
    mask = rng.random(len(latest)) < probs["price_shock"]
    shocked = latest[mask].copy()
    if len(shocked):
        shocked["median_house_price"] = (shocked["median_house_price"] * 1.12).round(-3)
        written.append(_write(shocked, landing, "property", batch_id, part="upd"))

    return written


def emit_new(con, landing: Path, batch_id: str) -> list[Path]:
    """Net-new records: the next period (max + 1) for every measure entity, projected one
    year forward. Pure inserts that never existed before — distinct from `update`, which
    restates existing rows / versions existing dimensions."""
    written = []
    # modest, deterministic forward uplift per measure family
    uplift = {
        "demographics": 1.012,
        "property": 1.06,
        "crime": 0.99,
        "transport": 1.0,
        "education": 1.002,
    }
    for entity, table in MEASURE_TABLES.items():
        df = pd.read_sql(f"SELECT * FROM {table}", con)
        next_period = int(df["period"].max()) + 1
        latest = df[df["period"] == df["period"].max()].copy()
        latest["period"] = next_period
        factor = uplift[entity]
        for col in latest.columns:
            if col == "period" or not pd.api.types.is_numeric_dtype(latest[col]):
                continue
            latest[col] = latest[col] * factor
            if pd.api.types.is_integer_dtype(df[col]):
                latest[col] = latest[col].round().astype(int)
            else:
                latest[col] = latest[col].round(2)
        written.append(_write(latest, landing, entity, batch_id, part="new"))
    return written


def emit(mode: str, landing: str, db_path: str, mutation_config: str) -> list[Path]:
    landing_path = Path(landing)
    batch_id = new_batch_id()
    con = sqlite3.connect(db_path)
    try:
        written: list[Path] = []
        if mode in ("new", "mixed"):
            written += emit_new(con, landing_path, batch_id)
        if mode in ("update", "mixed"):
            mut = load_yaml(mutation_config)
            written += emit_update(con, landing_path, batch_id, mut)
    finally:
        con.close()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] emit mode={mode} batch_id={batch_id} -> {len(written)} files under {landing}")
    for p in written:
        print(f"  {p}")
    return written


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Emit synthetic landing files.")
    ap.add_argument("--mode", choices=["new", "update", "mixed"], default="mixed")
    ap.add_argument("--landing", default=".local/landing")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--mutation-config", default="config/synthetic/mutation_rules.yaml")
    args = ap.parse_args()
    emit(args.mode, args.landing, args.db, args.mutation_config)


if __name__ == "__main__":
    main()
