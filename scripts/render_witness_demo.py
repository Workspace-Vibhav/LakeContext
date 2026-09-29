"""Export the Bakehouse witness as a PNG, JSON, and standalone SQLite replay.

uv run --with pillow python scripts/render_witness_demo.py
"""

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lakecontext.service import ContextService
from lakecontext.store import Store

ROOT = Path(__file__).resolve().parents[1]


def main():
    service = ContextService(Store(ROOT / ".lakecontext/bakehouse.db"))
    report = service.audit_sql((ROOT / "examples/bakehouse-risk.sql").read_text())["audit"]
    failure = report["counterexamples"][0]
    if failure["failure"] != "fanout" or len(failure["tables"]) != 2:
        raise ValueError("The demo layout expects a two-table fanout witness")
    output = ROOT / ".lakecontext"
    (output / "join-witness.json").write_text(json.dumps(report, indent=2))
    (output / "join-witness-replay.sql").write_text(
        "-- Entirely synthetic data. Run in an empty SQLite database.\n"
        + failure["fixture_sql"]
        + "\n-- Expected base sum and row count:\n"
        + failure["base_sql"]
        + "\n-- Joined sum and row count:\n"
        + failure["query_sql"]
        + "\n"
    )
    canvas = Image.new("RGB", (2160, 1550), "#f4f6fa")
    draw = ImageDraw.Draw(canvas)
    navy, muted, line, green, amber = "#18283f", "#627287", "#dbe2eb", "#286b56", "#ad551c"

    def font(size, bold=False):
        return ImageFont.truetype(
            "/usr/share/fonts/noto/NotoSans-" + ("Bold" if bold else "Regular") + ".ttf", size
        )

    def text(x, y, value, size=25, color=navy, bold=False):
        draw.text((x, y), str(value), font=font(size, bold), fill=color)

    text(90, 55, "LAKECONTEXT  /  EXPERIMENTAL JOIN WITNESS", 21, muted, True)
    text(90, 98, "Same schema. Different answer.", 60, bold=True)
    text(
        90,
        188,
        "A synthetic counterexample from the imported Bakehouse schema. No warehouse rows read.",
        29,
        muted,
    )
    for x, title, world, color in (
        (90, "ONE MATCH", report["safe_example"], green),
        (1130, "TWO MATCHES", failure, amber),
    ):
        draw.rounded_rectangle((x, 290, x + 940, 1060), 20, fill="white", outline=line, width=2)
        text(x + 35, 320, title, 24, color, True)
        text(x + 35, 359, "Synthetic database", 22, muted)
        y = 420
        for table in world["tables"]:
            text(x + 35, y, table["source_table"].split(".")[-1], 28, bold=True)
            fields = list(table["rows"][0])
            field_width = 840 / len(fields)
            draw.rectangle((x + 35, y + 52, x + 875, y + 95), fill="#edf1f7")
            for i, field in enumerate(fields):
                text(x + 48 + i * field_width, y + 57, field, 21, muted)
            row_y = y + 109
            for row in table["rows"]:
                for i, field in enumerate(fields):
                    text(x + 48 + i * field_width, row_y, row[field], 27)
                row_y += 47
            y += 230
        draw.line((x + 35, 898, x + 875, 898), fill=line, width=2)
        text(x + 35, 922, "SUM(totalPrice) after the LEFT JOIN", 24, muted)
        text(x + 35, 956, world["joined_sum"], 58, color, True)
        text(
            x + 290,
            981,
            f"Base total: {world['base_sum']}  /  Joined rows: {world['joined_rows']}",
            25,
            muted,
        )

    text(90, 1112, "THE MISSING GUARANTEE", 22, amber, True)
    question = report["questions"][0]
    for i, part in enumerate(textwrap.wrap(question, width=108)):
        text(90, 1155 + i * 43, part, 31, bold=True)
    text(
        90,
        1285,
        "The tool returns the question, the three-row counterexample, and SQL that reproduces 100 becoming 200.",
        27,
        muted,
    )
    text(
        90,
        1342,
        "An approved graph edge alone does not establish uniqueness. Claims stay conditional and expire on context changes.",
        26,
        muted,
    )
    draw.line((90, 1425, 2070, 1425), fill=line, width=2)
    text(
        90,
        1452,
        "Demonstrates a possible failure, not a measured error in your data. Counterexample methods have prior art; novelty is unproven.",
        23,
        muted,
    )
    path = output / "join-witness.png"
    canvas.save(path)
    print(path)


if __name__ == "__main__":
    main()
