"""Load any supported image and produce a 256px JPEG thumbnail."""

from __future__ import annotations

import hashlib
import io
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


def _load_raw(path: Path) -> Image.Image:
    """Load a RAW file as RGB. Prefers the embedded JPEG preview over a
    full demosaic — CLIP only ever sees a 224x224 tensor, so demosaicing
    the full sensor is ~30x wasted work. Cameras embed a preview at
    something like 1920x1280 which is plenty for CLIP."""
    if rawpy is None:
        raise RuntimeError(f"rawpy not installed; cannot read {path}")
    with rawpy.imread(str(path)) as raw:
        try:
            thumb_data = raw.extract_thumb()
            fmt = getattr(thumb_data, "format", None)
            jpeg_fmt = getattr(rawpy.ThumbFormat, "JPEG", None)
            bitmap_fmt = getattr(rawpy.ThumbFormat, "BITMAP", None)
            if fmt is not None and fmt == jpeg_fmt:
                img = Image.open(io.BytesIO(thumb_data.data))
                img = ImageOps.exif_transpose(img)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return img
            if fmt is not None and fmt == bitmap_fmt:
                img = Image.fromarray(thumb_data.data)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return img
        except Exception:
            pass
        # No preview (or rawpy can't extract it). Fall back to half-size
        # demosaic — still 4x faster than full, with identical CLIP input.
        rgb = raw.postprocess(use_camera_wb=True, output_bps=8, half_size=True)
    return Image.fromarray(rgb)


def load_image(path: Path) -> Image.Image:
    """Return an RGB PIL image regardless of source format."""
    ext = path.suffix.lower()
    if ext in RAW_EXTS:
        return _load_raw(path)
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
