#!/usr/bin/env python3
"""Create a flat-illustration fixture with text crossing a wing edge."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def main() -> None:
    width, height = 1000, 700
    image = Image.new("RGB", (width, height), "#F2E8CF")
    draw = ImageDraw.Draw(image)

    outline = "#1D3557"
    body = "#2A9D8F"
    wing = "#264653"
    beak = "#E9C46A"

    # Tail and legs.
    draw.polygon([(320, 400), (170, 340), (210, 460)], fill=wing, outline=outline)
    draw.line([(430, 510), (410, 610)], fill=outline, width=8)
    draw.line([(500, 515), (520, 610)], fill=outline, width=8)
    draw.line([(385, 615), (435, 615)], fill=outline, width=10)
    draw.line([(495, 615), (545, 615)], fill=outline, width=10)

    # Body and head.
    draw.ellipse((300, 280, 620, 530), fill=body, outline=outline, width=6)
    draw.ellipse((555, 225, 705, 375), fill=body, outline=outline, width=6)
    draw.ellipse((630, 275, 665, 310), fill="#FFFFFF", outline=outline, width=3)
    draw.ellipse((641, 286, 655, 300), fill="#111111")
    draw.polygon([(700, 300), (800, 320), (700, 345)], fill=beak, outline=outline)

    # Wing that extends to the right; the text crosses its tip.
    draw.polygon(
        [(470, 340), (845, 235), (875, 330), (760, 390), (470, 445)],
        fill=wing,
        outline=outline,
    )
    draw.line([(520, 380), (800, 300)], fill="#457B9D", width=4)
    draw.line([(540, 405), (790, 335)], fill="#457B9D", width=4)

    # Text over the wing edge and the background.
    font = load_font(86)
    draw.text((650, 300), "SALE", font=font, fill="#111111", stroke_width=2, stroke_fill="#111111")

    # A second, smaller text region in the background.
    small = load_font(34)
    draw.text((80, 80), "NEW", font=small, fill="#D62828")

    # A simple logo mark to exercise manual boxes.
    draw.ellipse((80, 540, 180, 640), fill="#D62828", outline=outline, width=4)
    draw.text((103, 565), "LO", font=load_font(38), fill="#FFFFFF")

    output = Path(__file__).resolve().parent.parent / "test-assets" / "flat-bird-text.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    print(output)


if __name__ == "__main__":
    main()
