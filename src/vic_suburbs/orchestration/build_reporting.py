# Databricks notebook source
"""Post-pipeline task: (re)create the question-shaped serving views in ``04_reporting`` and the
operational health view in ``05_metadata``, over the Gold star schema.

Runs after the pipeline, so every fact/dim and the business run log already exist. Views are
plain ``CREATE OR REPLACE VIEW`` (no materialisation) — cheap to rebuild and AI/BI-ready. They
join each fact to ``dim_suburb`` on the surrogate key ``suburb_sk`` and to ``dim_year`` on
``year_sk``, so the suburb label is the version that was valid in the fact's period.

Money/large-count columns are exposed twice: the raw numeric column (so AI/BI can sort and
aggregate) plus a ``*_fmt`` string column with thousands separators (``format_number(col, 0)``)
for display. Charts/sorts should bind the numeric column; tables can show the ``_fmt`` one.
"""

dbutils.widgets.text("catalog", "vic_suburbs_dev")  # noqa: F821
dbutils.widgets.text("env", "")  # noqa: F821
dbutils.widgets.text("src_path", "")  # noqa: F821
dbutils.widgets.text("run_id", "")  # noqa: F821

cat = dbutils.widgets.get("catalog")  # noqa: F821
g = f"`{cat}`.`03_gold`"
r = f"`{cat}`.`04_reporting`"
m = f"`{cat}`.`05_metadata`"

STATEMENTS = [
    # Q1 — population & demographic profile over time (per suburb, all census years)
    f"""CREATE OR REPLACE VIEW {r}.vw_q1_population_growth AS
        SELECT s.sal_code, s.suburb_name, s.lga_code, l.lga_name, y.year,
               f.population_total,
               format_number(f.population_total, 0) AS population_total_fmt,
               f.median_age,
               f.median_household_income_weekly,
               format_number(f.median_household_income_weekly, 0) AS median_household_income_weekly_fmt,
               f.pop_0_14, f.pop_15_24, f.pop_25_44, f.pop_45_64, f.pop_65_plus
        FROM {g}.fact_suburb_demographics f
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN (SELECT lga_code, MAX(lga_name) AS lga_name FROM {g}.dim_lga GROUP BY lga_code) l
             ON l.lga_code = s.lga_code
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk""",
    # Q2 — best public-transport connectivity (latest year)
    # Q2 — public-transport connectivity (latest year). connectivity_index weights each mode by
    # stops x (peak frequency / reference) x coverage x mode weight (rail > tram > bus), then
    # normalises to 0–100 against the most-connected suburb. Tune the weights here, not in the
    # pipeline — this is a serving-layer business metric over the fact's raw measures.
    f"""CREATE OR REPLACE VIEW {r}.vw_q2_transport_connectivity AS
        WITH latest AS (SELECT MAX(y.year) AS my FROM {g}.fact_suburb_transport f
                        JOIN {g}.dim_year y ON f.year_sk = y.year_sk),
        base AS (
            SELECT s.suburb_name, s.lga_code, l.lga_name, y.year,
                   f.train_station_count, f.tram_stop_count, f.bus_stop_count,
                   f.train_freq_peak, f.tram_freq_peak, f.bus_freq_peak,
                   f.train_coverage, f.tram_coverage, f.bus_coverage,
                   (f.train_station_count + f.tram_stop_count + f.bus_stop_count) AS stops_total,
                   ( f.train_station_count * (f.train_freq_peak / 6.0)  * f.train_coverage * 1.0
                   + f.tram_stop_count     * (f.tram_freq_peak  / 10.0) * f.tram_coverage  * 0.7
                   + f.bus_stop_count      * (f.bus_freq_peak   / 6.0)  * f.bus_coverage   * 0.4
                   ) AS conn_raw
            FROM {g}.fact_suburb_transport f
            JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
            JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
            JOIN (SELECT lga_code, MAX(lga_name) AS lga_name FROM {g}.dim_lga GROUP BY lga_code) l
                 ON l.lga_code = s.lga_code
            JOIN latest ON y.year = latest.my)
        SELECT suburb_name, lga_code, lga_name, year,
               train_station_count, tram_stop_count, bus_stop_count, stops_total,
               train_freq_peak, tram_freq_peak, bus_freq_peak,
               train_coverage, tram_coverage, bus_coverage,
               ROUND(conn_raw, 2) AS connectivity_raw,
               ROUND(100 * conn_raw / NULLIF(MAX(conn_raw) OVER (), 0), 1) AS connectivity_index
        FROM base
        ORDER BY connectivity_index DESC""",
    # Q3 — crime profile across ALL census years (per-suburb trend; dashboard filters to a suburb
    # for the time series, or derives the latest year downstream for ranking/composite scores).
    f"""CREATE OR REPLACE VIEW {r}.vw_q3_low_crime AS
        SELECT s.suburb_name, s.lga_code, l.lga_name, y.year, f.offence_count_total
        FROM {g}.fact_suburb_crime f
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN (SELECT lga_code, MAX(lga_name) AS lga_name FROM {g}.dim_lga GROUP BY lga_code) l
             ON l.lga_code = s.lga_code
        ORDER BY s.suburb_name, y.year""",
    # Q4 — best public schooling (latest year)
    f"""CREATE OR REPLACE VIEW {r}.vw_q4_top_schools AS
        WITH latest AS (SELECT MAX(y.year) AS my FROM {g}.fact_suburb_education f
                        JOIN {g}.dim_year y ON f.year_sk = y.year_sk)
        SELECT s.suburb_name, s.lga_code, l.lga_name, y.year, f.govt_school_count, f.mean_icsea
        FROM {g}.fact_suburb_education f
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN (SELECT lga_code, MAX(lga_name) AS lga_name FROM {g}.dim_lga GROUP BY lga_code) l
             ON l.lga_code = s.lga_code
        JOIN latest ON y.year = latest.my
        ORDER BY f.mean_icsea DESC""",
    # Q5 — affordable today + growth. pct_growth is the raw 50-year total (huge, time-dominated);
    # cagr_pct annualises it ((current/earliest)^(1/years) - 1) so suburbs are comparable on a sane
    # ~single-digit %/yr scale. The dashboard plots current price vs cagr_pct.
    f"""CREATE OR REPLACE VIEW {r}.vw_q5_affordable_growth AS
        WITH p AS (
            SELECT s.sal_code, s.suburb_name, s.lga_code, l.lga_name, y.year, f.median_house_price
            FROM {g}.fact_suburb_property f
            JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
            JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
            JOIN (SELECT lga_code, MAX(lga_name) AS lga_name FROM {g}.dim_lga GROUP BY lga_code) l
                 ON l.lga_code = s.lga_code),
        b AS (SELECT MIN(year) AS miny, MAX(year) AS maxy FROM p)
        SELECT cur.sal_code, cur.suburb_name, cur.lga_code, cur.lga_name,
               cur.median_house_price AS current_median,
               format_number(cur.median_house_price, 0) AS current_median_fmt,
               old.median_house_price AS earliest_median,
               format_number(old.median_house_price, 0) AS earliest_median_fmt,
               ROUND((cur.median_house_price - old.median_house_price)
                     / NULLIF(old.median_house_price, 0) * 100, 1) AS pct_growth,
               ROUND((POWER(cur.median_house_price / NULLIF(old.median_house_price, 0),
                            1.0 / NULLIF(b.maxy - b.miny, 0)) - 1) * 100, 1) AS cagr_pct
        FROM p cur JOIN b ON cur.year = b.maxy
        JOIN p old ON old.sal_code = cur.sal_code AND old.year = b.miny
        ORDER BY current_median ASC, cagr_pct DESC""",
    # Q6 — price profile across ALL census years (per-suburb trend; dashboard filters to a suburb
    # for the time series, or derives the latest year downstream for the composite score).
    f"""CREATE OR REPLACE VIEW {r}.vw_q6_most_expensive AS
        SELECT s.suburb_name, s.lga_code, l.lga_name, y.year,
               f.median_house_price,
               format_number(f.median_house_price, 0) AS median_house_price_fmt,
               f.median_unit_price,
               format_number(f.median_unit_price, 0) AS median_unit_price_fmt,
               f.median_rent_weekly,
               format_number(f.median_rent_weekly, 0) AS median_rent_weekly_fmt
        FROM {g}.fact_suburb_property f
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN (SELECT lga_code, MAX(lga_name) AS lga_name FROM {g}.dim_lga GROUP BY lga_code) l
             ON l.lga_code = s.lga_code
        ORDER BY s.suburb_name, y.year""",
    # Observability — per-run pipeline health (business run log)
    f"""CREATE OR REPLACE VIEW {m}.vw_pipeline_health AS
        SELECT run_id, pipeline_name, env, status, trigger,
               rows_read, rows_written, started_at, ended_at,
               timestampdiff(SECOND, started_at, ended_at) AS duration_seconds,
               error_class, error_message
        FROM {m}.pipeline_run_log
        ORDER BY started_at DESC""",
]

for stmt in STATEMENTS:
    spark.sql(stmt)  # noqa: F821

print(f"reporting: created {len(STATEMENTS)} views in {cat} (04_reporting + 05_metadata)")  # noqa
