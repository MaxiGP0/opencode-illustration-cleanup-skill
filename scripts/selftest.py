#!/usr/bin/env python3
"""Lightweight tests that do not need rembg, LaMa, or model downloads."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import (  # noqa: E402
    build_stroke_mask,
    crop_to_bbox,
    filter_alpha_components,
    hex_to_rgb,
    parse_box,
)


def test_helpers() -> None:
    assert hex_to_rgb("#111111") == (17, 17, 17)
    assert hex_to_rgb("fff") == (255, 255, 255)
    assert parse_box("1,2,30,40") == (1, 2, 30, 40)


def test_stroke_mask() -> None:
    rgb = np.full((160, 240, 3), (242, 232, 207), np.uint8)
    rgb[40:120, 80:200] = (38, 70, 83)
    for x in (100, 115, 130, 145):
        rgb[60:70, x : x + 10] = (17, 17, 17)

    mask, method = build_stroke_mask(
        rgb,
        rect=(90, 50, 160, 110),
        mode="auto",
        threshold=30.0,
        text_colors=[(17, 17, 17)],
    )
    assert method.startswith("color"), method
    assert int(mask.sum()) > 0
    assert int(mask[65, 105]) == 255, "the text stroke should be masked"
    assert int(mask[55, 90]) == 0, "the wing outside the text should stay untouched"


def test_crop_and_components() -> None:
    rgba = np.zeros((100, 100, 4), np.uint8)
    rgba[40:60, 30:50] = (255, 0, 0, 255)

    cropped, raw_bbox, crop_rect = crop_to_bbox(
        rgba, padding=5, square=False, threshold=8
    )
    assert raw_bbox == (30, 40, 50, 60), raw_bbox
    assert crop_rect == (25, 35, 55, 65), crop_rect
    assert cropped.shape == (30, 30, 4), cropped.shape

    rgba[80:85, 80:85] = (0, 255, 0, 255)
    filtered = rgba.copy()
    filter_alpha_components(filtered, keep_largest=True, min_component=0.0)
    assert filtered[45, 35, 3] == 255, "the main subject should remain"
    assert filtered[82, 82, 3] == 0, "the small island should be dropped"


def main() -> int:
    test_helpers()
    test_stroke_mask()
    test_crop_and_components()
    print("selftest: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
