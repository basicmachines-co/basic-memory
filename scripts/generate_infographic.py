#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openai>=1.100.2",
#   "python-dotenv>=1.1.0",
#   "typer>=0.9.0",
# ]
# ///
"""Generate a BM Bossbot infographic with the OpenAI Images API."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv
from openai import OpenAI


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1536x1024"
DEFAULT_QUALITY = "high"
DEFAULT_FORMAT = "webp"
DEFAULT_COMPRESSION = 90
app = typer.Typer(
    add_completion=False,
    help="Generate Basic Memory infographics with the OpenAI Images API.",
    no_args_is_help=True,
)


@dataclass(frozen=True)
class GeneratedImage:
    path: Path
    revised_prompt: str | None


def validate_output_path(path: Path, *, repo_root: Path | None = None) -> Path:
    root = (repo_root or Path.cwd()).resolve()
    output = path.resolve()
    allowed_root = (root / "docs" / "assets" / "infographics").resolve()
    if not output.is_relative_to(allowed_root):
        allowed_path = allowed_root.relative_to(root).as_posix()
        raise ValueError(f"Output path must be under {allowed_path}")
    if output.suffix != ".webp":
        raise ValueError("Output path must end with .webp")
    return output


def generate_image_result(
    *,
    prompt: str,
    output_path: Path,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
    quality: str = DEFAULT_QUALITY,
    output_format: str = DEFAULT_FORMAT,
    output_compression: int = DEFAULT_COMPRESSION,
    client: Any | None = None,
    retries: int = 2,
) -> GeneratedImage:
    output = validate_output_path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    load_dotenv()
    openai_client = client or OpenAI()

    for attempt in range(retries + 1):
        try:
            response = openai_client.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                quality=quality,
                output_format=output_format,
                output_compression=output_compression,
            )
            image = response.data[0]
            image_b64 = image.b64_json
            if not image_b64:
                raise RuntimeError("OpenAI image response did not include b64_json")
            output.write_bytes(base64.b64decode(image_b64))
            return GeneratedImage(path=output, revised_prompt=image.revised_prompt)
        except Exception:
            if attempt >= retries:
                raise
            time.sleep(2**attempt)

    raise RuntimeError("Image generation retry loop exited unexpectedly")


def generate_image(
    *,
    prompt: str,
    output_path: Path,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
    quality: str = DEFAULT_QUALITY,
    output_format: str = DEFAULT_FORMAT,
    output_compression: int = DEFAULT_COMPRESSION,
    client: Any | None = None,
    retries: int = 2,
) -> Path:
    return generate_image_result(
        prompt=prompt,
        output_path=output_path,
        model=model,
        size=size,
        quality=quality,
        output_format=output_format,
        output_compression=output_compression,
        client=client,
        retries=retries,
    ).path


@app.command()
def generate(
    prompt_file: Annotated[
        Path,
        typer.Option(
            "--prompt-file",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Markdown/text prompt file to send to the image model.",
        ),
    ],
    output: Annotated[Path, typer.Option("--output", help="Output .webp path.")],
    model: Annotated[str, typer.Option("--model", help="OpenAI image model.")] = DEFAULT_MODEL,
    size: Annotated[str, typer.Option("--size", help="Image size.")] = DEFAULT_SIZE,
    quality: Annotated[str, typer.Option("--quality", help="Image quality.")] = DEFAULT_QUALITY,
    output_compression: Annotated[
        int,
        typer.Option(
            "--output-compression",
            min=0,
            max=100,
            help="WebP output compression.",
        ),
    ] = DEFAULT_COMPRESSION,
    retries: Annotated[int, typer.Option("--retries", min=0, help="Retry attempts.")] = 2,
) -> None:
    """Generate an infographic from a prompt file."""
    output = generate_image(
        prompt=prompt_file.read_text(encoding="utf-8"),
        output_path=output,
        model=model,
        size=size,
        quality=quality,
        output_compression=output_compression,
        retries=retries,
    )
    typer.echo(output)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
