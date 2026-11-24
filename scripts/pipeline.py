#!/usr/bin/env python3
"""
Illustration cleanup pipeline.

Order matters:
1. Detect overlaid text/logo regions and build a stroke-level erase mask.
2. Inpaint the erase mask (LaMa, with OpenCV fallback).
3. Remove the background from the cleaned image (BiRefNet via rembg).
4. Refine the alpha edge (feather or closed-form matting).
5. Crop to the subject's alpha bounding box and export a transparent PNG.

The pipeline is intentionally conservative: it never overwrites the input, and it
writes a JSON report plus debug masks so the result can be verified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional, Sequence

# Keep model caches inside the skill unless the caller overrides them.
SKILL_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault("U2NET_HOME", str(SKILL_DIR / "models" / "rembg"))
os.environ.setdefault("TORCH_HOME", str(SKILL_DIR / "models" / "torch"))
os.environ.setdefault("HF_HOME", str(SKILL_DIR / "models" / "hf"))
os.environ.setdefault("PYTHONUTF8", "1")

import cv2
import numpy as np
from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def log(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message, flush=True)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise argparse.ArgumentTypeError(f"Color inválido: {value!r}; usá #RRGGBB")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def parse_box(value: str) -> tuple[int, int, int, int]:
    parts = re.split(r"[,\s;]+", value.strip())
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Box inválido: {value!r}; usá x1,y1,x2,y2"
        )
    try:
        x1, y1, x2, y2 = (int(round(float(p))) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Box inválido: {value!r}") from exc
    if x2 <= x1 or y2 <= y1:
        raise argparse.ArgumentTypeError(f"Box sin área: {value!r}")
    return x1, y1, x2, y2


def clamp_rect(rect: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = rect
    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(x1 + 1, min(x2, width))
    y2 = max(y1 + 1, min(y2, height))
    return x1, y1, x2, y2


def expand_rect(rect: tuple[int, int, int, int], px: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = rect
    return x1 - px, y1 - px, x2 + px, y2 + px


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"No pude encontrar un nombre libre para {path}")


def load_rgb(path: Path) -> tuple[np.ndarray, Optional[np.ndarray]]:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA"):
            rgba = image.convert("RGBA")
            array = np.array(rgba)
            return array[..., :3].copy(), array[..., 3].copy()
        return np.array(image.convert("RGB")), None


def save_rgba(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path)


def save_gray(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask, mode="L").save(path)


# ---------------------------------------------------------------------------
# Erase-mask generation
# ---------------------------------------------------------------------------


@dataclass
class TextDetection:
    rect: tuple[int, int, int, int]
    text: str
    score: float


def _border_pixels(crop: np.ndarray, border: int = 3) -> np.ndarray:
    height, width = crop.shape[:2]
    border = max(1, min(border, height // 4 or 1, width // 4 or 1))
    parts = [
        crop[:border, :, :].reshape(-1, 3),
        crop[-border:, :, :].reshape(-1, 3),
        crop[:, :border, :].reshape(-1, 3),
        crop[:, -border:, :].reshape(-1, 3),
    ]
    return np.concatenate(parts, axis=0)


def _clean_stroke_mask(mask: np.ndarray) -> np.ndarray:
    """Remove speckles but keep thin text strokes."""
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= 3:
            cleaned[labels == label] = 255
    return cleaned


def build_stroke_mask(
    rgb: np.ndarray,
    rect: tuple[int, int, int, int],
    mode: str,
    threshold: float,
    text_colors: Sequence[tuple[int, int, int]],
) -> tuple[np.ndarray, str]:
    """Build a mask for the actual strokes inside a text/logo box.

    Returns (mask, method). The mask is a full-size uint8 image.
    """
    height, width = rgb.shape[:2]
    x1, y1, x2, y2 = clamp_rect(rect, width, height)
    full = np.zeros((height, width), np.uint8)
    crop = rgb[y1:y2, x1:x2]
    if crop.size == 0:
        return full, "empty"

    if mode == "box":
        full[y1:y2, x1:x2] = 255
        return full, "box"

    border = _border_pixels(crop)
    background = np.median(border, axis=0)
    crop_height, crop_width = crop.shape[:2]

    if text_colors:
        distances = [
            np.linalg.norm(
                crop.astype(np.float32) - np.array(color, dtype=np.float32), axis=2
            )
            for color in text_colors
        ]
        distance = np.min(np.stack(distances, axis=0), axis=0)
        raw = (distance <= threshold).astype(np.uint8) * 255
        stroke, kept = _filter_stroke_components(raw, crop_width, crop_height)
        method = "color" if kept else "color-uncertain"
    elif mode == "border":
        distance = np.linalg.norm(
            crop.astype(np.float32) - background.astype(np.float32), axis=2
        )
        raw = (distance >= threshold).astype(np.uint8) * 255
        stroke, kept = _filter_stroke_components(raw, crop_width, crop_height)
        method = "border" if kept else "border-uncertain"
    elif mode in ("auto", "kmeans"):
        stroke, method = _auto_color_mask(crop)
    else:
        stroke = np.zeros((crop_height, crop_width), np.uint8)
        method = "unknown-mode"

    full[y1:y2, x1:x2] = stroke
    return full, method


def _filter_stroke_components(
    mask: np.ndarray, width: int, height: int
) -> tuple[np.ndarray, int]:
    """Drop subject blobs and long decorative lines, keep letter-like pieces."""
    box_area = max(1, width * height)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    kept = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        comp_w = int(stats[label, cv2.CC_STAT_WIDTH])
        comp_h = int(stats[label, cv2.CC_STAT_HEIGHT])
        if area < 3:
            continue
        if area > 0.18 * box_area:
            continue
        if comp_w > 0.80 * width and comp_h < 0.25 * height:
            continue
        if comp_h > 0.80 * height and comp_w < 0.25 * width:
            continue
        fill = area / max(1, comp_w * comp_h)
        if area > 0.12 * box_area and fill > 0.65:
            continue
        cleaned[labels == label] = 255
        kept += 1
    return cleaned, kept


def _mask_score(mask: np.ndarray, width: int, height: int) -> tuple[float, int, float]:
    """Score a mask by how much it looks like a set of text strokes."""
    box_area = max(1, width * height)
    total = float(np.count_nonzero(mask)) / box_area
    if total < 0.004 or total > 0.55:
        return -10.0, 0, total
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    score = 0.0
    pieces = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        fraction = area / box_area
        if fraction < 0.0004 or fraction > 0.08:
            continue
        pieces += 1
        score += 1.0 + min(fraction / 0.01, 2.0)
        component = (labels == label).astype(np.uint8)
        contours, _ = cv2.findContours(
            component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if contours:
            contour = max(contours, key=cv2.contourArea)
            perimeter = cv2.arcLength(contour, True)
            compactness = perimeter * perimeter / max(area, 1)
            if 8.0 <= compactness <= 150.0:
                score += 0.5
    return score, pieces, total


def _auto_color_mask(crop: np.ndarray) -> tuple[np.ndarray, str]:
    """Try colour clusters and keep the one that looks most like text.

    This is deliberately conservative: if no cluster produces letter-like
    components, it returns an empty mask instead of a rectangular box. A box
    over text that crosses a subject edge is what creates a notch.
    """
    height, width = crop.shape[:2]
    data = crop.reshape(-1, 3).astype(np.float32)
    if data.shape[0] < 16:
        return np.zeros((height, width), np.uint8), "auto-uncertain"
    k = min(5, max(2, data.shape[0] // 8))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 25, 0.5)
    try:
        _, labels, centers = cv2.kmeans(
            data, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS
        )
    except cv2.error:
        return np.zeros((height, width), np.uint8), "auto-uncertain"
    labels = labels.reshape(height, width)

    # Text is normally the darkest or the brightest thing inside its own box.
    # Restricting to those two clusters avoids decorative mid-tone lines.
    luminance = centers @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    order = np.argsort(luminance)
    candidates = {int(order[0]), int(order[-1])}

    best: Optional[tuple[float, np.ndarray, int, float]] = None
    for index in candidates:
        raw = (labels == index).astype(np.uint8) * 255
        filtered, kept = _filter_stroke_components(raw, width, height)
        if kept == 0:
            continue
        score, pieces, total = _mask_score(filtered, width, height)
        if best is None or score > best[0]:
            best = (score, filtered, pieces, total)

    if best is None:
        return np.zeros((height, width), np.uint8), "auto-uncertain"
    score, mask, pieces, total = best
    if score < 3.0 or pieces < 1:
        return np.zeros((height, width), np.uint8), "auto-uncertain"
    return mask, "auto-color"


def detect_text(
    rgb: np.ndarray, min_score: float, ocr_scale: float, quiet: bool
) -> list[TextDetection]:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "RapidOCR no está instalado. Ejecutá scripts\\bootstrap.ps1 primero."
        ) from exc

    height, width = rgb.shape[:2]
    scale = ocr_scale if ocr_scale > 0 else (2.0 if max(height, width) < 1600 else 1.0)
    if scale != 1.0:
        ocr_image = cv2.resize(
            rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
        )
    else:
        ocr_image = rgb

    engine = RapidOCR()
    # RapidOCR expects BGR when it receives an ndarray.
    result, _ = engine(cv2.cvtColor(ocr_image, cv2.COLOR_RGB2BGR))
    detections: list[TextDetection] = []
    if result is None:
        return detections

    items: Iterable
    if hasattr(result, "boxes") and hasattr(result, "txts"):
        items = zip(result.boxes, result.txts, result.scores)  # type: ignore[attr-defined]
    else:
        items = result  # type: ignore[assignment]

    for item in items:
        try:
            if hasattr(item, "boxes"):
                continue
            polygon, text, score = item[0], item[1], item[2]
            score = float(score)
            if score < min_score:
                continue
            points = np.array(polygon, dtype=np.float32).reshape(-1, 2)
            x1, y1 = np.floor(points.min(axis=0) / scale).astype(int)
            x2, y2 = np.ceil(points.max(axis=0) / scale).astype(int)
            detections.append(
                TextDetection(
                    rect=clamp_rect((int(x1), int(y1), int(x2), int(y2)), width, height),
                    text=str(text),
                    score=score,
                )
            )
        except (IndexError, TypeError, ValueError):
            continue

    log(f"  OCR: {len(detections)} región(es) de texto", quiet)
    return detections


def filter_detections(
    detections: Sequence[TextDetection],
    erase_text: Sequence[str],
    keep_text: Sequence[str],
) -> list[TextDetection]:
    selected = list(detections)
    if erase_text:
        needles = [value.casefold() for value in erase_text]
        selected = [
            item
            for item in selected
            if any(needle in item.text.casefold() for needle in needles)
        ]
    if keep_text:
        needles = [value.casefold() for value in keep_text]
        selected = [
            item
            for item in selected
            if not any(needle in item.text.casefold() for needle in needles)
        ]
    return selected


def build_erase_mask(
    rgb: np.ndarray,
    detections: Sequence[TextDetection],
    erase_boxes: Sequence[tuple[int, int, int, int]],
    manual_mask: Optional[np.ndarray],
    mask_mode: str,
    threshold: float,
    text_colors: Sequence[tuple[int, int, int]],
    expand: int,
    feather: float,
    quiet: bool,
) -> tuple[np.ndarray, list[dict], list[str], list[str]]:
    height, width = rgb.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    methods: list[str] = []
    warnings: list[str] = []
    boxes_report: list[dict] = []

    for detection in detections:
        stroke, method = build_stroke_mask(
            rgb, detection.rect, mask_mode, threshold, text_colors
        )
        applied = bool(np.any(stroke))
        mask = np.maximum(mask, stroke)
        methods.append(method)
        boxes_report.append(
            {
                "kind": "text",
                "text": detection.text,
                "score": round(detection.score, 4),
                "rect": list(detection.rect),
                "mask_method": method,
                "applied": applied,
            }
        )
        if not applied:
            warnings.append(
                f"No pude aislar el trazo de {detection.text!r} "
                f"(mask-method={method}); no se borró. "
                "Usá --text-color o --mask para esa región."
            )

    for rect in erase_boxes:
        stroke, method = build_stroke_mask(
            rgb, rect, mask_mode, threshold, text_colors
        )
        applied = bool(np.any(stroke))
        mask = np.maximum(mask, stroke)
        methods.append(method)
        boxes_report.append(
            {
                "kind": "manual-box",
                "text": None,
                "score": None,
                "rect": list(clamp_rect(rect, width, height)),
                "mask_method": method,
                "applied": applied,
            }
        )
        if not applied:
            warnings.append(
                f"No pude aislar el trazo dentro de la caja {rect} "
                f"(mask-method={method}); no se borró. "
                "Probá --text-color, --mask-mode border o --mask."
            )

    if manual_mask is not None:
        if manual_mask.shape[:2] != (height, width):
            manual_mask = cv2.resize(
                manual_mask, (width, height), interpolation=cv2.INTER_NEAREST
            )
        mask = np.maximum(mask, manual_mask)
        methods.append("manual-mask")

    if expand > 0 and np.any(mask):
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * expand + 1, 2 * expand + 1)
        )
        mask = cv2.dilate(mask, kernel, iterations=1)
    if feather > 0 and np.any(mask):
        mask = cv2.GaussianBlur(mask, (0, 0), feather)
    if np.any(mask):
        _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

    if methods:
        log(f"  Máscara: {', '.join(sorted(set(methods)))}", quiet)
    return mask, boxes_report, methods, warnings


# ---------------------------------------------------------------------------
# Inpainting
# ---------------------------------------------------------------------------


class LamaInpainter:
    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        from simple_lama_inpainting import SimpleLama

        try:
            self._model = SimpleLama(device=self.device)
        except TypeError:
            self._model = SimpleLama()

    def inpaint(self, rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
        self._load()
        image = Image.fromarray(rgb, mode="RGB")
        mask_image = Image.fromarray(mask, mode="L")
        result = self._model(image, mask_image)  # type: ignore[misc]
        if isinstance(result, Image.Image):
            result = np.array(result)
        if result.ndim == 3 and result.shape[2] == 4:
            result = result[..., :3]
        # LaMa pads to a multiple of 8; remove the extra rows/columns.
        if result.shape[0] != rgb.shape[0] or result.shape[1] != rgb.shape[1]:
            result = result[: rgb.shape[0], : rgb.shape[1]]
        return np.clip(result, 0, 255).astype(np.uint8)


def inpaint_opencv(rgb: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    result = cv2.inpaint(bgr, mask, radius, cv2.INPAINT_TELEA)
    return cv2.cvtColor(result, cv2.COLOR_BGR2RGB)


def inpaint_image(
    rgb: np.ndarray,
    mask: np.ndarray,
    backend: str,
    radius: int,
    lama: Optional[LamaInpainter],
    quiet: bool,
) -> tuple[np.ndarray, str, list[str]]:
    warnings: list[str] = []
    if not np.any(mask):
        return rgb, "none", warnings
    if backend == "none":
        warnings.append("Inpainting desactivado; el texto/logo queda en la imagen.")
        return rgb, "none", warnings

    if backend in ("auto", "lama"):
        try:
            if lama is None:
                lama = LamaInpainter()
            result = lama.inpaint(rgb, mask)
            log("  Inpainting: LaMa", quiet)
            return result, "lama", warnings
        except Exception as exc:
            if backend == "lama":
                raise
            warnings.append(f"LaMa no disponible ({exc}); usé OpenCV Telea.")
            log("  LaMa falló; usando OpenCV Telea", quiet)

    if backend in ("auto", "opencv"):
        log("  Inpainting: OpenCV Telea", quiet)
        return inpaint_opencv(rgb, mask, radius), "opencv", warnings

    raise ValueError(f"Backend de inpainting desconocido: {backend}")


# ---------------------------------------------------------------------------
# Background removal + alpha
# ---------------------------------------------------------------------------


def remove_background(
    rgb: np.ndarray, model: str, session_cache: dict
) -> tuple[np.ndarray, str, list[str]]:
    from rembg import new_session, remove

    warnings: list[str] = []
    candidates = [model, "birefnet-general-lite", "isnet-general-use", "u2net"]
    seen: set[str] = set()
    last_error: Optional[Exception] = None
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if candidate not in session_cache:
                session_cache[candidate] = new_session(candidate)
            session = session_cache[candidate]
            result = remove(
                Image.fromarray(rgb, mode="RGB"),
                session=session,
                alpha_matting=False,
            )
            if not isinstance(result, Image.Image):
                result = Image.open(result)
            rgba = np.array(result.convert("RGBA"))
            if candidate != model:
                warnings.append(
                    f"El modelo {model!r} no estaba disponible; usé {candidate!r}."
                )
            return rgba, candidate, warnings
        except Exception as exc:  # model download or runtime error
            last_error = exc
            warnings.append(f"Modelo {candidate!r} falló: {exc}")
            continue
    raise RuntimeError(
        "No pude cargar ningún modelo de segmentación. "
        f"Último error: {last_error}"
    )


def make_trimap(alpha: np.ndarray) -> np.ndarray:
    foreground = ((alpha > 250).astype(np.uint8) * 255)
    background = ((alpha < 5).astype(np.uint8) * 255)
    kernel = np.ones((3, 3), np.uint8)
    foreground = cv2.erode(foreground, kernel, iterations=1)
    background = cv2.erode(background, kernel, iterations=1)
    trimap = np.full(alpha.shape, 0.5, np.float64)
    trimap[foreground > 0] = 1.0
    trimap[background > 0] = 0.0
    return trimap


def refine_alpha(
    rgb: np.ndarray,
    rgba: np.ndarray,
    mode: str,
    feather: float,
    matting_max_dim: int,
    force_matting: bool,
    quiet: bool,
) -> tuple[np.ndarray, str, list[str]]:
    warnings: list[str] = []
    alpha = rgba[..., 3].astype(np.float64)
    if mode == "none":
        return rgba, "none", warnings

    if mode == "cf":
        height, width = alpha.shape
        if max(height, width) > matting_max_dim and not force_matting:
            warnings.append(
                f"Imagen {width}x{height} mayor que --matting-max-dim "
                f"{matting_max_dim}; usé feather en vez de matting CF."
            )
            mode = "feather"
        else:
            try:
                from pymatting import estimate_alpha_cf

                trimap = make_trimap(alpha.astype(np.uint8))
                if not np.any(trimap == 0.5):
                    warnings.append("No hay zona desconocida; matting CF omitido.")
                else:
                    estimated = estimate_alpha_cf(
                        rgb.astype(np.float64) / 255.0, trimap
                    )
                    rgba[..., 3] = np.clip(estimated * 255.0, 0, 255).astype(
                        np.uint8
                    )
                    log("  Alpha: matting closed-form", quiet)
                    return rgba, "cf", warnings
            except Exception as exc:
                warnings.append(f"Matting CF falló ({exc}); usé feather.")
                mode = "feather"

    if mode == "feather":
        if feather > 0:
            alpha = cv2.GaussianBlur(alpha, (0, 0), feather)
        rgba[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
        log(f"  Alpha: feather ({feather}px)", quiet)
        return rgba, "feather", warnings

    return rgba, mode, warnings


def despill(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    height, width = alpha.shape
    patch = max(2, min(24, height // 20 or 2, width // 20 or 2))
    corners = [
        rgb[:patch, :patch],
        rgb[:patch, -patch:],
        rgb[-patch:, :patch],
        rgb[-patch:, -patch:],
    ]
    background = np.median(
        np.concatenate([corner.reshape(-1, 3) for corner in corners], axis=0),
        axis=0,
    ).astype(np.float32)
    a = (alpha.astype(np.float32) / 255.0)[..., None]
    safe = np.maximum(a, 0.15)
    unmixed = (rgb.astype(np.float32) - (1.0 - a) * background) / safe
    edge = ((alpha > 0) & (alpha < 255))[..., None]
    result = np.where(edge, unmixed, rgb.astype(np.float32))
    return np.clip(result, 0, 255).astype(np.uint8)


def trim_alpha(rgba: np.ndarray) -> None:
    alpha = rgba[..., 3]
    alpha[alpha < 2] = 0
    alpha[alpha > 253] = 255


def filter_alpha_components(
    rgba: np.ndarray, keep_largest: bool, min_component: float
) -> None:
    """Drop isolated alpha islands (a stray logo, dust, detached specks)."""
    alpha = rgba[..., 3]
    binary = (alpha > 8).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return
    areas = [
        (label, int(stats[label, cv2.CC_STAT_AREA]))
        for label in range(1, count)
    ]
    largest = max(area for _, area in areas)
    keep = np.zeros_like(binary)
    for label, area in areas:
        if keep_largest:
            if area == largest:
                keep[labels == label] = 1
        elif min_component > 0:
            if area >= min_component * largest:
                keep[labels == label] = 1
        else:
            keep[labels == label] = 1
    rgba[..., 3] = np.where(keep > 0, alpha, 0)


# ---------------------------------------------------------------------------
# Crop + preview
# ---------------------------------------------------------------------------


def crop_to_bbox(
    rgba: np.ndarray,
    padding: int,
    square: bool,
    threshold: int,
) -> tuple[np.ndarray, tuple[int, int, int, int], tuple[int, int, int, int]]:
    alpha = rgba[..., 3]
    ys, xs = np.where(alpha > threshold)
    if xs.size == 0:
        raise ValueError(
            "No se detectó sujeto (alpha vacío). Probá otro --model o revisá la imagen."
        )
    raw_bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    height, width = rgba.shape[:2]
    x1, y1, x2, y2 = expand_rect(raw_bbox, padding)
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(width, x2)
    y2 = min(height, y2)
    cropped = rgba[y1:y2, x1:x2].copy()

    if padding > 0:
        # Add real transparent padding even when the subject touches the border.
        top = max(0, raw_bbox[1] - (y1))
        left = max(0, raw_bbox[0] - (x1))
        bottom = max(0, (y2 - raw_bbox[3]))
        right = max(0, (x2 - raw_bbox[2]))
        target_h = cropped.shape[0] + max(0, padding - top) + max(0, padding - bottom)
        target_w = cropped.shape[1] + max(0, padding - left) + max(0, padding - right)
        canvas = np.zeros((target_h, target_w, 4), np.uint8)
        offset_y = max(0, padding - top)
        offset_x = max(0, padding - left)
        canvas[offset_y : offset_y + cropped.shape[0], offset_x : offset_x + cropped.shape[1]] = cropped
        cropped = canvas

    if square:
        height, width = cropped.shape[:2]
        side = max(height, width)
        canvas = np.zeros((side, side, 4), np.uint8)
        offset_y = (side - height) // 2
        offset_x = (side - width) // 2
        canvas[offset_y : offset_y + height, offset_x : offset_x + width] = cropped
        cropped = canvas

    return cropped, raw_bbox, (x1, y1, x2, y2)


def make_preview(rgba: np.ndarray, tile: int = 16) -> np.ndarray:
    height, width = rgba.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    board = (((yy // tile) + (xx // tile)) % 2).astype(np.uint8)
    background = np.where(board[..., None] == 0, 190, 235).repeat(3, axis=2).astype(
        np.float32
    )
    alpha = (rgba[..., 3].astype(np.float32) / 255.0)[..., None]
    foreground = rgba[..., :3].astype(np.float32)
    composed = foreground * alpha + background * (1.0 - alpha)
    return np.clip(composed, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Per-image pipeline
# ---------------------------------------------------------------------------


@dataclass
class PipelineOptions:
    output: Path
    model: str
    matting: str
    feather: float
    matting_max_dim: int
    force_matting: bool
    despill: bool
    alpha_threshold: int
    padding: int
    square: bool
    keep_largest: bool
    min_component: float
    use_existing_alpha: bool
    ignore_existing_alpha: bool
    ocr: bool
    ocr_min_score: float
    ocr_scale: float
    erase_text: Sequence[str]
    keep_text: Sequence[str]
    erase_boxes: Sequence[tuple[int, int, int, int]]
    mask_path: Optional[Path]
    mask_mode: str
    mask_threshold: float
    text_colors: Sequence[tuple[int, int, int]]
    mask_expand: int
    mask_feather: float
    inpaint: str
    inpaint_radius: int
    no_crop: bool
    overwrite: bool
    quiet: bool


def process_one(
    input_path: Path,
    output_path: Path,
    options: PipelineOptions,
    session_cache: dict,
    lama_cache: dict,
) -> dict:
    start = time.time()
    warnings: list[str] = []
    log(f"Procesando: {input_path}", options.quiet)

    rgb, existing_alpha = load_rgb(input_path)
    height, width = rgb.shape[:2]
    if max(height, width) > 6000:
        warnings.append(
            f"Imagen grande ({width}x{height}); el inpainting y el matting pueden tardar."
        )

    # 1. Build the erase mask.
    detections: list[TextDetection] = []
    if options.ocr:
        detections = detect_text(
            rgb, options.ocr_min_score, options.ocr_scale, options.quiet
        )
        detections = filter_detections(
            detections, options.erase_text, options.keep_text
        )

    manual_mask = None
    if options.mask_path is not None:
        with Image.open(options.mask_path) as mask_image:
            manual_mask = np.array(mask_image.convert("L"))

    erase_mask, boxes_report, mask_methods, mask_warnings = build_erase_mask(
        rgb=rgb,
        detections=detections,
        erase_boxes=options.erase_boxes,
        manual_mask=manual_mask,
        mask_mode=options.mask_mode,
        threshold=options.mask_threshold,
        text_colors=options.text_colors,
        expand=options.mask_expand,
        feather=options.mask_feather,
        quiet=options.quiet,
    )
    warnings.extend(mask_warnings)

    stem = output_path.stem
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mask_path = unique_path(output_path.with_name(f"{stem}_erase-mask.png"))
    save_gray(mask_path, erase_mask)

    # 2. Inpaint the text/logo before segmentation so the subject edge can be rebuilt.
    if options.inpaint != "none" and np.any(erase_mask):
        lama = None
        if options.inpaint in ("auto", "lama"):
            if "lama" not in lama_cache:
                lama_cache["lama"] = LamaInpainter()
            lama = lama_cache["lama"]
        cleaned_rgb, inpaint_backend, inpaint_warnings = inpaint_image(
            rgb, erase_mask, options.inpaint, options.inpaint_radius, lama, options.quiet
        )
        warnings.extend(inpaint_warnings)
        cleaned_path = unique_path(output_path.with_name(f"{stem}_cleaned.png"))
        Image.fromarray(cleaned_rgb, mode="RGB").save(cleaned_path)
    else:
        cleaned_rgb = rgb
        inpaint_backend = "none"
        cleaned_path = None
        if np.any(erase_mask):
            warnings.append("Había máscara de borrado pero el inpainting está desactivado.")

    # 3. Background removal.
    if (
        existing_alpha is not None
        and options.use_existing_alpha
        and not options.ignore_existing_alpha
        and np.any(existing_alpha < 255)
    ):
        rgba = np.dstack([cleaned_rgb, existing_alpha])
        model_used = "existing-alpha"
        warnings.append("Usé el canal alpha existente en vez de segmentar.")
    else:
        rgba, model_used, bg_warnings = remove_background(
            cleaned_rgb, options.model, session_cache
        )
        warnings.extend(bg_warnings)
        if rgba.shape[:2] != cleaned_rgb.shape[:2]:
            rgba = cv2.resize(
                rgba,
                (cleaned_rgb.shape[1], cleaned_rgb.shape[0]),
                interpolation=cv2.INTER_LANCZOS4,
            )

    # 4. Alpha refinement.
    rgba, matting_used, matting_warnings = refine_alpha(
        cleaned_rgb,
        rgba,
        options.matting,
        options.feather,
        options.matting_max_dim,
        options.force_matting,
        options.quiet,
    )
    warnings.extend(matting_warnings)

    if options.despill:
        rgba[..., :3] = despill(cleaned_rgb, rgba[..., 3])
    trim_alpha(rgba)
    if options.keep_largest or options.min_component > 0:
        filter_alpha_components(
            rgba, options.keep_largest, options.min_component
        )

    # 5. Crop.
    if options.no_crop:
        cropped = rgba
        raw_bbox = (0, 0, rgba.shape[1], rgba.shape[0])
        crop_bbox = raw_bbox
    else:
        cropped, raw_bbox, crop_bbox = crop_to_bbox(
            rgba, options.padding, options.square, options.alpha_threshold
        )

    save_rgba(output_path, cropped)
    preview_path = unique_path(output_path.with_name(f"{stem}_preview.png"))
    Image.fromarray(make_preview(cropped), mode="RGB").save(preview_path)
    alpha_path = unique_path(output_path.with_name(f"{stem}_alpha.png"))
    save_gray(alpha_path, cropped[..., 3])

    alpha = cropped[..., 3]
    alpha_pixels = int(np.count_nonzero(alpha))
    report = {
        "input": str(input_path.resolve()),
        "output": str(output_path.resolve()),
        "preview": str(preview_path.resolve()),
        "erase_mask": str(mask_path.resolve()),
        "alpha": str(alpha_path.resolve()),
        "cleaned": str(cleaned_path.resolve()) if cleaned_path else None,
        "source_size": [width, height],
        "output_size": [int(cropped.shape[1]), int(cropped.shape[0])],
        "bbox_source": [int(value) for value in raw_bbox],
        "crop_rect_source": [int(value) for value in crop_bbox],
        "alpha_pixels": alpha_pixels,
        "alpha_coverage": round(alpha_pixels / max(1, alpha.size), 4),
        "model_requested": options.model,
        "model_used": model_used,
        "inpaint_backend": inpaint_backend,
        "matting": matting_used,
        "keep_largest": options.keep_largest,
        "min_component": options.min_component,
        "mask_methods": sorted(set(mask_methods)),
        "text_detections": [asdict(item) for item in detections],
        "erase_regions": boxes_report,
        "warnings": warnings,
        "elapsed_sec": round(time.time() - start, 2),
    }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def iter_images(
    root: Path, recursive: bool, skip_dir: Optional[Path] = None
) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    pattern = "**/*" if recursive else "*"
    skip_resolved = skip_dir.resolve() if skip_dir is not None else None
    for path in sorted(root.glob(pattern)):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if skip_resolved is not None:
            try:
                path.resolve().relative_to(skip_resolved)
                continue
            except ValueError:
                pass
        yield path


def default_output(input_path: Path) -> Path:
    if input_path.is_dir():
        return input_path / "illustration-cleanup-output"
    return input_path.parent / "illustration-cleanup-output"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Segmenta, limpia y recorta ilustraciones con fondo plano.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", type=Path, help="Imagen o carpeta de entrada")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="Carpeta de salida"
    )
    parser.add_argument("--recursive", action="store_true", help="Recorrer subcarpetas")
    parser.add_argument("--overwrite", action="store_true", help="Sobrescribir salidas")

    segment = parser.add_argument_group("Segmentación y alpha")
    segment.add_argument(
        "--model",
        default="birefnet-general",
        help="Modelo rembg (birefnet-general, isnet-anime, isnet-general-use, u2net...)",
    )
    segment.add_argument(
        "--matting",
        choices=("feather", "cf", "none"),
        default="feather",
        help="Refinado de alpha: feather, closed-form matting o ninguno",
    )
    segment.add_argument(
        "--feather", type=float, default=0.7, help="Sigma de feather en píxeles"
    )
    segment.add_argument(
        "--matting-max-dim",
        type=int,
        default=2048,
        help="Tamaño máximo para matting CF; arriba usa feather",
    )
    segment.add_argument(
        "--force-matting", action="store_true", help="Forzar matting CF en imágenes grandes"
    )
    segment.add_argument(
        "--despill", action="store_true", help="Revertir color del fondo en bordes"
    )
    segment.add_argument(
        "--alpha-threshold",
        type=int,
        default=8,
        help="Alpha mínimo para calcular el bounding box",
    )
    segment.add_argument(
        "--padding", type=int, default=8, help="Padding transparente alrededor del sujeto"
    )
    segment.add_argument(
        "--square", action="store_true", help="Completar el recorte a un cuadrado"
    )
    segment.add_argument(
        "--keep-largest",
        action="store_true",
        help="Conservar solo el componente alpha más grande (sujeto principal)",
    )
    segment.add_argument(
        "--min-component",
        type=float,
        default=0.0,
        help="Área mínima relativa al componente mayor (0-1); 0 conserva todo",
    )
    segment.add_argument(
        "--use-existing-alpha",
        action="store_true",
        help="Usar el alpha de entrada si existe",
    )
    segment.add_argument(
        "--ignore-existing-alpha",
        action="store_true",
        help="Ignorar el alpha de entrada y segmentar igual",
    )
    segment.add_argument(
        "--no-crop", action="store_true", help="No recortar al bounding box"
    )

    text = parser.add_argument_group("Texto, logos e inpainting")
    text.add_argument(
        "--ocr", dest="ocr", action="store_true", default=True, help="Detectar texto"
    )
    text.add_argument("--no-ocr", dest="ocr", action="store_false", help="No detectar texto")
    text.add_argument(
        "--ocr-min-score", type=float, default=0.5, help="Confianza mínima de OCR"
    )
    text.add_argument(
        "--ocr-scale",
        type=float,
        default=0.0,
        help="Escala para OCR; 0 = automática",
    )
    text.add_argument(
        "--erase-text",
        action="append",
        default=[],
        metavar="TEXTO",
        help="Borrar solo detecciones que contengan este texto (repetible)",
    )
    text.add_argument(
        "--keep-text",
        action="append",
        default=[],
        metavar="TEXTO",
        help="No borrar detecciones que contengan este texto (repetible)",
    )
    text.add_argument(
        "--erase-box",
        action="append",
        type=parse_box,
        default=[],
        metavar="x1,y1,x2,y2",
        help="Caja manual para borrar un logo u objeto (repetible)",
    )
    text.add_argument(
        "--mask", type=Path, default=None, help="Máscara manual (blanco = borrar)"
    )
    text.add_argument(
        "--mask-mode",
        choices=("auto", "kmeans", "border", "box"),
        default="auto",
        help="Cómo extraer el trazo dentro de cada caja",
    )
    text.add_argument(
        "--mask-threshold",
        type=float,
        default=28.0,
        help="Diferencia de color para detectar el trazo",
    )
    text.add_argument(
        "--text-color",
        type=hex_to_rgb,
        action="append",
        default=[],
        metavar="#RRGGBB",
        help="Color del texto/logo; repetible para varios colores",
    )
    text.add_argument(
        "--mask-expand", type=int, default=2, help="Dilatar la máscara de borrado"
    )
    text.add_argument(
        "--mask-feather", type=float, default=1.0, help="Suavizar la máscara de borrado"
    )
    text.add_argument(
        "--inpaint",
        choices=("auto", "lama", "opencv", "none"),
        default="auto",
        help="Backend de inpainting; auto intenta LaMa y cae a OpenCV",
    )
    text.add_argument(
        "--inpaint-radius", type=int, default=3, help="Radio de OpenCV Telea"
    )

    parser.add_argument("--quiet", action="store_true", help="Menos logs")
    parser.add_argument("--json-only", action="store_true", help="Imprimir solo JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_path: Path = args.input
    if not input_path.exists():
        parser.error(f"No existe la entrada: {input_path}")

    output_dir = args.output or default_output(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if input_path.is_dir() and output_dir.resolve() == input_path.resolve():
        parser.error("La salida no puede ser la misma carpeta que la entrada.")

    options = PipelineOptions(
        output=output_dir,
        model=args.model,
        matting=args.matting,
        feather=args.feather,
        matting_max_dim=args.matting_max_dim,
        force_matting=args.force_matting,
        despill=args.despill,
        alpha_threshold=args.alpha_threshold,
        padding=args.padding,
        square=args.square,
        keep_largest=args.keep_largest,
        min_component=args.min_component,
        use_existing_alpha=args.use_existing_alpha,
        ignore_existing_alpha=args.ignore_existing_alpha,
        ocr=args.ocr,
        ocr_min_score=args.ocr_min_score,
        ocr_scale=args.ocr_scale,
        erase_text=args.erase_text,
        keep_text=args.keep_text,
        erase_boxes=args.erase_box,

        mask_path=args.mask,
        mask_mode=args.mask_mode,
        mask_threshold=args.mask_threshold,
        text_colors=args.text_color,
        mask_expand=args.mask_expand,
        mask_feather=args.mask_feather,
        inpaint=args.inpaint,
        inpaint_radius=args.inpaint_radius,
        no_crop=args.no_crop,
        overwrite=args.overwrite,
        quiet=args.quiet or args.json_only,
    )

    if options.mask_path is not None and not options.mask_path.exists():
        parser.error(f"No existe la máscara: {options.mask_path}")

    images = list(iter_images(input_path, args.recursive, output_dir))
    if not images:
        parser.error(f"No encontré imágenes en {input_path}")

    session_cache: dict = {}
    lama_cache: dict = {}
    items: list[dict] = []
    failures: list[dict] = []
    is_batch = input_path.is_dir() or len(images) > 1

    for image_path in images:
        if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if input_path.is_dir():
            relative = image_path.relative_to(input_path)
            output_path = output_dir / relative.parent / f"{image_path.stem}_clean.png"
        else:
            output_path = output_dir / f"{image_path.stem}_clean.png"
        output_path = output_path if args.overwrite else unique_path(output_path)
        try:
            report = process_one(
                image_path, output_path, options, session_cache, lama_cache
            )
            items.append(report)
            if not args.json_only:
                log(
                    f"  Listo: {report['output']} "
                    f"({report['output_size'][0]}x{report['output_size'][1]})",
                    options.quiet,
                )
        except Exception as exc:
            failures.append({"input": str(image_path), "error": str(exc)})
            if not args.json_only:
                print(f"  ERROR: {image_path}: {exc}", file=sys.stderr)

    summary = {
        "processed": len(items),
        "failed": len(failures),
        "items": items,
        "failures": failures,
    }
    summary_path = unique_path(output_dir / "batch_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json_only:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        log("", options.quiet)
        log(f"Procesadas: {len(items)} | Fallidas: {len(failures)}", options.quiet)
        log(f"Resumen: {summary_path}", options.quiet)
        if failures:
            log("Revisá los errores en el resumen.", options.quiet)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
