"""Phase 1 of the synthetic universe: build it once, deterministically.

Reads the real suburb spine (``config/synthetic/suburb_seed.csv``) and the back-cast
parameters (``seed_config.yaml``), then projects ~50 years of plausible history for every
subject area and stores it in a SQLite database. ``emit.py`` reads this database to produce
landing files.

The spine (suburb identities) is treated as real; every projected *metric* is synthetic and
flagged as such downstream via ``source_system = SYNTHETIC``.

Run:  python -m vic_suburbs.generator.seed --config config/synthetic/seed_config.yaml
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from vic_suburbs.common.config import load_yaml

DEFAULT_DB = "synthetic_universe.db"


def _years(cfg: dict, census_only: bool) -> list[int]:
    start, base = cfg["history_start_year"], cfg["base_year"]
    if census_only and cfg.get("census_years_only"):
        step = cfg.get("census_step", 5)
        ys = list(range(base, start - 1, -step))
        return sorted(ys)
    return list(range(start, base + 1))


def _backcast(
    base_value: float, base_year: int, year: int, cagr: float, noise: float, rng
) -> float:
    """Project a base-year value back to ``year`` along a CAGR with multiplicative noise."""
    factor = (1.0 + cagr) ** (base_year - year)
    val = base_value / factor
    val *= 1.0 + rng.normal(0.0, noise)
    return max(val, 0.0)


def build_universe(config_path: str, db_path: str = DEFAULT_DB) -> str:
    cfg = load_yaml(config_path)
    seed_csv = Path(config_path).parent / "suburb_seed.csv"
    spine = pd.read_csv(seed_csv, dtype={"postcode": str})
    rng = np.random.default_rng(cfg["seed"])
    base = cfg["base_year"]
    noise = cfg["noise_pct"]

    # per-suburb growth rates (drawn once, reproducibly)
    n = len(spine)

    def draw(key):
        g = cfg["growth"][key]
        return rng.normal(g["mean"], g["sd"], n)

    pop_cagr = draw("population_cagr")
    price_cagr = draw("price_cagr")
    rent_cagr = draw("rent_cagr")
    crime_drift = draw("crime_drift")
    school_drift = draw("school_drift")

    census_years = _years(cfg, census_only=True)
    annual_years = _years(cfg, census_only=False)

    demo, prop, crime, transport, edu = [], [], [], [], []

    for i, row in spine.iterrows():
        # demographics on census cadence
        for y in census_years:
            pop = _backcast(row.base_population, base, y, pop_cagr[i], noise, rng)
            pop = int(round(pop))
            bands = np.array([0.18, 0.13, 0.30, 0.24, 0.15])
            counts = np.round(bands * pop).astype(int)
            counts[-1] = pop - counts[:-1].sum()  # force exact sum
            demo.append(
                dict(
                    sal_code=row.sal_code,
                    period=y,
                    population_total=pop,
                    median_age=round(34 + rng.normal(0, 3), 1),
                    pop_0_14=int(counts[0]),
                    pop_15_24=int(counts[1]),
                    pop_25_44=int(counts[2]),
                    pop_45_64=int(counts[3]),
                    pop_65_plus=int(counts[4]),
                    median_household_income_weekly=round(
                        _backcast(1700, base, y, 0.03, noise, rng), 0
                    ),
                )
            )
        # annual series for the rest (free-text source style: suburb + postcode, no sal_code)
        for y in annual_years:
            prop.append(
                dict(
                    suburb=row.suburb_name,
                    postcode=row.postcode,
                    period=y,
                    median_house_price=round(
                        _backcast(row.base_median_house_price, base, y, price_cagr[i], noise, rng),
                        -3,
                    ),
                    median_unit_price=round(
                        _backcast(
                            row.base_median_house_price * 0.72, base, y, price_cagr[i], noise, rng
                        ),
                        -3,
                    ),
                    median_rent_weekly=round(
                        _backcast(row.base_median_rent_weekly, base, y, rent_cagr[i], noise, rng), 0
                    ),
                    sales_volume=int(max(0, round(rng.normal(80, 20)))),
                )
            )
            crime.append(
                dict(
                    suburb=row.suburb_name,
                    postcode=row.postcode,
                    period=y,
                    offence_count_total=int(
                        _backcast(row.base_offences, base, y, crime_drift[i], noise, rng)
                    ),
                )
            )
            transport.append(
                dict(
                    suburb=row.suburb_name,
                    postcode=row.postcode,
                    period=y,
                    train_station_count=int(row.base_train_stations),
                    tram_stop_count=int(
                        round(
                            row.base_tram_stops
                            * (0.6 + 0.4 * (y - annual_years[0]) / max(1, base - annual_years[0]))
                        )
                    ),
                    bus_stop_count=int(
                        round(
                            row.base_bus_stops
                            * (0.5 + 0.5 * (y - annual_years[0]) / max(1, base - annual_years[0]))
                        )
                    ),
                )
            )
            edu.append(
                dict(
                    suburb=row.suburb_name,
                    postcode=row.postcode,
                    period=y,
                    govt_school_count=int(row.base_govt_schools),
                    mean_icsea=round(
                        _backcast(row.base_mean_icsea, base, y, school_drift[i], noise / 3, rng), 0
                    ),
                )
            )

    # suburb_ref: one initial SCD2 version per suburb (effective at the base ASGS edition)
    suburb_ref = spine.assign(
        asgs_edition="ASGS2021",
        effective_ts=f"{base}-08-10T00:00:00",
    )[
        [
            "sal_code",
            "suburb_name",
            "postcode",
            "lga_code",
            "region",
            "asgs_edition",
            "area_sqkm",
            "effective_ts",
        ]
    ]
    lga_ref = (
        spine[["lga_code", "lga_name", "lga_type"]]
        .drop_duplicates()
        .assign(asgs_edition="ASGS2021", effective_ts=f"{base}-08-10T00:00:00")
    )

    db = Path(db_path)
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db_path)
    spine.to_sql("spine", con, index=False)
    pd.DataFrame(demo).to_sql("demographics_series", con, index=False)
    pd.DataFrame(prop).to_sql("property_series", con, index=False)
    pd.DataFrame(crime).to_sql("crime_series", con, index=False)
    pd.DataFrame(transport).to_sql("transport_series", con, index=False)
    pd.DataFrame(edu).to_sql("education_series", con, index=False)
    suburb_ref.to_sql("suburb_ref_versions", con, index=False)
    lga_ref.to_sql("lga_ref_versions", con, index=False)
    con.commit()
    con.close()

    print(
        f"Built {db_path}: {n} suburbs | "
        f"{len(demo)} demo · {len(prop)} property · {len(crime)} crime · "
        f"{len(transport)} transport · {len(edu)} education rows "
        f"({annual_years[0]}–{base})"
    )
    return db_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the synthetic suburb universe.")
    ap.add_argument("--config", default="config/synthetic/seed_config.yaml")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    build_universe(args.config, args.db)


if __name__ == "__main__":
    main()
