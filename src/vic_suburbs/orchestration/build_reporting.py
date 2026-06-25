# Databricks notebook source
"""Post-pipeline task: (re)create the question-shaped serving views in ``04_reporting`` and the
operational health view in ``05_metadata``, over the Gold star schema.

Runs after the pipeline, so every fact/dim and the business run log already exist. Views are
plain ``CREATE OR REPLACE VIEW`` (no materialisation) — cheap to rebuild and AI/BI-ready. They
join each fact to ``dim_suburb`` on the surrogate key ``suburb_sk`` and to ``dim_year`` on
``year_sk``, so the suburb label is the version that was valid in the fact's period.
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
        SELECT s.sal_code, s.suburb_name, s.lga_code, y.year,
               f.population_total, f.median_age, f.median_household_income_weekly,
               f.pop_0_14, f.pop_15_24, f.pop_25_44, f.pop_45_64, f.pop_65_plus
        FROM {g}.fact_suburb_demographics f
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk""",
    # Q2 — best public-transport connectivity (latest year)
    f"""CREATE OR REPLACE VIEW {r}.vw_q2_transport_connectivity AS
        WITH latest AS (SELECT MAX(y.year) AS my FROM {g}.fact_suburb_transport f
                        JOIN {g}.dim_year y ON f.year_sk = y.year_sk)
        SELECT s.suburb_name, s.lga_code, y.year,
               f.train_station_count, f.tram_stop_count, f.bus_stop_count,
               (f.train_station_count + f.tram_stop_count + f.bus_stop_count) AS stops_total
        FROM {g}.fact_suburb_transport f
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN latest ON y.year = latest.my
        ORDER BY stops_total DESC""",
    # Q3 — lowest crime (latest year)
    f"""CREATE OR REPLACE VIEW {r}.vw_q3_low_crime AS
        WITH latest AS (SELECT MAX(y.year) AS my FROM {g}.fact_suburb_crime f
                        JOIN {g}.dim_year y ON f.year_sk = y.year_sk)
        SELECT s.suburb_name, s.lga_code, y.year, f.offence_count_total
        FROM {g}.fact_suburb_crime f
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN latest ON y.year = latest.my
        ORDER BY f.offence_count_total ASC""",
    # Q4 — best public schooling (latest year)
    f"""CREATE OR REPLACE VIEW {r}.vw_q4_top_schools AS
        WITH latest AS (SELECT MAX(y.year) AS my FROM {g}.fact_suburb_education f
                        JOIN {g}.dim_year y ON f.year_sk = y.year_sk)
        SELECT s.suburb_name, s.lga_code, y.year, f.govt_school_count, f.mean_icsea
        FROM {g}.fact_suburb_education f
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN latest ON y.year = latest.my
        ORDER BY f.mean_icsea DESC""",
    # Q5 — affordable today + growth over the available history
    f"""CREATE OR REPLACE VIEW {r}.vw_q5_affordable_growth AS
        WITH p AS (
            SELECT s.sal_code, s.suburb_name, s.lga_code, y.year, f.median_house_price
            FROM {g}.fact_suburb_property f
            JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
            JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk),
        b AS (SELECT MIN(year) AS miny, MAX(year) AS maxy FROM p)
        SELECT cur.sal_code, cur.suburb_name, cur.lga_code,
               cur.median_house_price AS current_median,
               old.median_house_price AS earliest_median,
               ROUND((cur.median_house_price - old.median_house_price)
                     / NULLIF(old.median_house_price, 0) * 100, 1) AS pct_growth
        FROM p cur JOIN b ON cur.year = b.maxy
        JOIN p old ON old.sal_code = cur.sal_code AND old.year = b.miny
        ORDER BY current_median ASC, pct_growth DESC""",
    # Q6 — most expensive (latest year)
    f"""CREATE OR REPLACE VIEW {r}.vw_q6_most_expensive AS
        WITH latest AS (SELECT MAX(y.year) AS my FROM {g}.fact_suburb_property f
                        JOIN {g}.dim_year y ON f.year_sk = y.year_sk)
        SELECT s.suburb_name, s.lga_code, y.year,
               f.median_house_price, f.median_unit_price, f.median_rent_weekly
        FROM {g}.fact_suburb_property f
        JOIN {g}.dim_year   y ON f.year_sk   = y.year_sk
        JOIN {g}.dim_suburb s ON f.suburb_sk = s.suburb_sk
        JOIN latest ON y.year = latest.my
        ORDER BY f.median_house_price DESC""",
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
