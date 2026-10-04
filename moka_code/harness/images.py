"""Image attachments on user messages.

History stores a *reference* to each image (a JSON-safe dict: path, name,
mime, size, width, height); the bytes are base64-encoded only when a request
is built (:func:`api_content`), so history and ``/compact`` stay small.

Pasted images live in the cache (``~/.cache/moka/images/<hash>.<ext>``).
Dimensions come from the file headers (PNG, JPEG, GIF, WebP) — no Pillow.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import os
import re
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ImageRef = Dict[str, Any]

_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}

# ``[image #N]`` markers inserted by a clipboard paste.
MARKER_RE = re.compile(r"\[image #(\d+)\]")


def max_bytes() -> int:
    """The ``context.max_image_mb`` limit, in bytes."""
    from moka_code import settings

    return int(settings.config.context_max_image_mb * 1024 * 1024)


class ImageError(ValueError):
    """An image cannot be attached; the message says why."""


_private_dir: Optional[Path] = None


def set_private(on: bool) -> None:
    """Private mode keeps images in a temporary folder that is deleted when
    the mode ends (and at exit) instead of the cache."""
    global _private_dir
    if on and _private_dir is None:
        _private_dir = Path(tempfile.mkdtemp(prefix="moka-private-"))
        atexit.register(shutil.rmtree, _private_dir, True)
    elif not on and _private_dir is not None:
        shutil.rmtree(_private_dir, ignore_errors=True)
        _private_dir = None


def cache_dir() -> Path:
    if _private_dir is not None:
        return _private_dir
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "moka" / "images"


def probe(data: bytes) -> tuple[str, int, int]:
    """Return ``(mime, width, height)`` read from the image header."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return "image/gif", width, height
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8 ":
            width, height = struct.unpack("<HH", data[26:30])
            return "image/webp", width & 0x3FFF, height & 0x3FFF
        if chunk == b"VP8L":
            b0, b1, b2, b3 = data[21:25]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return "image/webp", width, height
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return "image/webp", width, height
    if data[:2] == b"\xff\xd8":
        return ("image/jpeg", *_jpeg_size(data))
    raise ImageError("not a PNG, JPEG, GIF or WebP image")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """Walk the JPEG segments up to the first start-of-frame marker."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    raise ImageError("JPEG has no frame header")


def _ref(path: Path, name: str, data: bytes) -> ImageRef:
    mime, width, height = probe(data)
    return {"path": str(path), "name": name, "mime": mime,
            "size": len(data), "width": width, "height": height}


def _check_size(size: int, max_bytes: int, name: str) -> None:
    if size > max_bytes:
        raise ImageError(
            f"{name} is {format_size(size)}, over the {format_size(max_bytes)} limit "
            "(context.max_image_mb)"
        )


def store(data: bytes, max_bytes: Optional[int], name: str = "clipboard") -> ImageRef:
    """Save image bytes (a paste) to the cache and reference them."""
    if max_bytes is not None:
        _check_size(len(data), max_bytes, name)
    mime, _, _ = probe(data)
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{hashlib.sha256(data).hexdigest()[:16]}.{_EXTENSIONS[mime]}"
    if not path.exists():
        path.write_bytes(data)
    return _ref(path, name, data)


def collect(text: str, pasted: Dict[int, ImageRef]) -> List[ImageRef]:
    """The pasted images a message attaches: those whose ``[image #N]`` marker
    is still in the text. Files on disk are never attached; the model reads
    them itself (``read``)."""
    images: List[ImageRef] = []
    for match in MARKER_RE.finditer(text):
        n = int(match.group(1))
        if n in pasted and all(image["n"] != n for image in images):
            images.append({**pasted[n], "n": n})
    return images


def format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size // 1024} KB"
    return f"{size} B"


def describe(image: ImageRef) -> str:
    """Transcript line: ``▣ image #N · name · W×H · size``."""
    return (f"▣ image #{image.get('n', '?')} · {image.get('name', '')} · "
            f"{image.get('width', '?')}×{image.get('height', '?')} · "
            f"{format_size(int(image.get('size', 0)))}")


def encode(image: ImageRef) -> Optional[str]:
    """Base64 of the referenced file, or ``None`` when it is gone."""
    try:
        return base64.b64encode(Path(image["path"]).read_bytes()).decode("ascii")
    except (OSError, KeyError):
        return None


def api_content(text: Optional[str], images: Iterable[ImageRef]) -> List[Dict[str, Any]]:
    """OpenAI content parts: the text, then each image as a data URL.

    An image whose file is gone becomes a text placeholder.
    """
    parts: List[Dict[str, Any]] = [{"type": "text", "text": text or ""}]
    for image in images:
        data = encode(image)
        if data is None:
            parts.append({"type": "text", "text": f"[image #{image.get('n', '?')} unavailable]"})
        else:
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{image['mime']};base64,{data}"}})
    return parts


def embed(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A copy of *history* whose image references carry their bytes (export)."""
    exported = []
    for entry in history:
        if entry.get("images"):
            entry = {**entry, "images": [
                {**image, "data": encode(image)} for image in entry["images"]
            ]}
        exported.append(entry)
    return exported


def restore(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Write embedded image bytes back to the cache and repoint the references
    (import). An image without bytes keeps its reference; if the file is gone
    too it is sent as an "unavailable" placeholder."""
    restored = []
    for entry in history:
        if entry.get("images"):
            images = []
            for image in entry["images"]:
                image = dict(image)
                data = image.pop("data", None)
                if data:
                    try:
                        stored = store(base64.b64decode(data), None, image.get("name", "image"))
                        image["path"] = stored["path"]
                    except (ImageError, ValueError, OSError):
                        pass
                images.append(image)
            entry = {**entry, "images": images}
        restored.append(entry)
    return restored


def available(image: ImageRef) -> bool:
    return os.path.isfile(image.get("path", ""))
