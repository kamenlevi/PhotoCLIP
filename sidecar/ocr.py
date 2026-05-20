"""Lightweight OCR via EasyOCR. Lazily loads the reader on first call."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import easyocr
    from PIL import Image

_reader: "easyocr.Reader | None" = None

CONFIDENCE_THRESHOLD = 0.3


def _get_reader() -> "easyocr.Reader":
    global _reader
    if _reader is None:
        import easyocr
        from .paths import model_cache_dir

        model_dir = str(model_cache_dir() / "easyocr")
        _reader = easyocr.Reader(
            ["en"],
            gpu=False,
            model_storage_directory=model_dir,
            verbose=False,
        )
    return _reader


def extract_text(img: "Image.Image") -> str:
    """Run OCR on a PIL image and return detected text concatenated with spaces.

    Returns "" on any failure or if no text is detected above the confidence
    threshold.
    """
    try:
        import numpy as np

        arr = np.array(img.convert("RGB"))
        reader = _get_reader()
        results = reader.readtext(arr)
        parts: list[str] = []
        for _bbox, text, confidence in results:
            if confidence >= CONFIDENCE_THRESHOLD and text.strip():
                parts.append(text.strip())
        return " ".join(parts)
    except Exception as e:
        sys.stderr.write(f"[ocr] failed: {e}\n")
        return ""
