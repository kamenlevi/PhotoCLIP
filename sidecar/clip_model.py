"""Lazy-loaded open_clip model wrapper. Caches one instance per (name, pretrained, device)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import torch
from PIL import Image

import open_clip

from .paths import model_cache_dir

DEFAULT_MODEL = "ViT-B-32"
DEFAULT_PRETRAINED = "laion2b_s34b_b79k"


def pick_device(preferred: str | None = None) -> str:
    if preferred and preferred != "auto":
        return preferred
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@dataclass
class _ModelBundle:
    name: str
    pretrained: str
    device: str
    model: torch.nn.Module
    preprocess: Any
    tokenizer: Any
    dim: int


_cached: _ModelBundle | None = None


def get_model(
    name: str = DEFAULT_MODEL,
    pretrained: str = DEFAULT_PRETRAINED,
    device: str | None = None,
) -> _ModelBundle:
    global _cached
    dev = pick_device(device)
    if _cached and (_cached.name, _cached.pretrained, _cached.device) == (name, pretrained, dev):
        return _cached

    os.environ.setdefault("HF_HOME", str(model_cache_dir()))
    os.environ.setdefault("TORCH_HOME", str(model_cache_dir()))

    model, _, preprocess = open_clip.create_model_and_transforms(
        name,
        pretrained=pretrained,
        cache_dir=str(model_cache_dir()),
    )
    model = model.to(dev).eval()
    tokenizer = open_clip.get_tokenizer(name)

    with torch.no_grad():
        sample = tokenizer(["probe"]).to(dev)
        dim = int(model.encode_text(sample).shape[-1])

    _cached = _ModelBundle(name, pretrained, dev, model, preprocess, tokenizer, dim)
    return _cached


@torch.no_grad()
def encode_images(bundle: _ModelBundle, images: Iterable[Image.Image]) -> np.ndarray:
    batch = torch.stack([bundle.preprocess(im) for im in images]).to(bundle.device)
    feats = bundle.model.encode_image(batch)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().float().numpy()


@torch.no_grad()
def encode_text(bundle: _ModelBundle, text: str) -> np.ndarray:
    tokens = bundle.tokenizer([text]).to(bundle.device)
    feats = bundle.model.encode_text(tokens)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().float().numpy()[0]
