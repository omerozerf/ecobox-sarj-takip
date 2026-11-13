"""Shared look of every chart, and the lazy matplotlib import.

matplotlib is optional: the recorder and every text command run without it. It is imported
only when a chart is drawn, and its absence surfaces as `ChartsUnavailable`.
"""

import io
from typing import Any

# Restrained palette: one accent for "free", one dark tone for "busy", greys for the rest.
FREE = "#2f9e6e"
BUSY = "#34405a"
UNUSABLE = "#c9ced6"
NO_DATA = "#f1f2f4"
INK = "#1d2330"
MUTED = "#6b7385"
GRID = "#e3e6eb"
BACKGROUND = "#ffffff"
HEAT_LOW = "#f3f8f5"
HEAT_HIGH = "#1f7a52"

DPI = 150
PORTRAIT = (7.2, 9.0)   # 1080 x 1350 px: fills a phone screen
SQUARE = (7.2, 7.2)     # 1080 x 1080 px

TITLE_SIZE = 19
SUBTITLE_SIZE = 11.5
LABEL_SIZE = 12
TICK_SIZE = 11


class ChartsUnavailable(RuntimeError):
    """matplotlib is not installed in the interpreter running the service."""


def pyplot() -> Any:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ChartsUnavailable(str(exc)) from None
    plt.rcParams.update({
        "font.family": "DejaVu Sans",  # bundled with matplotlib; covers Turkish letters
        "font.size": TICK_SIZE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK,
        "axes.titlecolor": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": BACKGROUND,
        "axes.facecolor": BACKGROUND,
        "hatch.color": "#d5d8de",
        "hatch.linewidth": 0.8,
    })
    return plt


def available() -> bool:
    try:
        pyplot()
    except ChartsUnavailable:
        return False
    return True


def figure(size: tuple[float, float], title: str, subtitle: str):
    """A figure with the title block on top; returns (plt, fig)."""
    plt = pyplot()
    fig = plt.figure(figsize=size, dpi=DPI)
    fig.text(0.06, 0.965, title, fontsize=TITLE_SIZE, fontweight="bold", color=INK,
             ha="left", va="top")
    fig.text(0.06, 0.925, subtitle, fontsize=SUBTITLE_SIZE, color=MUTED, ha="left", va="top",
             wrap=True)
    return plt, fig


def png(plt, fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, facecolor=BACKGROUND)
    plt.close(fig)
    return buffer.getvalue()


def placeholder(size: tuple[float, float], title: str, subtitle: str, message: str) -> bytes:
    """The chart's frame with a sentence in place of data, so an empty chart still says why."""
    plt, fig = figure(size, title, subtitle)
    fig.text(0.5, 0.5, message, fontsize=LABEL_SIZE + 2, color=MUTED, ha="center",
             va="center", wrap=True)
    return png(plt, fig)
