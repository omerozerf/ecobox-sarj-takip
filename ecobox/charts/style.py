"""Shared look of every chart, and the lazy matplotlib import.

matplotlib is optional: the recorder and every text command run without it. It is imported
only when a chart is drawn, and its absence surfaces as `ChartsUnavailable`.
"""

import io
from typing import Any

# Restrained palette: green for "free", dark blue for "busy", greys for the rest, and one
# orange accent for "you / the median" that stays apart from both in colour-blind vision.
FREE = "#2f9e6e"
BUSY = "#34405a"
ACCENT = "#d9822b"
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

# A phone shows the 1080 px image at about 360 px, so 1 pt is about 0.69 px there: nothing is
# drawn below MIN_SIZE (about 10 px on the phone).
TITLE_SIZE = 26
SUBTITLE_SIZE = 16
LABEL_SIZE = 18
TICK_SIZE = 16
LEGEND_SIZE = 16
MIN_SIZE = 15

MARGIN_X = 0.06
"""Left and right margin of the title block, as a share of the figure width."""
_TOP_IN = 0.32
_TITLE_GAP_IN = 0.1
_HEADER_GAP_IN = 0.3


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


def text_width(fig, text: str, size: float, weight: str = "normal") -> float:
    """Width of `text` in figure fractions once drawn at `size` points."""
    from matplotlib.font_manager import FontProperties
    renderer = fig.canvas.get_renderer()
    width, _, _ = renderer.get_text_width_height_descent(
        text, FontProperties(family="DejaVu Sans", size=size, weight=weight), ismath=False)
    return width / (fig.get_figwidth() * fig.dpi)


def wrap(fig, text: str, size: float, width: float, weight: str = "normal") -> list[str]:
    """Greedy word wrap of each line of `text` to `width` figure fractions."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if line and text_width(fig, candidate, size, weight) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def figure(size: tuple[float, float], title: str, subtitle: str):
    """A figure with the title block on top; returns (plt, fig, top).

    The title (the chart's takeaway) wraps onto as many lines as it needs, and so does the
    subtitle; `top` is the figure fraction below the block where the plot may start.
    """
    plt = pyplot()
    fig = plt.figure(figsize=size, dpi=DPI)
    width = 1 - 2 * MARGIN_X
    height_in = size[1]
    y_in = _TOP_IN
    for line in wrap(fig, title, TITLE_SIZE, width, "bold"):
        fig.text(MARGIN_X, 1 - y_in / height_in, line, fontsize=TITLE_SIZE, fontweight="bold",
                 color=INK, ha="left", va="top")
        y_in += TITLE_SIZE * 1.22 / 72
    y_in += _TITLE_GAP_IN
    for line in wrap(fig, subtitle, SUBTITLE_SIZE, width):
        fig.text(MARGIN_X, 1 - y_in / height_in, line, fontsize=SUBTITLE_SIZE, color=MUTED,
                 ha="left", va="top")
        y_in += SUBTITLE_SIZE * 1.3 / 72
    return plt, fig, 1 - (y_in + _HEADER_GAP_IN) / height_in


def png(plt, fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, facecolor=BACKGROUND)
    plt.close(fig)
    return buffer.getvalue()


def placeholder(size: tuple[float, float], title: str, subtitle: str, message: str) -> bytes:
    """The chart's frame with a sentence in place of data, so an empty chart still says why."""
    plt, fig, top = figure(size, title, subtitle)
    lines = wrap(fig, message, LABEL_SIZE + 2, 0.8)
    fig.text(0.5, top / 2, "\n".join(lines), fontsize=LABEL_SIZE + 2, color=MUTED,
             ha="center", va="center", linespacing=1.5)
    return png(plt, fig)
