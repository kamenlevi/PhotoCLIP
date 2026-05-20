"""Load any supported image and produce a 256px JPEG thumbnail."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

try:
    import rawpy
except ImportError:
    rawpy = None

from .paths import thumb_dir

THUMB_SIZE = 256
JPEG_QUALITY = 85

RAW_EXTS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".rw2", ".orf", ".raf"}
HEIC_EXTS = {".heic", ".heif"}
PIL_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SUPPORTED_EXTS = RAW_EXTS | HEIC_EXTS | PIL_EXTS


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


def load_image(path: Path) -> Image.Image:
    """Return an RGB PIL image regardless of source format."""
    ext = path.suffix.lower()
    if ext in RAW_EXTS:
        if rawpy is None:
            raise RuntimeError(f"rawpy not installed; cannot read {path}")
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        return Image.fromarray(rgb)
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def thumb_path_for(image_path: Path) -> Path:
    """Deterministic filesystem path for an image's thumbnail."""
    h = hashlib.sha1(str(image_path.resolve()).encode("utf-8")).hexdigest()
    return thumb_dir() / h[:2] / f"{h}.jpg"


def make_thumb(image_path: Path, img: Image.Image | None = None) -> Path:
    """Generate and save a 256px JPEG thumbnail. Returns the cache path."""
    out = thumb_path_for(image_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        return out
    if img is None:
        img = load_image(image_path)
    thumb = img.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
    thumb.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return out
