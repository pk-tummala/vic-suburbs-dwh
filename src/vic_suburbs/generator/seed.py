"""Phase 1 of the synthetic universe: build it once, deterministically.

Reads the suburb spine (``config/synthetic/suburb_seed.csv``) and the back-cast parameters
(``seed_config.yaml``), then projects ~50 years of plausible history for every subject area
and stores it in a SQLite database. Building the universe also writes the full back-cast as
the initial landing load; ``emit.py`` then layers incremental batches on top of it.

Every suburb identity and every projected metric is synthetic, stamped ``source_system =
SYNTHETIC`` on the way into the landing files.

Run:  python -m vic_suburbs.generator.seed --config config/synthetic/seed_config.yaml \
          --landing .local/landing
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from vic_suburbs.common.config import load_yaml
from vic_suburbs.common.lineage import new_batch_id
from vic_suburbs.generator.emit import emit_full

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


# ── Demographic age model ────────────────────────────────────────────────────
# Five ABS-style age bands. Base-year shares tilt with how urban a suburb is — denser, inner
# suburbs skew toward young adults (study/work in-migration), leafier suburbs toward children and
# mid-life. Earlier history years are made younger overall, mirroring the long national rise in
# median age across this window, so each suburb's structure ages gradually toward the present.
_AGE_EDGES = np.array([0, 15, 25, 45, 65, 90])  # band boundaries (years)
_AGE_BASE = np.array([0.19, 0.12, 0.27, 0.24, 0.18])  # base-year shares (sum 1.0)
_AGE_URBAN_TILT = np.array(
    [-0.06, 0.06, 0.10, -0.04, -0.06]
)  # zero-sum shift per unit urbanisation
_AGE_TIME_TILT = np.array([0.32, 0.22, 0.04, -0.16, -0.42])  # younger-in-the-past slope


def _age_distribution(pop, urban, tilt, back, rng):
    """Return (5 age-band counts summing to ``pop``, a consistent median age).

    ``back`` is the back-cast fraction (0 at the base year, 1 at the start of history); larger
    values make the population younger. ``tilt`` is a small per-suburb shape perturbation.
    """
    w = _AGE_BASE + urban * _AGE_URBAN_TILT + tilt
    w *= 1.0 + back * _AGE_TIME_TILT
    w = np.clip(w, 0.01, None)
    w /= w.sum()
    counts = np.round(w * pop).astype(int)
    counts[-1] = pop - counts[:-1].sum()  # force the bands to sum to population_total exactly
    cum = np.concatenate([[0.0], np.cumsum(w)])
    median_age = float(np.interp(0.5, cum, _AGE_EDGES))
    return counts, round(median_age + float(rng.normal(0, 0.4)), 1)


# ── Property / income / crime / schooling realism ────────────────────────────
_INCOME_BASE = 1000.0  # weekly household income floor before the price-linked component
_INCOME_PRICE_COEF = 0.7  # +$/wk per $1k of median house price (a rough socioeconomic gradient)
_UNIT_RATIO_MEAN = 0.78  # median unit price as a fraction of the house price...
_UNIT_RATIO_URBAN = 0.12  # ...trimmed in denser suburbs (more apartment stock)
_HOUSEHOLD_SIZE = 2.6  # persons per dwelling, for turning population into a dwelling count
_TURNOVER_RATE = 0.035  # share of dwellings that transact in a year -> sales_volume
_SCHOOL_MIN = 0.7  # earliest-year govt-school count as a fraction of today's
_ICSEA_MIN, _ICSEA_MAX = 880.0, 1200.0  # plausible band for a school-mean ICSEA


def build_universe(config_path: str, db_path: str = DEFAULT_DB) -> str:
    cfg = load_yaml(config_path)
    seed_csv = Path(config_path).parent / "suburb_seed.csv"
    spine = pd.read_csv(seed_csv, dtype={"postcode": str})
    rng = np.random.default_rng(cfg["seed"])
    # Separate stream for transport service attributes, so adding them doesn't perturb the
    # existing demographics/property/crime/education draws (those stay byte-identical on re-seed).
    trng = np.random.default_rng(cfg["seed"] + 7)
    base = cfg["base_year"]
    start = cfg["history_start_year"]
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
    income_cagr = draw("income_cagr")
    # per-suburb shape perturbations (drawn once for reproducibility)
    income_noise = rng.normal(0.0, 0.06, n)
    unit_noise = rng.normal(0.0, 0.04, n)
    age_tilt = rng.normal(0.0, 0.02, (n, 5))

    census_years = _years(cfg, census_only=True)
    annual_years = _years(cfg, census_only=False)

    demo, prop, crime, transport, edu = [], [], [], [], []

    for i, row in spine.iterrows():
        # Transport service attributes (synthetic). An urbanisation factor from population density
        # drives peak service frequency per mode (denser suburbs run more frequent services);
        # per-year coverage is derived below from stop density. These let Q2 express *connectivity*
        # (frequency x coverage x mode weight), not just a raw stop count.
        _area = max(float(row.area_sqkm), 0.1)
        _urban = min(1.0, (row.base_population / _area) / 8000.0)
        train_freq = (
            int(max(0, round((4 + 8 * _urban) + trng.normal(0, 1))))
            if row.base_train_stations
            else 0
        )
        tram_freq = (
            int(max(0, round((6 + 14 * _urban) + trng.normal(0, 1.5))))
            if row.base_tram_stops
            else 0
        )
        bus_freq = (
            int(max(0, round((3 + 7 * _urban) + trng.normal(0, 1)))) if row.base_bus_stops else 0
        )
        # per-suburb socioeconomic anchors derived from the spine (house price as the proxy)
        crime_rate = float(row.base_offences) / max(float(row.base_population), 1.0)
        base_income = (
            _INCOME_BASE + float(row.base_median_house_price) / 1000.0 * _INCOME_PRICE_COEF
        ) * (1.0 + income_noise[i])
        unit_ratio = float(
            np.clip(_UNIT_RATIO_MEAN - _UNIT_RATIO_URBAN * _urban + unit_noise[i], 0.52, 0.9)
        )
        # demographics on census cadence
        for y in census_years:
            pop = int(round(_backcast(row.base_population, base, y, pop_cagr[i], noise, rng)))
            back = (base - y) / max(1, base - start)
            counts, median_age = _age_distribution(pop, _urban, age_tilt[i], back, rng)
            demo.append(
                dict(
                    sal_code=row.sal_code,
                    period=y,
                    population_total=pop,
                    median_age=median_age,
                    pop_0_14=int(counts[0]),
                    pop_15_24=int(counts[1]),
                    pop_25_44=int(counts[2]),
                    pop_45_64=int(counts[3]),
                    pop_65_plus=int(counts[4]),
                    median_household_income_weekly=round(
                        float(
                            np.clip(
                                _backcast(base_income, base, y, income_cagr[i], noise, rng),
                                300.0,
                                8000.0,
                            )
                        ),
                        0,
                    ),
                )
            )
        # annual series for the rest, keyed by sal_code (the stable suburb surrogate)
        for y in annual_years:
            pop_y = _backcast(row.base_population, base, y, pop_cagr[i], noise / 2, rng)
            house = _backcast(row.base_median_house_price, base, y, price_cagr[i], noise, rng)
            prop.append(
                dict(
                    sal_code=row.sal_code,
                    period=y,
                    median_house_price=round(house, -3),
                    median_unit_price=round(house * unit_ratio, -3),
                    median_rent_weekly=round(
                        _backcast(row.base_median_rent_weekly, base, y, rent_cagr[i], noise, rng), 0
                    ),
                    # sales scale with the dwelling stock (population / household size)
                    sales_volume=int(
                        max(
                            0,
                            round(
                                (pop_y / _HOUSEHOLD_SIZE)
                                * _TURNOVER_RATE
                                * (1.0 + rng.normal(0, 0.15))
                            ),
                        )
                    ),
                )
            )
            # offences = a drifting per-capita rate applied to that year's population
            rate_y = (
                crime_rate * (1.0 + crime_drift[i]) ** (base - y) * (1.0 + rng.normal(0, noise))
            )
            crime.append(
                dict(
                    sal_code=row.sal_code,
                    period=y,
                    offence_count_total=int(max(0, round(rate_y * pop_y))),
                )
            )
            ratio = (y - annual_years[0]) / max(1, base - annual_years[0])
            train_c = int(row.base_train_stations)
            tram_c = int(round(row.base_tram_stops * (0.6 + 0.4 * ratio)))
            bus_c = int(round(row.base_bus_stops * (0.5 + 0.5 * ratio)))
            transport.append(
                dict(
                    sal_code=row.sal_code,
                    period=y,
                    train_station_count=train_c,
                    tram_stop_count=tram_c,
                    bus_stop_count=bus_c,
                    train_freq_peak=train_freq,
                    tram_freq_peak=tram_freq,
                    bus_freq_peak=bus_freq,
                    # coverage = share of the suburb within walking distance of a stop, from stop
                    # density x mode catchment (rail ~800 m -> 2.0 km2, tram/bus ~400 m -> 0.5 km2).
                    train_coverage=round(min(0.98, train_c * 2.0 / _area), 3),
                    tram_coverage=round(min(0.98, tram_c * 0.5 / _area), 3),
                    bus_coverage=round(min(0.98, bus_c * 0.5 / _area), 3),
                )
            )
            edu.append(
                dict(
                    sal_code=row.sal_code,
                    period=y,
                    # school stock grew with the suburb: fewer schools in the earliest years
                    govt_school_count=int(
                        max(
                            0,
                            round(
                                row.base_govt_schools * (_SCHOOL_MIN + (1 - _SCHOOL_MIN) * ratio)
                            ),
                        )
                    ),
                    mean_icsea=round(
                        float(
                            np.clip(
                                _backcast(
                                    row.base_mean_icsea, base, y, school_drift[i], noise / 3, rng
                                ),
                                _ICSEA_MIN,
                                _ICSEA_MAX,
                            )
                        ),
                        0,
                    ),
                )
            )

    # suburb_ref: one initial SCD2 version per suburb, effective from the START of the analytical
    # window — so every back-cast measure period (history_start_year onward) binds to a suburb
    # version in the Gold temporal join instead of falling through to the -1 unknown member.
    suburb_ref = spine.assign(
        asgs_edition="ASGS2021",
        effective_ts=f"{start}-01-01T00:00:00",
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
        .assign(asgs_edition="ASGS2021", effective_ts=f"{start}-01-01T00:00:00")
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


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(
        description="Build the synthetic suburb universe and emit the full 50-year baseline."
    )
    ap.add_argument("--config", default="config/synthetic/seed_config.yaml")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--landing", default=".local/landing")
    args = ap.parse_args()
    build_universe(args.config, args.db)
    con = sqlite3.connect(args.db)
    try:
        written = emit_full(con, Path(args.landing), new_batch_id())
    finally:
        con.close()
    print(f"Emitted full baseline: {len(written)} files under {args.landing}")


if __name__ == "__main__":
    main()
