"""What a command answers with: text, plus optional images (charts)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Image:
    data: bytes
    """PNG bytes."""
    filename: str
    caption: str
    """One short line shown under the image."""


@dataclass(frozen=True)
class Reply:
    text: str
    images: tuple[Image, ...] = ()
