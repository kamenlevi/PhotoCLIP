"""EXIF extraction: capture date, camera make/model, GPS."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import exifread


@dataclass
class Exif:
    taken_at: str | None = None
    camera: str | None = None
    lat: float | None = None
    lon: float | None = None


def _ratio_to_float(value: Any) -> float | None:
    try:
        if hasattr(value, "num") and hasattr(value, "den"):
            return float(value.num) / float(value.den) if value.den else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms_to_decimal(dms: Any, ref: str | None) -> float | None:
    try:
        d, m, s = (_ratio_to_float(v) for v in dms.values)
    except (AttributeError, ValueError, TypeError):
        return None
    if None in (d, m, s):
        return None
    val = d + m / 60.0 + s / 3600.0
    if ref and ref.upper().startswith(("S", "W")):
        val = -val
    return val


def _normalize_datetime(raw: str | None) -> str | None:
    if not raw:
        return None
    # EXIF format: "2024:01:15 14:32:10" -> ISO "2024-01-15T14:32:10"
    raw = raw.strip()
    if len(raw) >= 19 and raw[4] == ":" and raw[7] == ":":
        return raw[:10].replace(":", "-") + "T" + raw[11:19]
    return raw


def read_exif(path: Path) -> Exif:
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:
        return Exif()

    if not tags:
        return Exif()

    date = (
        tags.get("EXIF DateTimeOriginal")
        or tags.get("EXIF DateTimeDigitized")
        or tags.get("Image DateTime")
    )
    make = tags.get("Image Make")
    model = tags.get("Image Model")
    camera_parts = [str(make).strip() for make in (make, model) if make]
    camera = " ".join(camera_parts) or None

    lat = _dms_to_decimal(tags.get("GPS GPSLatitude"), str(tags.get("GPS GPSLatitudeRef") or ""))
    lon = _dms_to_decimal(tags.get("GPS GPSLongitude"), str(tags.get("GPS GPSLongitudeRef") or ""))

    return Exif(
        taken_at=_normalize_datetime(str(date) if date else None),
        camera=camera,
        lat=lat,
        lon=lon,
    )
