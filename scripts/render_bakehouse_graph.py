"""Render the imported Bakehouse graph, with explicit proposal status.

Run: uv run --with pillow python scripts/render_bakehouse_graph.py
"""

import argparse
import math
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lakecontext.store import Store


def render(database: str, output: Path):
    graph = Store(database).read()
    tables = {t["name"].split(".")[-1]: t for t in graph["tables"]}
    positions = {
        "media_customer_reviews": (96, 310),
        "sales_transactions": (760, 310),
        "sales_customers": (1424, 310),
        "media_gold_reviews_chunked": (96, 780),
        "sales_franchises": (760, 780),
        "sales_suppliers": (1424, 780),
    }
    if set(tables) != set(positions) or any(
        not t["name"].startswith("samples.bakehouse.") for t in graph["tables"]
    ):
        raise ValueError("This layout requires the six samples.bakehouse tables")

    canvas = Image.new("RGB", (2040, 1300), "#f4f6fa")
    draw = ImageDraw.Draw(canvas)
    navy, muted, orange, border = "#18283f", "#627287", "#bb7425", "#dbe2eb"

    def font(size, bold=False):
        path = "/usr/share/fonts/noto/NotoSans-" + ("Bold" if bold else "Regular") + ".ttf"
        return ImageFont.truetype(path, size)

    def text(x, y, value, size=24, color=navy, bold=False, anchor=None):
        draw.text((x, y), value, font=font(size, bold), fill=color, anchor=anchor)

    def dashed(points, color=orange, width=4):
        for start, end in pairwise(points):
            distance = math.dist(start, end)
            for offset in range(0, int(distance), 21):
                a, b = offset / distance, min(offset + 12, distance) / distance
                draw.line(
                    [tuple(start[i] + (end[i] - start[i]) * t for i in (0, 1)) for t in (a, b)],
                    fill=color,
                    width=width,
                )

    def label(center, value):
        box = draw.textbbox((0, 0), value, font=font(20))
        width = box[2] - box[0]
        x, y = center
        draw.rounded_rectangle(
            (x - width / 2 - 10, y - 16, x + width / 2 + 10, y + 20), radius=9, fill="#f4f6fa"
        )
        text(x, y, value, size=20, color=orange, anchor="mm")

    text(96, 62, "LAKECONTEXT  /  IMPORTED METADATA", size=20, color=muted, bold=True)
    text(96, 102, "Bakehouse relationship graph", size=54, bold=True)
    text(98, 180, "samples.bakehouse", size=26, color=muted)
    column_count = sum(len(t["columns"]) for t in tables.values())
    proposed = sum(e["status"] == "proposed" for e in graph["relationships"])
    approved = sum(e["status"] == "approved" for e in graph["relationships"])
    text(
        1944,
        126,
        f"{len(tables)} tables  /  {column_count} columns",
        size=25,
        anchor="ra",
        bold=True,
    )
    text(
        1944,
        168,
        f"{proposed} proposals  /  {approved} approved",
        size=25,
        color=muted,
        anchor="ra",
    )
    dashed([(98, 251), (153, 251)])
    text(
        168, 233, "Proposed relationship · matching identifier name and type", size=23, color=muted
    )

    # Routes are layout only. Edges, labels and status come from the imported graph.
    routes = {
        frozenset(("media_customer_reviews", "sales_transactions")): (
            [(616, 445), (760, 445)],
            (688, 410),
        ),
        frozenset(("sales_transactions", "sales_customers")): (
            [(1280, 445), (1424, 445)],
            (1352, 410),
        ),
        frozenset(("media_customer_reviews", "sales_franchises")): (
            [(516, 560), (760, 840)],
            (618, 689),
        ),
        frozenset(("sales_transactions", "sales_franchises")): (
            [(1020, 560), (1020, 780)],
            (1100, 670),
        ),
        frozenset(("sales_franchises", "sales_suppliers")): (
            [(1280, 915), (1424, 915)],
            (1352, 880),
        ),
    }
    visible_fields = {name: set() for name in tables}
    for edge in graph["relationships"]:
        left, right = edge["left"], edge["right"]
        names = [side["table"].split(".")[-1] for side in (left, right)]
        key = frozenset(names)
        if key not in routes or edge["status"] != "proposed":
            raise ValueError("Graph changed; update the layout/status styling before rendering")
        points, label_position = routes[key]
        dashed(points)
        label(
            label_position,
            left["column"]
            if left["column"] == right["column"]
            else f"{left['column']} = {right['column']}",
        )
        for name, side in zip(names, (left, right), strict=True):
            visible_fields[name].add(side["column"])

    # Explain the isolated node using the actual differing type, without adding an edge.
    visible_fields["media_gold_reviews_chunked"].add("franchiseID")
    for name, (x, y) in positions.items():
        table = tables[name]
        draw.rounded_rectangle((x, y + 7, x + 520, y + 257), radius=18, fill="#e8edf4")
        draw.rounded_rectangle(
            (x, y, x + 520, y + 250), radius=18, fill="white", outline=border, width=2
        )
        draw.rounded_rectangle((x + 25, y + 25, x + 31, y + 78), radius=3, fill="#53869f")
        text(x + 48, y + 24, name, size=25, bold=True)
        text(x + 48, y + 65, f"TABLE  /  {len(table['columns'])} columns", size=19, color=muted)
        draw.line((x + 26, y + 111, x + 494, y + 111), fill=border, width=2)
        row = y + 129
        for column in table["columns"]:
            if column["name"] in visible_fields[name]:
                text(x + 29, row, column["name"], size=25)
                text(x + 490, row, column["data_type"], size=22, color=muted, anchor="ra")
                row += 45
        if name == "media_gold_reviews_chunked":
            text(x + 29, y + 183, "No inferred edge: int differs from bigint", size=21, color=muted)

    draw.line((96, 1110, 1944, 1110), fill=border, width=2)
    text(96, 1140, "Relationships are suggestions, not verified foreign keys.", size=26, bold=True)
    text(
        96,
        1183,
        "No cardinality or direction inferred. Cards show relationship identifiers; other columns are omitted.",
        size=23,
        color=muted,
    )
    text(
        96,
        1226,
        "Metadata captured "
        + graph["captured_at"][:19].replace("T", " ")
        + " UTC  ·  No table rows queried",
        size=20,
        color=muted,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    print(output.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".lakecontext/bakehouse.db")
    parser.add_argument("--output", type=Path, default=Path(".lakecontext/bakehouse-graph.png"))
    args = parser.parse_args()
    render(args.db, args.output)
