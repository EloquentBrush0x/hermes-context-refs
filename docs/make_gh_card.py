"""Render docs/gh-ref-card.png, the catalog card for gh-ref, from the plugin's real output.

The attached-context panel shows the lines ``expand()`` returned for the reference in the
composer, read live from GitHub's API by this script (two anonymous requests).

    PYTHONPATH=<hermes-agent checkout> python docs/make_gh_card.py \
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
PROMPT, TARGET = "Fix ", "NousResearch/hermes-agent#26193"
REFERENCE = f"@gh:{TARGET}"
FORMS = ("@gh:owner/repo#123", "@gh:https://github.com/owner/repo/pull/123")


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "gh_ref_card", ROOT / "gh-ref" / "__init__.py", submodule_search_locations=[str(ROOT / "gh-ref")]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def real_output(gh) -> str:
    settings = {"max_comments": 3}
    provider = gh.GitHubReferenceProvider(get_config=lambda key, default=None: settings.get(key, default))
    return await provider.expand(TARGET)


def fit_parts(draw: ImageDraw.ImageDraw, text: str, font, max_width: float, sep: str = " · ") -> str:
    """``text`` cut at whole ``sep``-separated parts (then "…") so it is at most ``max_width`` wide."""
    parts = text.split(sep)
    while len(parts) > 1 and draw.textlength(sep.join(parts) + sep + "…", font=font) > max_width:
        parts.pop()
    joined = sep.join(parts)
    return joined if joined == text else fit(draw, joined + sep + "…", font, max_width)


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: float, max_lines: int) -> list[str]:
    """Greedy word wrap by pixel width; the last line is ellipsized when text remains."""
    lines, words = [], text.split()
    while words and len(lines) < max_lines:
        line = words.pop(0)
        while words and draw.textlength(f"{line} {words[0]}", font=font) <= max_width:
            line += " " + words.pop(0)
        lines.append(line)
    if words:
        lines[-1] = fit(draw, lines[-1] + " " + " ".join(words), font, max_width)
    return [fit(draw, line, font, max_width) for line in lines]


def draw_card(fonts: Fonts, block: str) -> Image.Image:
    ink = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    top = BAND[0]

    # Left: name, pitch, the two ways to write a reference, and the composer.
    title_font = fonts.sans(60, 700)
    draw.text((MARGIN_X, top - title_font.getbbox("gh-ref")[1]), "gh-ref", font=title_font, fill=TEXT)
    draw.text((MARGIN_X, top + 66), "GitHub issues and pull requests", font=fonts.sans(24), fill=MUTED)
    draw.text((MARGIN_X, top + 96), "as @gh: context references", font=fonts.sans(24), fill=MUTED)
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
    mono = fonts.mono(18)
    x, y = MARGIN_X + 18, box_top + 13
    draw.text((x, y), PROMPT, font=mono, fill=TEXT)
    x += draw.textlength(PROMPT, font=mono)
    draw.text((x, y), REFERENCE, font=mono, fill=ACCENT)
    x += draw.textlength(REFERENCE, font=mono) + 2
    draw.line([(x, y + 1), (x, y + 22)], fill=TEXT, width=2)
    if x > MARGIN_X + LEFT_W - 12:
        raise SystemExit(f"composer text runs past its box (x={x})")

    # Right: the block the model receives under --- Attached Context ---.
    px0, px1 = MARGIN_X + LEFT_W + GAP, WIDTH - MARGIN_X
    inner = px1 - px0 - 48
    draw.rounded_rectangle([px0, top, px1, BAND[1]], radius=14, fill=PANEL, outline=PANEL_EDGE)
    lines = block.splitlines()
    heading, url, facts = lines[0], lines[1], lines[2]
    labels = next(line for line in lines if line.startswith("Labels: "))
    which, _, title = heading.partition(": ")
    comments = next(line for line in lines if line.startswith("Comments ("))
    last_author = [line for line in lines if line.startswith("--- @")][-1]
    last_text = lines[lines.index(last_author) + 1]
    source = lines[-1].split(" Issue and comment text")[0]
    x, y = px0 + 24, top + 22
    draw.text((x, y), "--- Attached Context ---", font=fonts.mono(16), fill=FAINT)
    y += 30
    kind, _, ref = which.rpartition(" ")  # "GitHub issue", "NousResearch/hermes-agent#26193"
    draw.text((x, y + 3), kind + " ", font=fonts.sans(17), fill=MUTED)
    rx = x + draw.textlength(kind + " ", font=fonts.sans(17))
    draw.text((rx, y), fit(draw, ref, fonts.sans(20, 700), inner - (rx - x)), font=fonts.sans(20, 700), fill=TEXT)
    y += 30
    for line in wrap(draw, title, fonts.sans(17), inner, 2):
        draw.text((x, y), line, font=fonts.sans(17), fill=MUTED)
        y += 23
    y += 3
    draw.text((x, y), fit(draw, url, fonts.mono(14), inner), font=fonts.mono(14), fill=LINK)
    y += 30
    draw.text((x, y), fit_parts(draw, facts, fonts.sans(16), inner), font=fonts.sans(16), fill=TEXT)
    y += 24
    draw.text((x, y), fit_parts(draw, labels, fonts.sans(16), inner, ", "), font=fonts.sans(16), fill=MUTED)
    y += 32
    draw.text((x, y), fit(draw, comments, fonts.sans(16, 700), inner), font=fonts.sans(16, 700), fill=TEXT)
    y += 24
    draw.text((x, y), fit(draw, last_author, fonts.mono(14), inner), font=fonts.mono(14), fill=ACCENT)
    y += 22
    draw.text((x, y), fit(draw, last_text, fonts.sans(16), inner), font=fonts.sans(16), fill=TEXT)
    y += 24
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
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "gh-ref-card.png")
    args = parser.parse_args()

    block = asyncio.run(real_output(load_plugin()))
    card = draw_card(Fonts(args.sans, args.mono), block)
    card.save(args.out, format="PNG", optimize=True)
    print(f"wrote {args.out} ({card.size[0]}x{card.size[1]}) from {REFERENCE}")


if __name__ == "__main__":
    main()
