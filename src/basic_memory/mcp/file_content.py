"""Turn a file read from the resource API into something an MCP client can show (#1830).

`bm_read` and the legacy `read_content` tool both read non-note files: images, PDFs,
JSON, plain text. `classify_file_response` decides once what a response is, and each
tool presents the result its own way. Images are re-encoded as JPEG under the inline
size budget; other binaries go out as they are, up to that budget.
"""

import io
from dataclasses import dataclass

from httpx import Response
from loguru import logger
from PIL import Image as PILImage

# GIF is excluded until Pillow can be upgraded: GHSA-74r2-7ff9-mjj2 burns CPU
# inside Image.open(), before resizing or pixel-count limits can protect callers.
DISPLAYABLE_IMAGE_FORMATS = ("PNG", "JPEG", "WEBP")

# Inline budget for one file in a tool result. Images are shrunk to fit; other
# binaries above it are refused.
MAX_INLINE_BYTES = 350_000


# --- Image optimization ---


def calculate_target_params(content_length):
    """Calculate initial quality and size based on input file size"""
    target_size = 350000  # Reduced target for more safety margin
    ratio = content_length / target_size

    logger.debug(
        "Calculating target parameters",
        content_length=content_length,
        ratio=ratio,
        target_size=target_size,
    )

    if ratio > 4:
        # Very large images - start very aggressive
        return 50, 600  # Lower initial quality and size
    elif ratio > 2:
        return 60, 800
    else:
        return 70, 1000


def resize_image(img, max_size):
    """Resize image maintaining aspect ratio"""
    original_dimensions = {"width": img.width, "height": img.height}

    if img.width > max_size or img.height > max_size:
        ratio = min(max_size / img.width, max_size / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        logger.debug("Resizing image", original=original_dimensions, target=new_size, ratio=ratio)
        return img.resize(new_size, PILImage.Resampling.LANCZOS)

    logger.debug("No resize needed", dimensions=original_dimensions)
    return img


def optimize_image(img, content_length, max_output_bytes=350000):
    """Iteratively optimize image with aggressive size reduction"""
    stats = {
        "dimensions": {"width": img.width, "height": img.height},
        "mode": img.mode,
        "estimated_memory": (img.width * img.height * len(img.getbands())),
    }

    initial_quality, initial_size = calculate_target_params(content_length)

    logger.debug(
        "Starting optimization",
        image_stats=stats,
        content_length=content_length,
        initial_quality=initial_quality,
        initial_size=initial_size,
        max_output_bytes=max_output_bytes,
    )

    quality = initial_quality
    size = initial_size

    # Convert to RGB if needed
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGB")
        logger.debug("Converted to RGB mode")

    iteration = 0
    min_size = 300  # Absolute minimum size
    min_quality = 20  # Absolute minimum quality

    while True:
        iteration += 1
        buf = io.BytesIO()
        resized = resize_image(img, size)

        resized.save(
            buf,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )

        output_size = buf.getbuffer().nbytes
        reduction_ratio = output_size / content_length

        logger.debug(
            "Optimization attempt",
            iteration=iteration,
            quality=quality,
            size=size,
            output_bytes=output_size,
            target_bytes=max_output_bytes,
            reduction_ratio=f"{reduction_ratio:.2f}",
        )

        if output_size < max_output_bytes:
            logger.info(
                "Image optimization complete",
                final_size=output_size,
                quality=quality,
                dimensions={"width": resized.width, "height": resized.height},
                reduction_ratio=f"{reduction_ratio:.2f}",
            )
            return buf.getvalue()

        # Very aggressive reduction for large files
        if content_length > 2000000:  # 2MB+   # pragma: no cover
            quality = max(min_quality, quality - 20)
            size = max(min_size, int(size * 0.6))
        elif content_length > 1000000:  # 1MB+ # pragma: no cover
            quality = max(min_quality, quality - 15)
            size = max(min_size, int(size * 0.7))
        else:
            quality = max(min_quality, quality - 10)  # pragma: no cover
            size = max(min_size, int(size * 0.8))  # pragma: no cover

        logger.debug("Reducing parameters", new_quality=quality, new_size=size)  # pragma: no cover

        # If we've hit minimum values and still too big
        if quality <= min_quality and size <= min_size:  # pragma: no cover
            logger.warning(
                "Reached minimum parameters",
                final_size=output_size,
                over_limit_by=output_size - max_output_bytes,
            )
            return buf.getvalue()


# --- Classification ---


@dataclass(frozen=True, slots=True)
class TextFile:
    text: str
    content_type: str


@dataclass(frozen=True, slots=True)
class ImageFile:
    """An image re-encoded as JPEG within the inline budget."""

    jpeg: bytes


@dataclass(frozen=True, slots=True)
class DocumentFile:
    data: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class UnsupportedImage:
    """An image in a format we do not decode (see DISPLAYABLE_IMAGE_FORMATS)."""


@dataclass(frozen=True, slots=True)
class DocumentTooLarge:
    size: int


type FileContent = TextFile | ImageFile | DocumentFile | UnsupportedImage | DocumentTooLarge


def classify_file_response(response: Response) -> FileContent:
    """Decide what a resource response is and prepare it for inline display."""
    content_type = response.headers.get("content-type", "application/octet-stream")
    content_length = int(response.headers.get("content-length", 0))

    if content_type.startswith("text/") or content_type == "application/json":
        return TextFile(text=response.text, content_type=content_type)

    if content_type.startswith("image/"):
        # The content type comes from the stored file, which whoever wrote the
        # file chose, and Pillow picks a decoder by sniffing the bytes. Decoding
        # only the formats we display keeps a crafted TIFF, EPS or PDF named
        # .png away from Pillow's other parsers, which carry known memory-safety
        # bugs we cannot upgrade past yet (fastembed pins pillow<13).
        try:
            image = PILImage.open(io.BytesIO(response.content), formats=DISPLAYABLE_IMAGE_FORMATS)
        except PILImage.UnidentifiedImageError:
            return UnsupportedImage()
        return ImageFile(jpeg=optimize_image(image, content_length))

    if content_length > MAX_INLINE_BYTES:  # pragma: no cover
        return DocumentTooLarge(size=content_length)
    return DocumentFile(data=response.content, content_type=content_type)
