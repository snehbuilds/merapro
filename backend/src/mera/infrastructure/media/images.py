"""Image checks and per-platform renditions (M5.14; docs/01 §5.4: deterministic code, not
provider calls).

Every byte that arrives is untrusted. ``probe`` opens the file with only the JPEG, PNG and
WebP decoders enabled (no other Pillow plugin ever sees it), refuses pixel counts that
would make decoding a memory bomb *before* any pixel is decoded, refuses animation, and then
does a cheap trial decode so a truncated file is refused at upload rather than failing
later.

``derive`` crops to the platform's aspect around the centre, scales down (never up), and
re-encodes as JPEG with no metadata: EXIF (camera, GPS location) does not survive into
anything that is published. If the result is over the platform's byte limit it steps
quality down, then size.

Both are CPU- and memory-bound and synchronous; callers run them in a worker thread. A
process-wide semaphore bounds how many full decodes run at once (security review S7-M3).
"""

from __future__ import annotations

import io
import threading
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 25_000_000  # ~ 6100 x 4100; a decode costs ~75 MB of RGB
MIN_SIDE = 200
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_DECODERS = tuple(FORMATS)
# At most this many full-size decodes at once in this process.
_DECODE_SLOTS = threading.BoundedSemaphore(3)

Image.MAX_IMAGE_PIXELS = MAX_PIXELS

_DECODE_ERRORS = (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError)


class ImageRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ImageInfo:
    content_type: str
    width: int
    height: int


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data), formats=_DECODERS)


def probe(data: bytes) -> ImageInfo:
    if not data:
        raise ImageRejected("empty_file", "The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejected("file_too_large", "Images can be at most 20 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with _open(data) as img:
                fmt = img.format or ""
                if fmt not in FORMATS:
                    raise ImageRejected(
                        "unsupported_media_type", "Upload a JPEG, PNG or WebP image."
                    )
                width, height = img.size
                if width * height > MAX_PIXELS:
                    raise ImageRejected("image_too_large", "The image has too many pixels.")
                # Only now, with the format and size known to be safe, count frames.
                if getattr(img, "n_frames", 1) > 1:
                    raise ImageRejected(
                        "animated_image", "Animated images are not supported yet; upload a still."
                    )
                img.verify()  # structure check without decoding pixels
            # A trial decode (JPEG at reduced scale, which is cheap) catches truncated and
            # corrupt files that verify() lets through.
            with _DECODE_SLOTS, _open(data) as img:
                if img.format == "JPEG":
                    img.draft("RGB", (max(1, width // 8), max(1, height // 8)))
                img.load()
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageRejected("image_too_large", "The image has too many pixels.") from exc
    except UnidentifiedImageError as exc:
        # Not one of the three formats whose decoders are enabled (or not an image at all).
        raise ImageRejected("unsupported_media_type", "Upload a JPEG, PNG or WebP image.") from exc
    except _DECODE_ERRORS as exc:
        raise ImageRejected("unreadable_image", "That file is not a readable image.") from exc
    if min(width, height) < MIN_SIDE:
        raise ImageRejected("image_too_small", f"Images need at least {MIN_SIDE}px on each side.")
    return ImageInfo(FORMATS[fmt], width, height)


@dataclass(frozen=True, slots=True)
class Rendition:
    data: bytes
    width: int
    height: int
    content_type: str = "image/jpeg"


def _crop_box(w: int, h: int, aw: int, ah: int) -> tuple[int, int, int, int]:
    """The largest centred box of aspect aw:ah inside w x h."""
    if w * ah > h * aw:  # too wide: trim the sides
        cw, ch = round(h * aw / ah), h
    else:  # too tall: trim top and bottom
        cw, ch = w, round(w * ah / aw)
    left, top = (w - cw) // 2, (h - ch) // 2
    return left, top, left + cw, top + ch


def derive(data: bytes, width: int, height: int, max_bytes: int) -> Rendition:
    """Crop to ``width:height``, scale down to at most that size, encode under ``max_bytes``."""
    probe(data)  # never decode what probe would refuse
    try:
        with _DECODE_SLOTS:
            return _derive(data, width, height, max_bytes)
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, *_DECODE_ERRORS) as exc:
        raise ImageRejected("unreadable_image", "That file is not a readable image.") from exc


def _derive(data: bytes, width: int, height: int, max_bytes: int) -> Rendition:
    with _open(data) as src:
        if src.format == "JPEG":
            # Decode at the smallest DCT scale still at least twice the target: far less
            # memory for big photos, no visible loss after the resize.
            side = max(width, height) * 2  # square: EXIF rotation may swap the axes
            src.draft("RGB", (side, side))
        img = ImageOps.exif_transpose(src)
        img.load()
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")
    box = _crop_box(img.width, img.height, width, height)
    cropped = img.crop(box)
    del img
    scale = min(1.0, width / cropped.width)
    size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    out = cropped.resize(size, Image.Resampling.LANCZOS) if size != cropped.size else cropped
    for _ in range(6):
        for quality in (88, 80, 72, 64):
            buf = io.BytesIO()
            # No exif=, no icc_profile=: nothing from the original's metadata goes out.
            out.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
            if buf.tell() <= max_bytes:
                return Rendition(buf.getvalue(), out.width, out.height)
        out = out.resize(
            (max(1, round(out.width * 0.85)), max(1, round(out.height * 0.85))),
            Image.Resampling.LANCZOS,
        )
    raise ImageRejected("image_too_complex", "The image could not be made small enough.")
