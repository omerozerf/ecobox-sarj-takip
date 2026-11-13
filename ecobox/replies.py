"""What a command answers with: text, plus optional images (charts)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Image:
    data: bytes
    """PNG bytes."""
    filename: str
    caption: str
    """Shown under the image: the chart's takeaway, at most a few short lines."""


@dataclass(frozen=True)
class Reply:
    text: str
    """Empty for a reply that is its images alone (charts)."""
    images: tuple[Image, ...] = ()
