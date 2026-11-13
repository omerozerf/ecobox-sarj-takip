"""Drawing: plain data in, PNG bytes out. One clear message per chart, Turkish labels.

The title is the takeaway and is passed in; every text is at least `style.MIN_SIZE` points so
it stays legible on a phone. Every function draws a placeholder with a sentence instead of an empty plot when it has no
data, so the reply still explains itself.
"""

from dataclasses import dataclass
from datetime import datetime

from ecobox.charts import style
from ecobox.clock import LOCAL_TZ
from ecobox.fmt import minutes_text
from ecobox.state import AVAILABLE, CHARGING
from ecobox.stats import FreeGrid, TimeDistribution

WEEKDAYS_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
NO_DATA_TEXT = "Henüz kayıt yok.\nKayıt sürdükçe grafik dolacak."

# Occupancy-time bins in minutes; the last one is open-ended.
OCCUPANCY_BINS = ((0, 30, "<30 dk"), (30, 60, "30–60 dk"), (60, 120, "1–2 sa"),
                  (120, 180, "2–3 sa"), (180, 240, "3–4 sa"), (240, 360, "4–6 sa"),
                  (360, 480, "6–8 sa"), (480, None, "8+ sa"))


@dataclass(frozen=True)
class Segment:
    start: datetime
    end: datetime
    state: str


@dataclass(frozen=True)
class TimelineRow:
    name: str
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class SiteBar:
    name: str
    busy_share: float
    home: bool = False
    average: bool = False
    """The pooled "other sites" bar under the ranked ones."""


class MatplotlibRenderer:
    """The default renderer; `ChartBook` takes any object with these methods."""

    def notice(self, title: str, subtitle: str, message: str) -> bytes:
        """A portrait frame with a sentence: a chart whose data threshold is not met yet."""
        return style.placeholder(style.PORTRAIT, title, subtitle, message)

    def heatmap(self, grid: FreeGrid, title: str, subtitle: str) -> bytes:
        if not grid.cells:
            return style.placeholder(style.PORTRAIT, title, subtitle, NO_DATA_TEXT)
        plt, fig, top = style.figure(style.PORTRAIT, title, subtitle)
        from matplotlib.colors import LinearSegmentedColormap
        from matplotlib.patches import Rectangle
        cmap = LinearSegmentedColormap.from_list("free", [style.HEAT_LOW, style.HEAT_HIGH])
        first, last = heatmap_hours(grid)
        hours = range(first, last + 1)
        tick_band = 0.045  # weekday names sit above the cells
        ax = fig.add_axes((0.15, 0.03, 0.81, top - 0.03 - tick_band))
        for weekday in range(7):
            for hour in hours:
                p = grid.cell(weekday, hour).probability
                face = style.NO_DATA if p is None else cmap(p)
                ax.add_patch(Rectangle((weekday, hour), 1, 1, facecolor=face,
                                       edgecolor=style.BACKGROUND, linewidth=1.5,
                                       hatch=None if p is not None else "////"))
                if p is not None:
                    ax.text(weekday + 0.5, hour + 0.5, f"{round(100 * p)}", ha="center",
                            va="center", fontsize=style.MIN_SIZE, fontweight="bold",
                            color="white" if p > 0.55 else style.INK)
        ax.set_xlim(0, 7)
        ax.set_ylim(last + 1, first)
        ax.set_xticks([d + 0.5 for d in range(7)], WEEKDAYS_SHORT, fontsize=style.LABEL_SIZE)
        ax.xaxis.tick_top()
        step = 1 if len(hours) <= 14 else 2
        labelled = [h for h in hours if (h - first) % step == 0]
        ax.set_yticks([h + 0.5 for h in labelled], [f"{h:02d}:00" for h in labelled],
                      fontsize=style.TICK_SIZE)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        return style.png(plt, fig)

    def arrivals(self, plug_in: TimeDistribution, unplug: TimeDistribution, title: str,
                 subtitle: str) -> bytes:
        if not plug_in.count and not unplug.count:
            return style.placeholder(style.SQUARE, title, subtitle, NO_DATA_TEXT)
        plt, fig, top = style.figure(style.SQUARE, title, subtitle)
        ax = fig.add_axes((0.11, 0.13, 0.84, top - 0.15))
        ax.yaxis.set_major_locator(_integer_ticks())
        hours = list(range(24))
        # Plugging in fills a socket (busy blue); unplugging frees it (free green).
        ax.bar([h + 0.5 for h in hours], plug_in.by_hour, width=0.8, color=style.BUSY)
        ax.bar([h + 0.5 for h in hours], [-n for n in unplug.by_hour], width=0.8,
               color=style.FREE)
        ax.axhline(0, color=style.MUTED, linewidth=0.8)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 3), [f"{h:02d}" for h in range(0, 25, 3)])
        peak = max(max(plug_in.by_hour), max(unplug.by_hour), 1)
        ax.set_ylim(-peak * 1.3, peak * 1.3)
        ax.yaxis.set_major_formatter(_absolute_ticks())
        ax.grid(axis="y", color=style.GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_xlabel("Saat", fontsize=style.LABEL_SIZE)
        ax.text(0.3, peak * 1.25, "▲ takma", color=style.BUSY, fontsize=style.LABEL_SIZE,
                fontweight="bold", va="top")
        ax.text(0.3, -peak * 1.25, "▼ çıkarma", color=style.FREE, fontsize=style.LABEL_SIZE,
                fontweight="bold", va="bottom")
        return style.png(plt, fig)

    def timeline(self, rows: list[TimelineRow], start: datetime, end: datetime, title: str,
                 subtitle: str) -> bytes:
        if not any(row.segments for row in rows):
            return style.placeholder(style.PORTRAIT, title, subtitle, NO_DATA_TEXT)
        plt, fig, top = style.figure(style.PORTRAIT, title, subtitle)
        import matplotlib.dates as mdates
        left = max(style.text_width(fig, row.name, style.LABEL_SIZE) for row in rows) + 0.06
        legend_band, tick_band, now_band = 0.07, 0.07, 0.035
        ax = fig.add_axes((left, legend_band + tick_band, 0.95 - left,
                           top - legend_band - tick_band - now_band))
        x0, x1 = mdates.date2num(start), mdates.date2num(end)
        colours = {AVAILABLE: style.FREE, CHARGING: style.BUSY}
        for index, row in enumerate(rows):
            # Unrecorded time stays visible as the "kayıt yok" band behind the segments.
            ax.broken_barh([(x0, x1 - x0)], (index + 0.15, 0.7), facecolors=style.NO_DATA,
                           linewidth=0)
            for state in {s.state for s in row.segments}:
                spans = [(mdates.date2num(s.start), mdates.date2num(s.end) -
                          mdates.date2num(s.start)) for s in row.segments if s.state == state]
                ax.broken_barh(spans, (index + 0.15, 0.7),
                               facecolors=colours.get(state, style.UNUSABLE),
                               hatch=None if state in colours else "////", linewidth=0)
        ax.set_ylim(len(rows), 0)
        ax.set_yticks([i + 0.5 for i in range(len(rows))], [row.name for row in rows],
                      fontsize=style.LABEL_SIZE)
        ax.set_xlim(x0, x1)
        _time_axis(ax, (end - start).total_seconds() / 86400)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.axvline(x1, color=style.INK, linewidth=1.5)
        ax.text(x1, -0.15, "şimdi", ha="right", va="bottom", fontsize=style.MIN_SIZE,
                color=style.INK)
        _legend(fig, [(style.FREE, "boş"), (style.BUSY, "dolu"),
                      (style.UNUSABLE, "kullanılamaz"), (style.NO_DATA, "kayıt yok")])
        return style.png(plt, fig)

    def occupancy(self, minutes: list[float], median: float | None, title: str,
                  subtitle: str) -> bytes:
        if not minutes:
            return style.placeholder(style.SQUARE, title, subtitle,
                                     "Henüz ölçülen araç yok.\nKayıt sürdükçe grafik dolacak.")
        plt, fig, top = style.figure(style.SQUARE, title, subtitle)
        ax = fig.add_axes((0.13, 0.19, 0.83, top - 0.21))
        ax.yaxis.set_major_locator(_integer_ticks())
        counts = [sum(low <= m and (high is None or m < high) for m in minutes)
                  for low, high, _ in OCCUPANCY_BINS]
        bars = ax.bar(range(len(counts)), counts, color=style.BUSY, width=0.75)
        lift = max(counts) * 0.025  # clear of the bar top, so the white box never cuts it
        for rect, n in zip(bars, counts):
            if n:
                ax.text(rect.get_x() + rect.get_width() / 2, n + lift, str(n), ha="center",
                        va="bottom", fontsize=style.TICK_SIZE, color=style.INK, zorder=3,
                        bbox={"facecolor": style.BACKGROUND, "edgecolor": "none", "pad": 1})
        ax.set_xticks(range(len(counts)), [label for _, _, label in OCCUPANCY_BINS],
                      rotation=40, ha="right", rotation_mode="anchor")
        ax.set_ylabel("Araç sayısı", fontsize=style.LABEL_SIZE)
        ax.grid(axis="y", color=style.GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_xlim(-0.6, len(counts) - 0.4)
        ax.set_ylim(0, max(counts) * 1.32)
        if median is not None:
            x = median_position(median)
            ax.axvline(x, color=style.ACCENT, linestyle="--", linewidth=2.2)
            right = x < len(counts) / 2
            ax.text(x + (0.12 if right else -0.12), max(counts) * 1.27,
                    f"ortanca {minutes_text(median)}", color=style.ACCENT,
                    fontsize=style.TICK_SIZE, fontweight="bold", va="top",
                    ha="left" if right else "right")
        return style.png(plt, fig)

    def sites(self, bars: list[SiteBar], others_share: float | None, title: str,
              subtitle: str) -> bytes:
        if not bars:
            return style.placeholder(style.PORTRAIT, title, subtitle, NO_DATA_TEXT)
        plt, fig, top = style.figure(style.PORTRAIT, title, subtitle)
        names = [_short(bar.name) for bar in bars]
        left = max(style.text_width(fig, n, style.TICK_SIZE) for n in names) + 0.07
        line_band, tick_band = 0.045, 0.06
        ax = fig.add_axes((left, tick_band, 0.93 - left, top - tick_band - line_band))
        shares = [100 * bar.busy_share for bar in bars]
        colours = [style.ACCENT if bar.home else style.MUTED if bar.average else style.UNUSABLE
                   for bar in bars]
        ax.barh(range(len(bars)), shares, color=colours, height=0.68)
        for i, (bar, share) in enumerate(zip(bars, shares)):
            _bar_value(ax, i, share, bar.home or bar.average, bar.home)
        ax.set_yticks(range(len(bars)), names, fontsize=style.TICK_SIZE)
        for label, bar in zip(ax.get_yticklabels(), bars):
            if bar.home:
                label.set_color(style.INK)
                label.set_fontweight("bold")
        ax.set_ylim(len(bars) - 0.5, -0.5)
        ax.set_xlim(0, 118)
        ax.set_xticks([0, 50, 100], ["%0", "%50", "%100"])
        if others_share is not None:
            x = 100 * others_share
            ax.axvline(x, color=style.MUTED, linestyle="--", linewidth=1.4)
            ax.text(x + 1.5, -0.55, "diğer siteler", fontsize=style.MIN_SIZE,
                    color=style.MUTED, va="bottom", ha="left")
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.grid(axis="x", color=style.GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        return style.png(plt, fig)


def _bar_value(ax, row: int, share: float, dark_bar: bool, bold: bool) -> None:
    """"%49" inside the end of a long bar (clear of the average line), else just after it."""
    inside = share >= 18
    ax.text(share - 1.5 if inside else share + 1.5, row, f"%{round(share)}", va="center",
            ha="right" if inside else "left", fontsize=style.TICK_SIZE,
            color="white" if inside and dark_bar else style.INK,
            fontweight="bold" if bold else "normal", zorder=3)


def heatmap_hours(grid: FreeGrid) -> tuple[int, int]:
    """First and last hour row to draw: the earliest and latest hour any day has data, one
    hour of margin either side, so empty night rows do not shrink the cells."""
    hours = [hour for (_, hour), cell in grid.cells.items() if cell.total]
    return max(0, min(hours) - 1), min(23, max(hours) + 1)


def median_position(median: float) -> float:
    """The median's x on the bar axis: inside its bin, in proportion to where it falls."""
    for index, (low, high, _) in enumerate(OCCUPANCY_BINS):
        if high is None:
            return index
        if low <= median < high:
            return index - 0.5 + (median - low) / (high - low)
    raise AssertionError("the last bin is open-ended")


def _time_axis(ax, days: float) -> None:
    """Hours for a day; for a week, the grid at midnight and the weekday centred at noon."""
    import matplotlib.dates as mdates
    if days <= 1.5:
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 6), tz=LOCAL_TZ))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=LOCAL_TZ))
        ax.grid(axis="x", color=style.GRID, linewidth=0.8)
        return
    ax.xaxis.set_major_locator(mdates.DayLocator(tz=LOCAL_TZ))
    ax.xaxis.set_major_formatter(_blank())
    ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[12], tz=LOCAL_TZ))
    ax.xaxis.set_minor_formatter(_weekday_formatter())
    ax.tick_params(axis="x", which="minor", length=0, labelsize=style.TICK_SIZE)
    ax.grid(axis="x", which="major", color=style.GRID, linewidth=0.8)


def _integer_ticks():
    from matplotlib.ticker import MaxNLocator
    return MaxNLocator(integer=True)


def _absolute_ticks():
    from matplotlib.ticker import FuncFormatter
    return FuncFormatter(lambda value, _: f"{abs(int(value))}")


def _blank():
    from matplotlib.ticker import NullFormatter
    return NullFormatter()


def _weekday_formatter():
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    def label(value, _):
        moment = mdates.num2date(value, tz=LOCAL_TZ)
        return f"{WEEKDAYS_SHORT[moment.weekday()]}\n{moment.day}"
    return FuncFormatter(label)


def _legend(fig, items: list[tuple[str, str]]) -> None:
    """Swatches along the bottom edge, the same gap between every item, centred."""
    swatch, label_gap, gap = 0.024, 0.01, 0.045
    widths = [swatch + label_gap + style.text_width(fig, label, style.LEGEND_SIZE)
              for _, label in items]
    x = (1 - sum(widths) - gap * (len(items) - 1)) / 2
    for (colour, label), width in zip(items, widths):
        fig.patches.append(_swatch(fig, x, colour))
        fig.text(x + swatch + label_gap, 0.03, label, fontsize=style.LEGEND_SIZE,
                 color=style.MUTED, va="center")
        x += width + gap


def _swatch(fig, x: float, colour: str):
    from matplotlib.patches import Rectangle
    return Rectangle((x, 0.019), 0.024, 0.022, transform=fig.transFigure, facecolor=colour,
                     edgecolor=style.MUTED, linewidth=0.6, figure=fig)


def _short(name: str, limit: int = 24) -> str:
    return name if len(name) <= limit else name[:limit - 1] + "…"
