#!/usr/bin/env python3
"""Generate a clean fact-constellation ER diagram as SVG (orthogonal data-bus routing).

Run from anywhere:  python3 tools/build-er-diagram.py   (writes docs/data-model/er-fact-constellation.svg)
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "data-model" / "er-fact-constellation.svg"

W, H = 1660, 1000
NAVY = "#1A2B4A"
BLUE = "#3D6BBA"
TEAL = "#1FA39A"
INK = "#33415C"
MUTE = "#6B7B96"
LINE = "#C7D2E4"
BG = "#FFFFFF"
FONT = "Inter, 'Segoe UI', system-ui, sans-serif"


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


facts = [  # name, y, qmap
    ("fact_suburb_demographics", 150, "Q1 · population & growth"),
    ("fact_suburb_property", 266, "Q5 · Q6 · price & rent"),
    ("fact_suburb_crime", 382, "Q3 · low crime"),
    ("fact_suburb_transport", 498, "Q2 · connectivity"),
    ("fact_suburb_education", 614, "Q4 · schooling"),
]
FX, FW, FH = 70, 300, 88

# buses: key -> (x, colour, branch_y to its dimension)
buses = {
    "suburb_sk": (430, BLUE, 285),
    "year_sk": (505, TEAL, 490),
}
DIMX, DIMW = 1200, 270

s = []
s.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="{FONT}">')
s.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
# defs: soft shadow
s.append(
    '<defs><filter id="sh" x="-20%" y="-20%" width="140%" height="140%">'
    '<feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#1A2B4A" flood-opacity="0.16"/>'
    "</filter></defs>"
)

# title
s.append(
    f'<text x="{W/2}" y="58" text-anchor="middle" font-size="30" font-weight="800" '
    f'fill="{NAVY}" letter-spacing="0.5">Data Model — Fact Constellation</text>'
)
s.append(
    f'<text x="{W/2}" y="88" text-anchor="middle" font-size="15" fill="{MUTE}">'
    f"One fact per subject area · dimensions shared across all facts</text>"
)


def box(x, y, w, h, title, header_fill, rows, header_text="#FFFFFF", rx=12):
    g = ['<g filter="url(#sh)">']
    g.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="white" '
        f'stroke="{LINE}" stroke-width="1.5"/>'
    )
    g.append(
        f'<path d="M{x+1} {y+30} v-18 a12 12 0 0 1 12 -12 h{w-26} a12 12 0 0 1 12 12 v18 Z" '
        f'fill="{header_fill}"/>'
    )
    g.append(
        f'<text x="{x+16} " y="{y+20}" font-size="15" font-weight="700" fill="{header_text}">{esc(title)}</text>'
    )
    yy = y + 50
    for r, col in rows:
        g.append(f'<text x="{x+16}" y="{yy}" font-size="12.5" fill="{col}">{esc(r)}</text>')
        yy += 19
    g.append("</g>")
    return "\n".join(g)


# ---- dimensions (right) ----
s.append(
    box(
        DIMX,
        70,
        DIMW,
        96,
        "dim_lga  ·  SCD2",
        BLUE,
        [("lga_sk (PK) · lga_code", INK), ("lga_name · lga_type · is_current", MUTE)],
    )
)
s.append(
    box(
        DIMX,
        210,
        DIMW,
        150,
        "dim_suburb  ·  SCD2",
        BLUE,
        [
            ("suburb_sk (PK) · sal_code (BK)", INK),
            ("suburb_name · postcode", MUTE),
            ("lga_sk (FK) · region", MUTE),
            ("asgs_edition · is_current", MUTE),
        ],
    )
)
s.append(
    box(
        DIMX,
        430,
        DIMW,
        120,
        "dim_year  ·  Type 1",
        TEAL,
        [("year_sk (PK) · year", INK), ("decade · is_census_year", MUTE)],
    )
)

# lga_sk link (dim_suburb -> dim_lga, vertical)
lx = DIMX + DIMW - 60
s.append(f'<path d="M{lx} 210 V 166" fill="none" stroke="{BLUE}" stroke-width="2"/>')
s.append(f'<circle cx="{lx}" cy="210" r="3.5" fill="{BLUE}"/>')
s.append(f'<text x="{lx+8}" y="190" font-size="11.5" fill="{BLUE}">lga_sk</text>')

# ---- facts (left) ----
for name, fy, qmap in facts:
    s.append(
        box(
            FX,
            fy,
            FW,
            FH,
            name,
            NAVY,
            [("FK: suburb_sk · year_sk", INK), ("\u2192 " + qmap, MUTE)],
        )
    )
FRX = FX + FW  # fact right edge = 370
tap_dy = {"suburb_sk": 46, "year_sk": 60}

# ---- buses (vertical) + branch to dimension ----
all_fact_rows = list(facts)
for key, (bx, col, branch_y) in buses.items():
    taps = []
    for row in all_fact_rows:
        fname, fy = row[0], row[1]
        taps.append(fy + tap_dy[key])
    ys = taps + [branch_y]
    y0, y1 = min(ys), max(ys)
    # vertical bus
    s.append(f'<path d="M{bx} {y0} V {y1}" fill="none" stroke="{col}" stroke-width="2.5"/>')
    # branch to dimension (horizontal)
    s.append(f'<path d="M{bx} {branch_y} H {DIMX}" fill="none" stroke="{col}" stroke-width="2.5"/>')
    s.append(
        f'<polygon points="{DIMX-9},{branch_y-5} {DIMX},{branch_y} {DIMX-9},{branch_y+5}" fill="{col}"/>'
    )
    # key label at top of bus
    s.append(
        f'<text x="{bx}" y="{y0-8}" text-anchor="middle" font-size="11.5" '
        f'font-weight="700" fill="{col}">{key}</text>'
    )
    # taps from facts
    for row in all_fact_rows:
        fname, fy = row[0], row[1]
        ty = fy + tap_dy[key]
        s.append(f'<path d="M{FRX} {ty} H {bx}" fill="none" stroke="{col}" stroke-width="2"/>')
        s.append(f'<circle cx="{bx}" cy="{ty}" r="3.6" fill="{col}"/>')
        s.append(f'<circle cx="{FRX}" cy="{ty}" r="2.6" fill="{col}"/>')

# ---- legend ----
ly = 880
items = [
    ("SCD2 dimension", BLUE),
    ("Type 1 dimension", TEAL),
    ("Fact (grain: suburb × year)", NAVY),
]
s.append(f'<text x="70" y="{ly-14}" font-size="13" font-weight="700" fill="{NAVY}">Legend</text>')
x = 70
for label, col in items:
    s.append(f'<rect x="{x}" y="{ly}" width="22" height="14" rx="3" fill="{col}"/>')
    s.append(f'<text x="{x+30}" y="{ly+12}" font-size="12.5" fill="{INK}">{label}</text>')
    x += 40 + len(label) * 7.6
# bus key legend
s.append(
    f'<text x="70" y="{ly+44}" font-size="12.5" fill="{MUTE}">'
    f"\u25cf junction = foreign-key join into the shared dimension\u2003·\u2003"
    f"lines crossing without a dot are not connected (data-bus convention)</text>"
)

s.append("</svg>")
OUT.write_text("\n".join(s) + "\n")
print(f"wrote {OUT.relative_to(REPO_ROOT)}")
