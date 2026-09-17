#!/usr/bin/env python3
"""Generate the repo's brand images from the logo and the gate's real output.

    python3 scripts/make_brand_images.py

Writes, all under docs/assets/:

    banner.png          the README header card (1600x460, shown at 800)
    social-preview.png  GitHub's Open Graph card (1280x640, the size GitHub wants)
    verify-demo.png     a terminal card showing the verification gate, text from a real run

Why a card and not the bare logo: the wordmark's K is #E2F4F1, so on GitHub's
light theme it disappears against white. Compositing onto the brand navy makes
it legible in both themes and needs no <picture> switch.

The demo text is pasted from a real run of the tools against a throwaway repo,
never written by hand. Regenerate it with the script in DEMO_SOURCE below.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
LOGO = ASSETS / "logo.png"

# Brand, shared with k-mem so the two repos read as one project.
NAVY = (8, 12, 22)
PANEL = (16, 22, 38)
CYAN = (0, 225, 255)
ORANGE = (255, 148, 26)
FG = (241, 245, 249)
MUTED = (148, 163, 184)
RED = (248, 113, 113)
GREEN = (74, 222, 128)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

#: How the demo text was captured, so a future edit reproduces it rather than invents it.
DEMO_SOURCE = (
    "Build a throwaway git repo with a failing test, create a project with "
    "repo_path pointing at it, add a ticket carrying that test as verify_cmd, "
    "then call update_ticket_status(done) / verify_ticket / update_ticket_status(done) "
    "in that order, fixing the code between the two verify calls."
)

DEMO_LINES = [
    ("dim", "# T-001 carries verify_cmd: python3 -m pytest tests/test_rounding.py"),
    ("dim", ""),
    ("cmd", "> update_ticket_status(\"T-001\", \"done\")"),
    ("err", "T-001 needs verification before it can be done."),
    ("plain", "hint: Run verify_ticket(\"T-001\") first."),
    ("dim", ""),
    ("cmd", "> verify_ticket(\"T-001\")"),
    ("err", "passed: false   exit: 1   rev: 2b8498e"),
    ("plain", "  assert 3 == 2"),
    ("plain", "  FAILED tests/test_rounding.py::test_half_even"),
    ("dim", ""),
    ("cmd", "> update_ticket_status(\"T-001\", \"done\")"),
    ("err", "T-001 last failed verification (exit 1)."),
    ("dim", ""),
    ("dim", "# fix the rounding, commit"),
    ("dim", ""),
    ("cmd", "> verify_ticket(\"T-001\")"),
    ("ok", "passed: true    exit: 0   rev: 8cc2b89"),
    ("dim", ""),
    ("cmd", "> update_ticket_status(\"T-001\", \"done\")"),
    ("ok", "success: true   status: done   proof recorded on 8cc2b89"),
]

COLOURS = {
    "dim": MUTED, "cmd": CYAN, "err": RED, "plain": FG, "ok": GREEN,
}


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def rounded_card(size: tuple[int, int], radius: int, fill: tuple[int, int, int]) -> Image.Image:
    """A card with transparent corners, so it sits well on either GitHub theme."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle(
        [(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=fill + (255,)
    )
    return im


def fit_logo(max_w: int, max_h: int) -> Image.Image:
    logo = Image.open(LOGO).convert("RGBA")
    scale = min(max_w / logo.width, max_h / logo.height)
    return logo.resize((round(logo.width * scale), round(logo.height * scale)), Image.LANCZOS)


def centred(d: ImageDraw.ImageDraw, y: int, text: str, f: ImageFont.FreeTypeFont, fill, width: int) -> None:
    w = d.textbbox((0, 0), text, font=f)[2]
    d.text(((width - w) // 2, y), text, font=f, fill=fill)


def make_banner() -> Path:
    W, H = 1600, 460
    im = rounded_card((W, H), 28, NAVY)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([(0, 0), (W - 1, 5)], radius=3, fill=ORANGE + (255,))
    logo = fit_logo(int(W * 0.56), 250)
    im.alpha_composite(logo, ((W - logo.width) // 2, 62))
    y = 62 + logo.height + 30
    centred(d, y, "DevFlow: the ticket tracker for AI coding agents", font(SANS, 40), FG, W)
    centred(d, y + 58, "Every ticket states why. A ticket cannot close until its check passes on the current commit.",
            font(SANS, 25), MUTED, W)
    im.save(ASSETS / "banner.png", optimize=True)
    return ASSETS / "banner.png"


def make_social() -> Path:
    W, H = 1280, 640  # the size GitHub asks for
    im = Image.new("RGBA", (W, H), NAVY + (255,))
    d = ImageDraw.Draw(im)
    d.rectangle([(0, 0), (W, 7)], fill=ORANGE)
    d.rectangle([(0, H - 7), (W, H)], fill=CYAN)
    logo = fit_logo(int(W * 0.60), 230)
    im.alpha_composite(logo, ((W - logo.width) // 2, 92))
    y = 92 + logo.height + 34
    centred(d, y, "DevFlow", font(SANS_BOLD, 66), FG, W)
    centred(d, y + 86, "The ticket tracker for AI coding agents", font(SANS, 34), ORANGE, W)
    centred(d, y + 140, "A mandatory why  ·  dependencies  ·  a close that must be earned",
            font(SANS, 26), MUTED, W)
    centred(d, H - 74, "github.com/PyGuy2000/devflow-mcp", font(MONO, 25), MUTED, W)
    im.convert("RGB").save(ASSETS / "social-preview.png", optimize=True)
    return ASSETS / "social-preview.png"


def make_verify_demo() -> Path:
    pad, lh = 40, 30
    f = font(MONO, 20)
    W = 1120
    H = pad * 2 + 52 + lh * len(DEMO_LINES)
    im = rounded_card((W, H), 16, PANEL)
    d = ImageDraw.Draw(im)
    # a title bar with the three dots, so it reads as a terminal without a screenshot
    d.rounded_rectangle([(0, 0), (W - 1, 52)], radius=16, fill=(11, 16, 29, 255))
    d.rectangle([(0, 36), (W - 1, 52)], fill=(11, 16, 29, 255))
    for i, c in enumerate([(248, 113, 113), (255, 193, 87), (74, 222, 128)]):
        d.ellipse([(24 + i * 24, 20), (36 + i * 24, 32)], fill=c)
    d.text((112, 17), "devflow · the verification gate", font=font(MONO, 18), fill=MUTED)
    y = 52 + pad
    for kind, text in DEMO_LINES:
        if text:
            d.text((pad, y), text, font=f, fill=COLOURS[kind])
        y += lh
    im.save(ASSETS / "verify-demo.png", optimize=True)
    return ASSETS / "verify-demo.png"


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for build in (make_banner, make_social, make_verify_demo):
        p = build()
        with Image.open(p) as im:
            print(f"{p.relative_to(ROOT)}  {im.width}x{im.height}  {p.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
