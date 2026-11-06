"""Turn connected images into what an APIYI endpoint accepts, and answers back into bytes."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from griptape.artifacts import ImageArtifact
from griptape_nodes.exe_types.core_types import NodeError, NodeErrorLink
from griptape_nodes.files.file import File, FileLoadError
from PIL import Image, ImageOps, UnidentifiedImageError

__all__ = [
    "ImageData",
    "decode_b64",
    "prepare_image",
    "prepare_mask",
    "public_url",
    "read_image",
    "without_image_data",
]

# Values from the APIYI upload guide: images above this size go through compression.
COMPRESS_ABOVE_BYTES = 1_500_000
MAX_LONG_EDGE = 2048
JPEG_QUALITY = 90

MASK_GUIDE_URL = "https://docs.apiyi.com/api-capabilities/gpt-image-2/mask-editing"

MIME_BY_FORMAT = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
EXTENSION_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# provider_response keeps strings up to this length. Longer ones are image data or model signatures.
MAX_KEPT_CHARS = 1000


@dataclass(frozen=True)
class ImageData:
    """Image bytes in a format the gateway accepts."""

    content: bytes
    mime_type: str

    @property
    def base64(self) -> str:
        return base64.b64encode(self.content).decode("ascii")

    @property
    def data_uri(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64}"

    def filename(self, index: int) -> str:
        return f"image_{index}.{EXTENSION_BY_MIME[self.mime_type]}"


def public_url(value: Any) -> str | None:
    """The URL of an image that APIYI can fetch by itself, or None."""
    location = _location(value)
    if location is None:
        return None
    parsed = urlparse(location)
    if parsed.scheme in ("http", "https") and parsed.hostname not in LOCAL_HOSTS:
        return location
    return None


async def read_image(value: Any, label: str) -> bytes:
    """Read the bytes of a connected image.

    Args:
        value: An image artifact, a serialized artifact, or a path, URL, or data URI.
        label: How to name this image in an error, such as "Input Images item 2".

    Raises:
        NodeError: The value has no image, or the file cannot be read.
    """
    if isinstance(value, ImageArtifact):
        return value.value
    if isinstance(value, dict) and value.get("type") == "ImageArtifact":
        return base64.b64decode(value["value"])

    location = _location(value)
    if location is None:
        raise NodeError(f"{label} has no image. Connect an image, or remove the item.")
    if location.startswith("data:"):
        return decode_b64(location)
    try:
        return await File(location).aread_bytes()
    except FileLoadError as e:
        raise NodeError(f"Could not read {label} from '{location}'. Connect a PNG, JPEG, or WEBP image.") from e


def prepare_image(content: bytes, *, compress: bool, formats: frozenset[str], label: str) -> ImageData:
    """Make image bytes ready to upload.

    Args:
        content: The image bytes.
        compress: Whether a large image goes through compression, as the APIYI upload guide advises.
        formats: The Pillow format names the endpoint accepts. Other formats are encoded again. This
            also fixes MPO files from phones, which look like JPEG and which the gateway refuses.
        label: How to name this image in an error.

    Raises:
        NodeError: The bytes are not an image.
    """
    try:
        with Image.open(BytesIO(content)) as source:
            image_format = source.format or ""
            shrink = compress and len(content) > COMPRESS_ABOVE_BYTES
            if image_format in formats and not shrink:
                return ImageData(content, MIME_BY_FORMAT[image_format])
            return _encode(source, shrink=shrink)
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise NodeError(f"{label} is not a readable image. Connect a PNG, JPEG, or WEBP image.") from e


def prepare_mask(content: bytes) -> ImageData:
    """Make a mask ready to upload. APIYI needs a PNG in which transparent pixels mark the edit area.

    Raises:
        NodeError: The bytes are not an image, or the image has no alpha channel.
    """
    try:
        with Image.open(BytesIO(content)) as source:
            if not _has_alpha(source):
                raise NodeError(
                    "'Mask' has no alpha channel. Connect a PNG with transparent pixels where the image must change.",
                    links=[NodeErrorLink(label="APIYI mask guide", url=MASK_GUIDE_URL)],
                )
            if source.format == "PNG":
                return ImageData(content, "image/png")
            return _encode(source, shrink=False)
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise NodeError("'Mask' is not a readable image. Connect a PNG with an alpha channel.") from e


def decode_b64(value: str) -> bytes:
    """Decode base64 image data. Some APIYI model lines add a `data:` prefix, so this removes it first."""
    if value.startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)


def without_image_data(value: Any) -> Any:
    """A copy of an answer with long strings replaced by their length, so it stays small in a workflow.

    This covers more than the image: a Gemini `thoughtSignature` can be 2.5 MB of base64.
    """
    if isinstance(value, dict):
        return {key: without_image_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [without_image_data(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_KEPT_CHARS:
        return f"[{len(value)} characters removed]"
    return value


def _location(value: Any) -> str | None:
    """The path, URL, or data URI that a connected value names, or None."""
    if isinstance(value, dict):
        value = value.get("value") or value.get("url")
    else:
        value = getattr(value, "value", value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or "transparency" in image.info


def _encode(source: Image.Image, *, shrink: bool) -> ImageData:
    # Encoding again removes the EXIF data, so the rotation in it must go into the pixels first.
    image = ImageOps.exif_transpose(source)
    if shrink:
        image.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
    buffer = BytesIO()
    if _has_alpha(image):
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        return ImageData(buffer.getvalue(), "image/png")
    image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return ImageData(buffer.getvalue(), "image/jpeg")
