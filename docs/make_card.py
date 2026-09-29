"""Render docs/card.png, the catalog card for wiki-ref, from the plugin's real output.

Every string on the card comes from calling the plugin, against the live Wikipedia API:
``autocomplete()`` for the suggestion list and ``expand()`` for the attached block.

    PYTHONPATH=<hermes-agent checkout> python docs/make_card.py \
        --sans 'DMSans[opsz,wght].ttf' --mono 'JetBrainsMono[wght].ttf'

Fonts: DM Sans and JetBrains Mono (SIL Open Font License), the faces the Hermes docs site
uses. docs/card.png was rendered with the variable fonts from google/fonts at commit
23e54b51ddffbc7713c583748e3bd86f62b1fa4a:
  ofl/dmsans/DMSans[opsz,wght].ttf               sha256 8cd08d97e89c24d0aa92edd2f0f4c8ee6195eee9b7c9f154865a58b02f0c1c0d
  ofl/jetbrainsmono/JetBrainsMono[wght].ttf      sha256 48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda

The card is 1200x600 (2:1). The plugin page hero scales it to the page width and crops it
to 360px (object-fit: cover), which at the widest layout keeps source rows ~110-490; the
catalog grid and the Desktop catalog show the whole 2:1 image. The script refuses to write
a card whose ink leaves that band or its side margins.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 1200, 600
VISIBLE = (110, 490)  # hero rows visible at the widest layout (measured: 109.5-490.5); ink must stay inside
BAND = (132, 468)  # where the layout puts content: ~20px of air inside the visible rows
MARGIN_X = 64
LEFT_W = 500
GAP = 40

TITLE = "Tim_Berners-Lee"
TYPED = "Tim Bern"
PROMPT = "Summarize "

BG_TOP, BG_BOTTOM = (16, 19, 24), (22, 26, 33)
PANEL, PANEL_EDGE = (27, 31, 39), (46, 52, 62)
TEXT, MUTED, FAINT = (232, 234, 238), (160, 168, 180), (112, 120, 132)
ACCENT, LINK = (245, 185, 74), (128, 190, 255)
HIGHLIGHT = (38, 44, 56)


class Fonts:
    def __init__(self, sans: Path, mono: Path) -> None:
        self._sans, self._mono = str(sans), str(mono)

    def sans(self, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
        font = ImageFont.truetype(self._sans, size)
        font.set_variation_by_axes([min(max(size, 9), 40), weight])  # DM Sans axes: opsz, wght
        return font

    def mono(self, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
        font = ImageFont.truetype(self._mono, size)
        font.set_variation_by_axes([weight])
        return font


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "wiki_ref_card", ROOT / "wiki-ref" / "__init__.py", submodule_search_locations=[str(ROOT / "wiki-ref")]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def real_output(wiki) -> tuple[list[str], str]:
    provider = wiki.WikiReferenceProvider()
    suggestions = await provider.autocomplete(TYPED, limit=3)
    block = await provider.expand(TITLE)
    return [item.display for item in suggestions], block


def fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """``text``, shortened with an ellipsis until it is at most ``max_width`` pixels wide."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def background() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        shade = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM, strict=True))
        draw.line([(0, y), (WIDTH, y)], fill=shade)
    return image


def draw_card(fonts: Fonts, suggestions: list[str], block: str) -> Image.Image:
    ink = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    top = BAND[0]

    # Left: name, pitch, and the composer with @wiki: autocomplete open.
    title_font = fonts.sans(60, 700)
    draw.text((MARGIN_X, top - title_font.getbbox("wiki-ref")[1]), "wiki-ref", font=title_font, fill=TEXT)
    draw.text((MARGIN_X, top + 66), "Wikipedia lead sections as", font=fonts.sans(24), fill=MUTED)
    draw.text((MARGIN_X, top + 96), "@wiki: context references", font=fonts.sans(24), fill=MUTED)

    row_h = 34
    # The suggestion menu's bottom edge lines up with the right panel's.
    box_top = BAND[1] - (56 + row_h * len(suggestions) + 8)
    draw.rounded_rectangle(
        [MARGIN_X, box_top, MARGIN_X + LEFT_W, box_top + 48], radius=10, fill=PANEL, outline=PANEL_EDGE,
    )
    mono = fonts.mono(20)
    x, y = MARGIN_X + 18, box_top + 12
    draw.text((x, y), PROMPT, font=mono, fill=TEXT)
    x += draw.textlength(PROMPT, font=mono)
    typed = "@wiki:" + TYPED.replace(" ", "_")
    draw.text((x, y), typed, font=mono, fill=ACCENT)
    x += draw.textlength(typed, font=mono) + 2
    draw.line([(x, y + 1), (x, y + 24)], fill=TEXT, width=2)

    menu_top = box_top + 56
    draw.rounded_rectangle(
        [MARGIN_X, menu_top, MARGIN_X + LEFT_W, menu_top + row_h * len(suggestions) + 8],
        radius=10, fill=PANEL, outline=PANEL_EDGE,
    )
    meta, meta_font, item_font = "Wikipedia (en)", fonts.sans(15), fonts.sans(20)
    meta_w = draw.textlength(meta, font=meta_font)
    title_w = LEFT_W - 36 - meta_w - 16
    for i, title in enumerate(suggestions):
        row = menu_top + 4 + i * row_h
        if i == 0:
            draw.rounded_rectangle(
                [MARGIN_X + 4, row, MARGIN_X + LEFT_W - 4, row + row_h - 2], radius=8, fill=HIGHLIGHT,
            )
        draw.text((MARGIN_X + 18, row + 5), fit(draw, title, item_font, title_w), font=item_font, fill=TEXT)
        draw.text((MARGIN_X + LEFT_W - 18 - meta_w, row + 10), meta, font=meta_font, fill=FAINT)

    # Right: the block the model receives under --- Attached Context ---.
    px0, px1 = MARGIN_X + LEFT_W + GAP, WIDTH - MARGIN_X
    inner = px1 - px0 - 48
    draw.rounded_rectangle([px0, top, px1, BAND[1]], radius=14, fill=PANEL, outline=PANEL_EDGE)
    lines = block.splitlines()
    heading, url = lines[0], lines[1]
    body = next(line for line in lines[2:] if line.strip())
    source = lines[-1].split(". Quoted")[0] + "."
    title_part, _, description = heading.partition(" — ")
    x, y = px0 + 24, top + 22
    draw.text((x, y), "--- Attached Context ---", font=fonts.mono(16), fill=FAINT)
    y += 32
    draw.text((x, y), fit(draw, f"@wiki:{TITLE}", fonts.mono(18), inner), font=fonts.mono(18), fill=ACCENT)
    y += 34
    draw.text((x, y), fit(draw, title_part, fonts.sans(22, 700), inner), font=fonts.sans(22, 700), fill=TEXT)
    y += 30
    if description:
        draw.text((x, y), fit(draw, description, fonts.sans(18), inner), font=fonts.sans(18), fill=MUTED)
        y += 28
    draw.text((x, y), fit(draw, url, fonts.mono(15), inner), font=fonts.mono(15), fill=LINK)
    y += 36
    body_font = fonts.sans(17)
    wrapped = textwrap.wrap(body, width=60)
    shown = wrapped[:4]
    if len(wrapped) > len(shown):  # the lead goes on: say so instead of stopping mid-sentence
        shown[-1] = fit(draw, shown[-1] + " …", body_font, inner)
    for line in shown:
        draw.text((x, y), fit(draw, line, body_font, inner), font=body_font, fill=TEXT)
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
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "card.png")
    args = parser.parse_args()

    suggestions, block = asyncio.run(real_output(load_plugin()))
    card = draw_card(Fonts(args.sans, args.mono), suggestions, block)
    card.save(args.out, format="PNG", optimize=True)
    print(f"wrote {args.out} ({card.size[0]}x{card.size[1]}); suggestions={suggestions}")


if __name__ == "__main__":
    main()
