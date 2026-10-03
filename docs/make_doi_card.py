"""Render docs/doi-ref-card.png, the catalog card for doi-ref, from the plugin's real output.

The attached-context panel shows the lines ``expand()`` returned for the reference in the
composer, read live by this script (Crossref answers "not found", DataCite has the record).

    PYTHONPATH=<hermes-agent checkout> python docs/make_doi_card.py \
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
from make_gh_card import fit_parts, wrap
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PROMPT, TARGET = "Explain ", "10.48550/arXiv.1706.03762"
REFERENCE = f"@doi:{TARGET}"
FORMS = ("@doi:10.1038/nature14539", "@doi:https://doi.org/10.5281/…")


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "doi_ref_card", ROOT / "doi-ref" / "__init__.py", submodule_search_locations=[str(ROOT / "doi-ref")]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def real_output(doi) -> str:
    return await doi.DoiReferenceProvider().expand(TARGET)


def draw_card(fonts: Fonts, block: str) -> Image.Image:
    ink = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    top = BAND[0]

    # Left: name, pitch, two ways to write a reference, and the composer.
    title_font = fonts.sans(60, 700)
    draw.text((MARGIN_X, top - title_font.getbbox("doi-ref")[1]), "doi-ref", font=title_font, fill=TEXT)
    draw.text((MARGIN_X, top + 66), "Papers, datasets and software", font=fonts.sans(24), fill=MUTED)
    draw.text((MARGIN_X, top + 96), "as @doi: context references", font=fonts.sans(24), fill=MUTED)
    mono = fonts.mono(17)
    for i, form in enumerate(FORMS):
        prefix, _, rest = form.partition(":")
        x, y = MARGIN_X, top + 150 + i * 30
        draw.text((x, y), prefix + ":", font=mono, fill=ACCENT)
        draw.text((x + draw.textlength(prefix + ":", font=mono), y), fit(draw, rest, mono, LEFT_W - 60), font=mono,
                  fill=TEXT)

    box_top = BAND[1] - 48
    draw.rounded_rectangle(
        [MARGIN_X, box_top, MARGIN_X + LEFT_W, box_top + 48], radius=10, fill=PANEL, outline=PANEL_EDGE,
    )
    mono = fonts.mono(16)
    x, y = MARGIN_X + 18, box_top + 14
    draw.text((x, y), PROMPT, font=mono, fill=TEXT)
    x += draw.textlength(PROMPT, font=mono)
    draw.text((x, y), REFERENCE, font=mono, fill=ACCENT)
    x += draw.textlength(REFERENCE, font=mono) + 2
    draw.line([(x, y + 1), (x, y + 20)], fill=TEXT, width=2)
    if x > MARGIN_X + LEFT_W - 12:
        raise SystemExit(f"composer text runs past its box (x={x})")

    # Right: the block the model receives under --- Attached Context ---.
    px0, px1 = MARGIN_X + LEFT_W + GAP, WIDTH - MARGIN_X
    inner = px1 - px0 - 48
    draw.rounded_rectangle([px0, top, px1, BAND[1]], radius=14, fill=PANEL, outline=PANEL_EDGE)
    lines = block.splitlines()
    heading, url, facts = lines[0], lines[1], lines[2]
    number, _, title = heading.partition(": ")
    creators = next(line for line in lines if line.startswith("Creators: "))
    abstract = lines[lines.index("Abstract:") + 1]
    source = lines[-1].split(" Quoted reference")[0]
    x, y = px0 + 24, top + 22
    draw.text((x, y), "--- Attached Context ---", font=fonts.mono(16), fill=FAINT)
    y += 30
    draw.text((x, y), fit(draw, number, fonts.sans(20, 700), inner), font=fonts.sans(20, 700), fill=TEXT)
    y += 30
    draw.text((x, y), fit(draw, title, fonts.sans(17), inner), font=fonts.sans(17), fill=MUTED)
    y += 26
    draw.text((x, y), fit(draw, url, fonts.mono(14), inner), font=fonts.mono(14), fill=LINK)
    y += 30
    draw.text((x, y), fit_parts(draw, facts, fonts.sans(16), inner), font=fonts.sans(16), fill=TEXT)
    y += 24
    creators_font = fonts.sans(15, 700)
    if draw.textlength(creators, font=creators_font) > inner:
        creators = fit_parts(draw, creators, creators_font, inner, "; ")
    draw.text((x, y), creators, font=creators_font, fill=ACCENT)
    y += 32
    draw.text((x, y), "Abstract:", font=fonts.sans(16, 700), fill=TEXT)
    y += 26
    for line in wrap(draw, abstract, fonts.sans(16), inner, 3):
        draw.text((x, y), line, font=fonts.sans(16), fill=TEXT)
        y += 22
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
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "doi-ref-card.png")
    args = parser.parse_args()

    block = asyncio.run(real_output(load_plugin()))
    card = draw_card(Fonts(args.sans, args.mono), block)
    card.save(args.out, format="PNG", optimize=True)
    print(f"wrote {args.out} ({card.size[0]}x{card.size[1]}) from {REFERENCE}")


if __name__ == "__main__":
    main()
