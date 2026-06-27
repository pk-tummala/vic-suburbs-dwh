#!/usr/bin/env python3
r"""Generate the AI/BI dashboard definition (``resources/dashboards/vic_suburbs.lvdash.json``).

The Lakeview ``.lvdash.json`` widget schema is verbose and repetitive (each table column carries
~20 keys), so it is generated here rather than hand-authored — the same approach as
``tools/build-er-diagram.py``. Edit the dashboard *intent* below (datasets, KPIs, tables, charts)
and re-run; the script fills in the boilerplate and writes valid JSON.

The widget specs (counter v2, bar v3, line v3, scatter/bubble v3, filter-multi-select v2, table v1) follow the shapes used
by Databricks' own ``bundle-examples`` dashboards, so the output imports cleanly via DABs.

Queries are **schema-qualified but catalog-less** (e.g. ``\`04_reporting\`.vw_q1_...``). The bundle's
dashboard resource sets ``dataset_catalog: ${var.catalog}``, so the catalog is injected per target
(dev/qa/prod) at deploy time — one dashboard file, three environments, no edits.

Usage:  python3 tools/build_dashboard.py [--out resources/dashboards/vic_suburbs.lvdash.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPORTING = "`04_reporting`"
METADATA = "`05_metadata`"


def _schemas(catalog: str | None) -> tuple[str, str]:
    """Return (reporting, metadata) schema refs. If a catalog is given, fully-qualify them
    (fallback for CLIs without dataset_catalog); otherwise stay catalog-less for ${var.catalog}."""
    if catalog:
        return f"`{catalog}`.`04_reporting`", f"`{catalog}`.`05_metadata`"
    return REPORTING, METADATA


# Databricks AI/BI categorical palette (from the bundle-examples dashboard).
PALETTE = [
    "#077A9D",
    "#FFAB00",
    "#00A972",
    "#FF3621",
    "#8BCAE7",
    "#AB4057",
    "#99DDB4",
    "#FCA4A1",
    "#919191",
    "#BF7080",
]


def _id(key: str) -> str:
    """Stable 8-char hex id for a dataset/page/widget (Lakeview convention)."""
    return hashlib.md5(key.encode()).hexdigest()[:8]


def dataset(key: str, display: str, sql: str) -> dict:
    return {"name": _id(key), "displayName": display, "query": sql}


def _col(
    field: str,
    display: str,
    *,
    display_as: str,
    col_type: str,
    number_format: str | None = None,
    order: int = 0,
    align: str | None = None,
) -> dict:
    """One table column with the full Lakeview key set (defaults from bundle-examples)."""
    col = {
        "alignContent": align or ("right" if display_as == "number" else "left"),
        "allowHTML": False,
        "allowSearch": False,
        "booleanValues": ["false", "true"],
        "displayAs": display_as,
        "displayName": display,
        "fieldName": field,
        "highlightLinks": False,
        "imageHeight": "",
        "imageTitleTemplate": "{{ @ }}",
        "imageUrlTemplate": "{{ @ }}",
        "imageWidth": "",
        "linkOpenInNewTab": True,
        "linkTextTemplate": "{{ @ }}",
        "linkTitleTemplate": "{{ @ }}",
        "linkUrlTemplate": "{{ @ }}",
        "order": 100000 + order,
        "preserveWhitespace": False,
        "title": display,
        "type": col_type,
        "useMonospaceFont": False,
        "visible": True,
    }
    if number_format is not None:
        col["numberFormat"] = number_format
    return col


def table(key: str, dataset_key: str, title: str, columns: list[dict], pos: dict) -> dict:
    fields = [{"name": c["fieldName"], "expression": f"`{c['fieldName']}`"} for c in columns]
    return {
        "widget": {
            "name": _id(key),
            "queries": [
                {
                    "name": "main_query",
                    "query": {
                        "datasetName": _id(dataset_key),
                        "fields": fields,
                        "disaggregated": True,
                    },
                }
            ],
            "spec": {
                "version": 1,
                "widgetType": "table",
                "allowHTMLByDefault": False,
                "condensed": True,
                "withRowNumber": False,
                "itemsPerPage": 25,
                "paginationSize": "default",
                "encodings": {"columns": columns},
                "invisibleColumns": [],
                "frame": {"showTitle": True, "title": title},
            },
        },
        "position": pos,
    }


def counter(key: str, dataset_key: str, expr: str, field: str, title: str, pos: dict) -> dict:
    return {
        "widget": {
            "name": _id(key),
            "queries": [
                {
                    "name": "main_query",
                    "query": {
                        "datasetName": _id(dataset_key),
                        "fields": [{"name": field, "expression": expr}],
                        "disaggregated": False,
                    },
                }
            ],
            "spec": {
                "version": 2,
                "widgetType": "counter",
                "encodings": {"value": {"fieldName": field, "displayName": title}},
                "frame": {"showTitle": True, "title": title},
            },
        },
        "position": pos,
    }


def bar(
    key: str,
    dataset_key: str,
    x_field: str,
    x_expr: str,
    x_title: str,
    y_field: str,
    y_expr: str,
    y_title: str,
    title: str,
    pos: dict,
    color_field: str | None = None,
    color_expr: str | None = None,
    color_title: str | None = None,
    color_mappings: list[dict] | None = None,
    colors: list[str] | None = None,
    description: str | None = None,
) -> dict:
    fields = [
        {"name": x_field, "expression": x_expr},
        {"name": y_field, "expression": y_expr},
    ]
    encodings = {
        "x": {
            "fieldName": x_field,
            "displayName": x_title,
            "scale": {"type": "categorical"},
            "axis": {"title": x_title},
        },
        "y": {
            "fieldName": y_field,
            "displayName": y_title,
            "scale": {"type": "quantitative"},
            "axis": {"title": y_title},
        },
        "label": {"show": False},
    }
    if color_field:
        # A color/series dimension on a bar produces a STACKED bar by default (cumulative).
        fields.append({"name": color_field, "expression": color_expr})
        scale: dict = {"type": "categorical"}
        if color_mappings:
            scale["mappings"] = color_mappings
        encodings["color"] = {
            "fieldName": color_field,
            "displayName": color_title or color_field,
            "scale": scale,
        }
    spec_mark = {"colors": colors or PALETTE}
    frame = {"showTitle": True, "title": title}
    if description:
        frame["showDescription"] = True
        frame["description"] = description
    return {
        "widget": {
            "name": _id(key),
            "queries": [
                {
                    "name": "main_query",
                    "query": {
                        "datasetName": _id(dataset_key),
                        "fields": fields,
                        "disaggregated": False,
                    },
                }
            ],
            "spec": {
                "version": 3,
                "widgetType": "bar",
                "encodings": encodings,
                "frame": frame,
                "mark": spec_mark,
            },
        },
        "position": pos,
    }


def point(
    key: str,
    dataset_key: str,
    x_field: str,
    x_expr: str,
    x_title: str,
    y_field: str,
    y_expr: str,
    y_title: str,
    title: str,
    pos: dict,
    size_field: str | None = None,
    size_expr: str | None = None,
    size_title: str | None = None,
    color_field: str | None = None,
    color_expr: str | None = None,
    color_title: str | None = None,
    description: str | None = None,
    x_domain: list[float] | None = None,
    y_domain: list[float] | None = None,
) -> dict:
    """Scatter (and, when ``size_field`` is given, bubble) widget — schema v3.

    A bubble chart is a scatter with a ``size`` encoding (Databricks: "to make a bubble chart,
    select Scatter and set the Size metric"). Points are plotted disaggregated (one row = one
    marker), so axes are quantitative and no aggregation is applied — the suburb identity rides
    in the tooltip rather than on a category axis, which is what keeps 25 suburbs clutter-free.
    ``x_domain``/``y_domain`` pad the axes (the documented custom min/max) so big edge markers are
    not clipped by the plot boundary.
    """
    x_scale: dict = {"type": "quantitative"}
    y_scale: dict = {"type": "quantitative"}
    if x_domain:
        x_scale["domain"] = x_domain
    if y_domain:
        y_scale["domain"] = y_domain
    fields = [
        {"name": x_field, "expression": x_expr},
        {"name": y_field, "expression": y_expr},
    ]
    encodings = {
        "x": {
            "fieldName": x_field,
            "displayName": x_title,
            "scale": x_scale,
            "axis": {"title": x_title},
        },
        "y": {
            "fieldName": y_field,
            "displayName": y_title,
            "scale": y_scale,
            "axis": {"title": y_title},
        },
    }
    if size_field:
        fields.append({"name": size_field, "expression": size_expr})
        encodings["size"] = {
            "fieldName": size_field,
            "displayName": size_title or size_field,
            "scale": {"type": "quantitative"},
        }
    if color_field:
        fields.append({"name": color_field, "expression": color_expr})
        encodings["color"] = {
            "fieldName": color_field,
            "displayName": color_title or color_field,
            "scale": {"type": "categorical"},
        }
    frame = {"showTitle": True, "title": title}
    if description:
        frame["showDescription"] = True
        frame["description"] = description
    return {
        "widget": {
            "name": _id(key),
            "queries": [
                {
                    "name": "main_query",
                    "query": {
                        "datasetName": _id(dataset_key),
                        "fields": fields,
                        "disaggregated": True,
                    },
                }
            ],
            "spec": {
                "version": 3,
                "widgetType": "scatter",
                "encodings": encodings,
                "frame": frame,
            },
        },
        "position": pos,
    }


def line(
    key: str,
    dataset_key: str,
    x_field: str,
    x_expr: str,
    x_title: str,
    y_field: str,
    y_expr: str,
    y_title: str,
    series_field: str,
    series_expr: str,
    series_title: str,
    title: str,
    pos: dict,
) -> dict:
    """Multi-series line widget — schema v3. ``y`` is aggregated (identity over the unique
    suburb x year grain) and ``series_field`` (color) splits one line per suburb. Numeric x/y
    axes mean no category-axis labels to overlap; suburb names live in the legend."""
    fields = [
        {"name": series_field, "expression": series_expr},
        {"name": x_field, "expression": x_expr},
        {"name": y_field, "expression": y_expr},
    ]
    encodings = {
        "x": {
            "fieldName": x_field,
            "displayName": x_title,
            "scale": {"type": "quantitative"},
            "axis": {"title": x_title},
        },
        "y": {
            "fieldName": y_field,
            "displayName": y_title,
            "scale": {"type": "quantitative"},
            "axis": {"title": y_title},
        },
        "color": {
            "fieldName": series_field,
            "displayName": series_title,
            "scale": {"type": "categorical"},
        },
        "label": {"show": False},
    }
    return {
        "widget": {
            "name": _id(key),
            "queries": [
                {
                    "name": "main_query",
                    "query": {
                        "datasetName": _id(dataset_key),
                        "fields": fields,
                        "disaggregated": False,
                    },
                }
            ],
            "spec": {
                "version": 3,
                "widgetType": "line",
                "encodings": encodings,
                "frame": {"showTitle": True, "title": title},
            },
        },
        "position": pos,
    }


def filter_multi(
    key: str,
    title: str,
    targets: list[tuple[str, str]],
    pos: dict,
    dash_id: str,
    default_values: list[str] | None = None,
) -> dict:
    """Associative multi-select dropdown that filters one or more datasets by a shared field.

    ``targets`` is a list of ``(dataset_key, field)`` pairs — the same field (e.g. ``suburb_name``)
    across several datasets, so one selector drives every chart built on those datasets. With no
    value picked the filter is inert (all rows shown); pick one or many to focus. Mirrors the
    field-filter shape Databricks' own bundle-examples emit (note the associativity predicate).
    ``default_values`` pre-selects values so the dependent charts open focused rather than showing
    everything at once."""
    queries = []
    enc_fields = []
    for ds_key, field in targets:
        ds_id = _id(ds_key)
        qname = f"dashboards/{dash_id}/datasets/{ds_id}_{field}"
        queries.append(
            {
                "name": qname,
                "query": {
                    "datasetName": ds_id,
                    "disaggregated": False,
                    "fields": [
                        {"name": field, "expression": f"`{field}`"},
                        {
                            "name": f"{field}_associativity",
                            "expression": "COUNT_IF(`associative_filter_predicate_group`)",
                        },
                    ],
                },
            }
        )
        enc_fields.append({"displayName": field, "fieldName": field, "queryName": qname})
    spec = {
        "version": 2,
        "widgetType": "filter-multi-select",
        "encodings": {"fields": enc_fields},
        "frame": {"showTitle": True, "title": title},
    }
    if default_values:
        spec["selection"] = {
            "defaultSelection": {
                "values": {
                    "dataType": "STRING",
                    "values": [{"value": v} for v in default_values],
                }
            }
        }
    return {"widget": {"name": _id(key), "queries": queries, "spec": spec}, "position": pos}


def markdown(key: str, text: str, pos: dict) -> dict:
    """Markdown text widget. Uses ``textbox_spec`` (a single Markdown string, the shape Databricks'
    own dashboards emit) so headings, paragraphs (blank line between), bold and images render."""
    return {"widget": {"name": _id(key), "textbox_spec": text}, "position": pos}


def build(reporting: str = REPORTING, metadata: str = METADATA) -> dict:
    # Stable internal id used to wire the associative suburb filter to its target datasets.
    dash_id = _id("vic_suburbs_dashboard")

    # Each dataset selects EXACTLY the columns its widget binds.
    datasets = [
        dataset(
            "ds_kpi",
            "KPIs",
            f"SELECT sal_code, lga_code, year FROM {reporting}.vw_q1_population_growth",
        ),
        # Q1/Q3/Q6 are per-suburb TRENDS (one row per suburb x census year). The shared Suburb
        # filter targets these three so one selector drives the whole suburb profile.
        # Trend datasets carry lga_name too, so BOTH the Suburb and Region filters can target every
        # chart. With both fields present on each dataset the two filters become associative: picking
        # a council greys out non-member suburbs and slices the trends; picking suburbs narrows the
        # council list. That linkage is what keeps every chart consistent (no unrelated suburbs).
        dataset(
            "ds_q1",
            "Q1 population growth",
            f"SELECT suburb_name, lga_name, year, population_total "
            f"FROM {reporting}.vw_q1_population_growth",
        ),
        dataset(
            "ds_q3",
            "Q3 crime over time",
            f"SELECT suburb_name, lga_name, year, offence_count_total "
            f"FROM {reporting}.vw_q3_low_crime",
        ),
        dataset(
            "ds_q6",
            "Q6 house price over time",
            f"SELECT suburb_name, lga_name, year, median_house_price "
            f"FROM {reporting}.vw_q6_most_expensive",
        ),
        # Q2 — transport stops folded long (one row per suburb x mode) so colour stacks by mode.
        # lga_name is carried so the Region filter can slice this chart by council name.
        dataset(
            "ds_q2",
            "Q2 transport connectivity",
            "SELECT suburb_name, lga_name, 'Train' AS mode, train_station_count AS stops "
            f"FROM {reporting}.vw_q2_transport_connectivity "
            "UNION ALL SELECT suburb_name, lga_name, 'Tram', tram_stop_count "
            f"FROM {reporting}.vw_q2_transport_connectivity "
            "UNION ALL SELECT suburb_name, lga_name, 'Bus', bus_stop_count "
            f"FROM {reporting}.vw_q2_transport_connectivity",
        ),
        # Q4 — schools bubble: x=#govt schools, y=mean ICSEA, size=latest-year population,
        # colour=suburb (so hovering a bubble names the suburb), lga_name for the Region filter.
        # Population is joined from vw_q1's latest census year, leaving the reporting view untouched.
        # With the default 5-suburb selection (and any council slice) the bubble shows only a handful
        # of points, so AI/BI's own auto-padding keeps the big ones off the edges.
        dataset(
            "ds_q4",
            "Q4 top schools",
            "WITH latest_pop AS ("
            "  SELECT suburb_name, population_total,"
            "         ROW_NUMBER() OVER (PARTITION BY suburb_name ORDER BY year DESC) AS rn"
            f"  FROM {reporting}.vw_q1_population_growth) "
            "SELECT q.suburb_name, q.lga_name, q.govt_school_count, q.mean_icsea, "
            "       p.population_total "
            f"FROM {reporting}.vw_q4_top_schools q "
            "LEFT JOIN latest_pop p ON p.suburb_name = q.suburb_name AND p.rn = 1",
        ),
        # Q5 — value bubble: x=current price, y=annualised growth, size=population, colour=suburb.
        # Population (latest census year) is joined in here so the markers scale by suburb size,
        # making them far more visible than the old flat dots.
        dataset(
            "ds_q5",
            "Q5 affordable + growth",
            "WITH latest_pop AS ("
            "  SELECT suburb_name, population_total,"
            "         ROW_NUMBER() OVER (PARTITION BY suburb_name ORDER BY year DESC) AS rn"
            f"  FROM {reporting}.vw_q1_population_growth) "
            "SELECT v.suburb_name, v.lga_name, v.current_median, v.cagr_pct, "
            "       p.population_total "
            f"FROM {reporting}.vw_q5_affordable_growth v "
            "LEFT JOIN latest_pop p ON p.suburb_name = v.suburb_name AND p.rn = 1",
        ),
        # Best all-round value: each suburb scored 0-25 on four equally-weighted, min-max-normalised
        # latest-year factors (low crime, high ICSEA, strong transit, low price), emitted LONG (one
        # row per suburb x factor) so a stacked bar's segments SUM to the 0-100 total. lga_name is
        # carried and there is NO top-N cap: the leaderboard now reflects the Suburb/Region filter
        # selection like every other chart (pick a council to keep it legible at full scale).
        dataset(
            "ds_best",
            "Best all-round value",
            "WITH crime AS ("
            "  SELECT suburb_name, offence_count_total,"
            "         ROW_NUMBER() OVER (PARTITION BY suburb_name ORDER BY year DESC) AS rn"
            f"  FROM {reporting}.vw_q3_low_crime), "
            "price AS ("
            "  SELECT suburb_name, median_house_price,"
            "         ROW_NUMBER() OVER (PARTITION BY suburb_name ORDER BY year DESC) AS rn"
            f"  FROM {reporting}.vw_q6_most_expensive), "
            "m AS ("
            "  SELECT sch.suburb_name, sch.lga_name,"
            "         c.offence_count_total AS crime, sch.mean_icsea AS icsea,"
            "         t.connectivity_index AS transit, p.median_house_price AS price"
            f"  FROM {reporting}.vw_q4_top_schools sch"
            "  LEFT JOIN crime c ON c.suburb_name = sch.suburb_name AND c.rn = 1"
            f"  LEFT JOIN {reporting}.vw_q2_transport_connectivity t"
            "       ON t.suburb_name = sch.suburb_name"
            "  LEFT JOIN price p ON p.suburb_name = sch.suburb_name AND p.rn = 1), "
            "n AS ("
            "  SELECT suburb_name, lga_name,"
            "    ROUND(25*(1-(crime-MIN(crime) OVER())"
            "             /NULLIF(MAX(crime) OVER()-MIN(crime) OVER(),0)), 1) AS s_safety,"
            "    ROUND(25*((icsea-MIN(icsea) OVER())"
            "             /NULLIF(MAX(icsea) OVER()-MIN(icsea) OVER(),0)), 1) AS s_schools,"
            "    ROUND(25*((transit-MIN(transit) OVER())"
            "             /NULLIF(MAX(transit) OVER()-MIN(transit) OVER(),0)), 1) AS s_transit,"
            "    ROUND(25*(1-(price-MIN(price) OVER())"
            "             /NULLIF(MAX(price) OVER()-MIN(price) OVER(),0)), 1) AS s_afford"
            "  FROM m) "
            "SELECT suburb_name, lga_name, 'Safety (low crime)' AS factor, s_safety  AS score FROM n "
            "UNION ALL SELECT suburb_name, lga_name, 'Schools (ICSEA)',    s_schools FROM n "
            "UNION ALL SELECT suburb_name, lga_name, 'Transit',            s_transit FROM n "
            "UNION ALL SELECT suburb_name, lga_name, 'Affordability',      s_afford  FROM n",
        ),
    ]

    layout = [
        markdown(
            "hdr",
            "# \U0001f3d9\ufe0f Victoria Suburbs Profiler",
            {"x": 0, "y": 0, "width": 6, "height": 2},
        ),
        # Filter row: a note spanning the top, then the two selectors side by side.
        markdown(
            "flt_note",
            "**Suburb** and **Region / council** are linked \u2014 together they drive *every* "
            "chart. Pick a council to slice all charts to its suburbs (non-member suburbs grey "
            "out in the Suburb box); pick suburbs to focus further. Defaults to a few suburbs "
            "\u2014 clear them to see all of Victoria. Counters stay global.",
            {"x": 0, "y": 2, "width": 6, "height": 1},
        ),
        # Both filters target EVERY chart dataset (suburb_name + lga_name live on all of them), which
        # makes them associative: a council selection greys out non-member suburbs and slices every
        # chart; a suburb selection narrows the council list. No more disconnected trend-vs-comparison
        # behaviour. Counters stay global (unfiltered) as stable context.
        filter_multi(
            "flt_suburb",
            "Suburb",
            [
                ("ds_q1", "suburb_name"),
                ("ds_q3", "suburb_name"),
                ("ds_q6", "suburb_name"),
                ("ds_q2", "suburb_name"),
                ("ds_q4", "suburb_name"),
                ("ds_q5", "suburb_name"),
                ("ds_best", "suburb_name"),
            ],
            {"x": 0, "y": 3, "width": 3, "height": 1},
            dash_id,
            default_values=["Carlton", "Brunswick", "Box Hill", "Fitzroy", "Richmond"],
        ),
        filter_multi(
            "flt_region",
            "Region / council",
            [
                ("ds_q1", "lga_name"),
                ("ds_q3", "lga_name"),
                ("ds_q6", "lga_name"),
                ("ds_q2", "lga_name"),
                ("ds_q4", "lga_name"),
                ("ds_q5", "lga_name"),
                ("ds_best", "lga_name"),
            ],
            {"x": 3, "y": 3, "width": 3, "height": 1},
            dash_id,
        ),
        # KPI counters
        counter(
            "kpi_sub",
            "ds_kpi",
            "COUNT(DISTINCT `sal_code`)",
            "suburbs",
            "Suburbs profiled",
            {"x": 0, "y": 4, "width": 2, "height": 2},
        ),
        counter(
            "kpi_lga",
            "ds_kpi",
            "COUNT(DISTINCT `lga_code`)",
            "lgas",
            "LGAs",
            {"x": 2, "y": 4, "width": 2, "height": 2},
        ),
        counter(
            "kpi_yr",
            "ds_kpi",
            "COUNT(DISTINCT `year`)",
            "census_years",
            "Census years",
            {"x": 4, "y": 4, "width": 2, "height": 2},
        ),
        # Population trend (LINE; filtered by Suburb)
        line(
            "l_q1",
            "ds_q1",
            "year",
            "`year`",
            "Census year",
            "population",
            "SUM(`population_total`)",
            "Population",
            "suburb_name",
            "`suburb_name`",
            "Suburb",
            "Population growth over time (per suburb)",
            {"x": 0, "y": 6, "width": 6, "height": 8},
        ),
        # Crime trend (LINE; filtered by Suburb)
        line(
            "l_q3",
            "ds_q3",
            "year",
            "`year`",
            "Census year",
            "offences",
            "SUM(`offence_count_total`)",
            "Total offences",
            "suburb_name",
            "`suburb_name`",
            "Suburb",
            "Total crime over time (per suburb)",
            {"x": 0, "y": 14, "width": 6, "height": 8},
        ),
        # House-price trend (LINE; filtered by Suburb)
        line(
            "l_q6",
            "ds_q6",
            "year",
            "`year`",
            "Census year",
            "median_house_price",
            "SUM(`median_house_price`)",
            "Median house price ($)",
            "suburb_name",
            "`suburb_name`",
            "Suburb",
            "House prices over time (per suburb)",
            {"x": 0, "y": 22, "width": 6, "height": 8},
        ),
        # Public-transport connectivity (STACKED BAR by mode; soft matte palette)
        bar(
            "b_q2",
            "ds_q2",
            "suburb_name",
            "`suburb_name`",
            "Suburb",
            "stops",
            "SUM(`stops`)",
            "Transit stops",
            "Public-transport stops by mode",
            {"x": 0, "y": 30, "width": 6, "height": 8},
            color_field="mode",
            color_expr="`mode`",
            color_title="Mode",
            color_mappings=[
                {"value": "Train", "color": "#5B7C99"},
                {"value": "Tram", "color": "#7FA88B"},
                {"value": "Bus", "color": "#C9A66B"},
            ],
            colors=["#5B7C99", "#7FA88B", "#C9A66B"],
        ),
        # Public schooling (SCATTER: colour=suburb; NO size encoding). A size-driven bubble makes
        # AI/BI scale the largest marker to a fixed radius it won't let the file cap, so big bubbles
        # at the ICSEA min/max overflow the plot. Plain small dots can't overflow -> no truncation.
        point(
            "p_q4",
            "ds_q4",
            "govt_school_count",
            "`govt_school_count`",
            "Govt schools",
            "mean_icsea",
            "`mean_icsea`",
            "Mean ICSEA",
            "Public schooling \u2014 government schools vs ICSEA",
            {"x": 0, "y": 38, "width": 6, "height": 9},
            color_field="suburb_name",
            color_expr="`suburb_name`",
            color_title="Suburb",
            description="ICSEA \u2248 socio-educational advantage (1000 = national average; higher "
            "= a more advantaged student intake, a proxy for school-performance "
            "context). Each point is a suburb (colour = suburb; hover for the name); "
            "top-right = more schools and a higher mean ICSEA.",
        ),
        # Value for money (SCATTER: colour=suburb; NO size encoding, same reason as schooling —
        # a size-driven bubble overflows the plot and the file can't cap it). Plain dots: no truncation.
        point(
            "p_q5",
            "ds_q5",
            "current_median",
            "`current_median`",
            "Current median price ($)",
            "cagr_pct",
            "`cagr_pct`",
            "Annualised growth (CAGR %)",
            "Value for money: current price vs annualised growth",
            {"x": 0, "y": 47, "width": 6, "height": 8},
            color_field="suburb_name",
            color_expr="`suburb_name`",
            color_title="Suburb",
            description="Bottom-left = cheap today with strong annualised growth. Each point is a "
            "suburb (colour = suburb; hover any point for the name).",
        ),
        # Best all-round value (STACKED BAR; reflects the filter selection, 4 factors x 0-25 = total)
        bar(
            "b_best",
            "ds_best",
            "suburb_name",
            "`suburb_name`",
            "Suburb",
            "score",
            "SUM(`score`)",
            "All-round score (0-100)",
            "Best all-round value (selected suburbs)",
            {"x": 0, "y": 55, "width": 6, "height": 9},
            color_field="factor",
            color_expr="`factor`",
            color_title="Factor",
            color_mappings=[
                {"value": "Safety (low crime)", "color": "#7FA88B"},
                {"value": "Schools (ICSEA)", "color": "#6E8CA0"},
                {"value": "Transit", "color": "#9CC0C7"},
                {"value": "Affordability", "color": "#C9A66B"},
            ],
            colors=["#7FA88B", "#6E8CA0", "#9CC0C7", "#C9A66B"],
            description="Top 20 suburbs across all of Victoria. Each scores 0\u201325 on four "
            "equally-weighted, min\u2013max-normalised latest-year factors \u2014 low "
            "crime, school ICSEA, transit connectivity, affordability \u2014 stacked to "
            "a 0\u2013100 total, so the segments add up to the bar height.",
        ),
    ]

    page = {"name": _id("page_main"), "displayName": "Victoria Suburbs Profiler", "layout": layout}
    return {"datasets": datasets, "pages": [page]}


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Generate the AI/BI dashboard .lvdash.json")
    ap.add_argument("--out", default="resources/dashboards/vic_suburbs.lvdash.json")
    ap.add_argument(
        "--catalog",
        default=None,
        help="Bake a catalog into the queries (fallback for CLIs without "
        "dataset_catalog). Default: catalog-less, injected via ${var.catalog}.",
    )
    args = ap.parse_args()
    reporting, metadata = _schemas(args.catalog)
    spec = build(reporting, metadata)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    where = (
        f"catalog={args.catalog}" if args.catalog else "catalog-less (dataset_catalog injects it)"
    )
    print(
        f"dashboard: wrote {out} "
        f"({len(spec['datasets'])} datasets, {len(spec['pages'][0]['layout'])} widgets; {where})"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
