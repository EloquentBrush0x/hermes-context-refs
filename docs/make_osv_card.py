"""Render docs/osv-ref-card.png, the catalog card for osv-ref, from the plugin's real output.

Every reference on the card is expanded live against the OSV API by this script, and the
attached-context panel shows the lines ``expand()`` returned for the one in the composer.

    PYTHONPATH=<hermes-agent checkout> python docs/make_osv_card.py \
        --sans 'DMSans[opsz,wght].ttf' --mono 'JetBrainsMono[wght].ttf'

Fonts, size and the crop-safe band are the same as wiki-ref's card (see docs/make_card.py, whose
helpers this script reuses): 1200x600, all ink inside rows 132-468.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

from make_card import (
    ACCENT,
    BAND,
    FAINT,
    GAP,
    HEIGHT,
    LEFT_W,
    LINK,
    MARGIN_X,
    MUTED,
    PANEL,
    PANEL_EDGE,
    TEXT,
    VISIBLE,
    WIDTH,
    Fonts,
    background,
    fit,
)
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PROMPT, REFERENCE = "Am I hit by ", "@cve:CVE-2024-3651"
EXAMPLES = ("@cve:CVE-2024-3651", "@ghsa:GHSA-rrjw-j4m2-mf34", "@osv:PYSEC-2024-60")


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "osv_ref_card", ROOT / "osv-ref" / "__init__.py", submodule_search_locations=[str(ROOT / "osv-ref")]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def real_output(osv) -> dict[str, str]:
    """``{reference: expanded block}`` for every reference drawn on the card."""
    providers = {p.prefix: p() for p in osv.PROVIDERS}
    blocks = {}
    for reference in dict.fromkeys((REFERENCE, *EXAMPLES)):
        prefix, _, target = reference[1:].partition(":")
        blocks[reference] = await providers[prefix].expand(target)
    return blocks


def draw_card(fonts: Fonts, block: str) -> Image.Image:
    ink = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    top = BAND[0]

    # Left: name, pitch, the three reference kinds, and the composer.
    title_font = fonts.sans(60, 700)
    draw.text((MARGIN_X, top - title_font.getbbox("osv-ref")[1]), "osv-ref", font=title_font, fill=TEXT)
    draw.text((MARGIN_X, top + 66), "Vulnerability records from OSV.dev", font=fonts.sans(24), fill=MUTED)
    draw.text((MARGIN_X, top + 96), "as @-context references", font=fonts.sans(24), fill=MUTED)

    # The three reference kinds as plain text: osv-ref has no autocomplete, so nothing here may
    # look like a suggestion menu.
    mono = fonts.mono(18)
    for i, example in enumerate(EXAMPLES):
        prefix, _, rest = example.partition(":")
        x, y = MARGIN_X, top + 150 + i * 30
        draw.text((x, y), prefix + ":", font=mono, fill=ACCENT)
        draw.text((x + draw.textlength(prefix + ":", font=mono), y), rest, font=mono, fill=TEXT)

    box_top = BAND[1] - 48
    draw.rounded_rectangle(
        [MARGIN_X, box_top, MARGIN_X + LEFT_W, box_top + 48], radius=10, fill=PANEL, outline=PANEL_EDGE,
    )
    mono = fonts.mono(20)
    x, y = MARGIN_X + 18, box_top + 12
    draw.text((x, y), PROMPT, font=mono, fill=TEXT)
    x += draw.textlength(PROMPT, font=mono)
    draw.text((x, y), REFERENCE + "?", font=mono, fill=ACCENT)
    x += draw.textlength(REFERENCE + "?", font=mono) + 2
    draw.line([(x, y + 1), (x, y + 24)], fill=TEXT, width=2)
    if x > MARGIN_X + LEFT_W - 12:
        raise SystemExit(f"composer text runs past its box (x={x})")

    # Right: the block the model receives under --- Attached Context ---.
    px0, px1 = MARGIN_X + LEFT_W + GAP, WIDTH - MARGIN_X
    inner = px1 - px0 - 48
    draw.rounded_rectangle([px0, top, px1, BAND[1]], radius=14, fill=PANEL, outline=PANEL_EDGE)
    lines = block.splitlines()
    heading, url = lines[0], lines[1]
    identifier, _, summary = heading.partition(" — ")
    start = lines.index("Affected packages:")
    packages = [line for line in lines[start + 1:start + 3] if line.startswith("- ")]
    label = next(line for line in lines if line.startswith("Severity label:"))
    source = lines[-1].split(" Advisory text")[0]
    x, y = px0 + 24, top + 22
    draw.text((x, y), "--- Attached Context ---", font=fonts.mono(16), fill=FAINT)
    y += 32
    draw.text((x, y), REFERENCE, font=fonts.mono(18), fill=ACCENT)
    y += 34
    draw.text((x, y), fit(draw, identifier, fonts.sans(22, 700), inner), font=fonts.sans(22, 700), fill=TEXT)
    y += 30
    draw.text((x, y), fit(draw, summary, fonts.sans(18), inner), font=fonts.sans(18), fill=MUTED)
    y += 28
    draw.text((x, y), fit(draw, url, fonts.mono(15), inner), font=fonts.mono(15), fill=LINK)
    y += 36
    draw.text((x, y), "Affected packages:", font=fonts.sans(17, 700), fill=TEXT)
    y += 26
    for line in packages:
        draw.text((x, y), fit(draw, line, fonts.sans(17), inner), font=fonts.sans(17), fill=TEXT)
        y += 25
    y += 7
    draw.text((x, y), fit(draw, label, fonts.sans(17), inner), font=fonts.sans(17), fill=MUTED)
    y += 25
    draw.text((x, BAND[1] - 38), fit(draw, source, fonts.mono(14), inner), font=fonts.mono(14), fill=FAINT)
    if y > BAND[1] - 46:
        raise SystemExit(f"attached-context text runs into the source line (y={y})")

    left, top_ink, right, bottom = ink.getchannel("A").getbbox()  # right/bottom are exclusive
    last_row, last_col = bottom - 1, right - 1
    if top_ink < BAND[0] or last_row > BAND[1] or left < MARGIN_X or last_col > WIDTH - MARGIN_X:
        raise SystemExit(f"ink x {left}-{last_col}, y {top_ink}-{last_row} leaves the layout box {BAND}")
    assert VISIBLE[0] <= top_ink and last_row <= VISIBLE[1]
    print(f"ink x {left}-{last_col}, y {top_ink}-{last_row}; "
          f"{top_ink - VISIBLE[0]}px / {VISIBLE[1] - last_row}px clear of the hero crop rows {VISIBLE}")
    card = background()
    card.paste(ink, (0, 0), ink)
    return card


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sans", type=Path, required=True, help="DM Sans variable font (DMSans[opsz,wght].ttf)")
    parser.add_argument(
        "--mono", type=Path, required=True, help="JetBrains Mono variable font (JetBrainsMono[wght].ttf)",
    )
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "osv-ref-card.png")
    args = parser.parse_args()

    blocks = asyncio.run(real_output(load_plugin()))
    card = draw_card(Fonts(args.sans, args.mono), blocks[REFERENCE])
    card.save(args.out, format="PNG", optimize=True)
    print(f"wrote {args.out} ({card.size[0]}x{card.size[1]}); expanded {list(blocks)}")


if __name__ == "__main__":
    main()
