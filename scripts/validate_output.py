#!/usr/bin/env python3
"""Check the basic alpha/crop invariants of an illustration-cleanup PNG."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida un PNG transparente de salida.")
    parser.add_argument("image", type=Path)
    parser.add_argument("--alpha-threshold", type=int, default=8)
    args = parser.parse_args()

    with Image.open(args.image) as image:
        mode = image.mode
        rgba = np.array(image.convert("RGBA"))
    alpha = rgba[..., 3]
    ys, xs = np.where(alpha > args.alpha_threshold)
    checks: dict[str, bool] = {}
    report: dict = {
        "input": str(args.image.resolve()),
        "mode": mode,
        "size": [int(rgba.shape[1]), int(rgba.shape[0])],
        "alpha_min": int(alpha.min()),
        "alpha_max": int(alpha.max()),
        "alpha_coverage": round(float(np.count_nonzero(alpha)) / max(1, alpha.size), 4),
        "corners": [
            int(alpha[0, 0]),
            int(alpha[0, -1]),
            int(alpha[-1, 0]),
            int(alpha[-1, -1]),
        ],
    }

    checks["rgba"] = mode == "RGBA"
    checks["has_alpha"] = int(alpha.min()) < 255 and int(alpha.max()) > 0
    checks["transparent_corners"] = all(value <= 1 for value in report["corners"])
    checks["has_subject"] = bool(xs.size)

    if xs.size:
        x1, y1 = int(xs.min()), int(ys.min())
        x2, y2 = int(xs.max()) + 1, int(ys.max()) + 1
        report["bbox"] = [x1, y1, x2, y2]
        report["margins"] = [
            x1,
            y1,
            int(alpha.shape[1]) - x2,
            int(alpha.shape[0]) - y2,
        ]
        checks["subject_not_empty"] = (x2 - x1) >= 2 and (y2 - y1) >= 2
        checks["coverage_sane"] = 0.005 <= report["alpha_coverage"] <= 0.95
    else:
        report["bbox"] = None
        report["margins"] = None
        checks["subject_not_empty"] = False
        checks["coverage_sane"] = False

    report["checks"] = checks
    report["ok"] = all(checks.values())
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
