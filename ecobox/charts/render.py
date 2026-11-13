"""Drawing: plain data in, PNG bytes out. One clear message per chart, Turkish labels.

Every function draws a placeholder with a sentence instead of an empty plot when it has no
data, so the reply still explains itself.
"""

from dataclasses import dataclass
from datetime import datetime

from ecobox.charts import style
from ecobox.clock import LOCAL_TZ
from ecobox.state import AVAILABLE, CHARGING
from ecobox.stats import FreeGrid, SiteLoad, TimeDistribution

WEEKDAYS_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
NO_DATA_TEXT = "Henüz veri yok.\nKayıt sürdükçe grafik dolacak."

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


class MatplotlibRenderer:
    """The default renderer; `ChartBook` takes any object with these methods."""

    def heatmap(self, grid: FreeGrid, title: str, subtitle: str) -> bytes:
        if not grid.cells:
            return style.placeholder(style.PORTRAIT, title, subtitle, NO_DATA_TEXT)
        plt, fig = style.figure(style.PORTRAIT, title, subtitle)
        from matplotlib.colors import LinearSegmentedColormap
        from matplotlib.patches import Rectangle
        cmap = LinearSegmentedColormap.from_list("free", [style.HEAT_LOW, style.HEAT_HIGH])
        ax = fig.add_axes((0.13, 0.06, 0.82, 0.775))
        for weekday in range(7):
            for hour in range(24):
                cell = grid.cell(weekday, hour)
                p = cell.probability
                face = style.NO_DATA if p is None else cmap(p)
                ax.add_patch(Rectangle((weekday, hour), 1, 1, facecolor=face,
                                       edgecolor=style.BACKGROUND, linewidth=1.5,
                                       hatch=None if p is not None else "////"))
                if p is not None:
                    ax.text(weekday + 0.5, hour + 0.5, f"{round(100 * p)}", ha="center",
                            va="center", fontsize=8.5,
                            color="white" if p > 0.55 else style.INK)
        ax.set_xlim(0, 7)
        ax.set_ylim(24, 0)
        ax.set_xticks([d + 0.5 for d in range(7)], WEEKDAYS_SHORT, fontsize=style.LABEL_SIZE)
        ax.xaxis.tick_top()
        ax.set_yticks([h + 0.5 for h in range(0, 24, 2)],
                      [f"{h:02d}:00" for h in range(0, 24, 2)])
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.text(0.13, 0.025, "Hücredeki sayı: o saatte en az bir AC prizin boş olma "
                              "yüzdesi. Taralı: gözlem yok.", fontsize=9, color=style.MUTED)
        return style.png(plt, fig)

    def arrivals(self, plug_in: TimeDistribution, unplug: TimeDistribution, title: str,
                 subtitle: str) -> bytes:
        if not plug_in.count and not unplug.count:
            return style.placeholder(style.SQUARE, title, subtitle, NO_DATA_TEXT)
        plt, fig = style.figure(style.SQUARE, title, subtitle)
        ax = fig.add_axes((0.11, 0.1, 0.84, 0.72))
        ax.yaxis.set_major_locator(_integer_ticks())
        hours = list(range(24))
        ax.bar([h + 0.5 for h in hours], plug_in.by_hour, width=0.8, color=style.FREE,
               label="Takma")
        ax.bar([h + 0.5 for h in hours], [-n for n in unplug.by_hour], width=0.8,
               color=style.BUSY, label="Çıkarma")
        ax.axhline(0, color=style.MUTED, linewidth=0.8)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 3), [f"{h:02d}" for h in range(0, 25, 3)])
        top = max(max(plug_in.by_hour), max(unplug.by_hour), 1)
        ax.set_ylim(-top * 1.15, top * 1.15)
        ax.yaxis.set_major_formatter(_absolute_ticks())
        ax.grid(axis="y", color=style.GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_xlabel("Saat", fontsize=style.LABEL_SIZE)
        ax.text(0.2, top * 1.05, "▲ takma", color=style.FREE, fontsize=style.LABEL_SIZE,
                fontweight="bold", va="top")
        ax.text(0.2, -top * 1.05, "▼ çıkarma", color=style.BUSY, fontsize=style.LABEL_SIZE,
                fontweight="bold", va="bottom")
        return style.png(plt, fig)

    def timeline(self, rows: list[TimelineRow], start: datetime, end: datetime, title: str,
                 subtitle: str) -> bytes:
        if not any(row.segments for row in rows):
            return style.placeholder(style.PORTRAIT, title, subtitle, NO_DATA_TEXT)
        plt, fig = style.figure(style.PORTRAIT, title, subtitle)
        import matplotlib.dates as mdates
        ax = fig.add_axes((0.3, 0.13, 0.65, 0.73))
        colours = {AVAILABLE: style.FREE, CHARGING: style.BUSY}
        for index, row in enumerate(rows):
            for state in {s.state for s in row.segments}:
                spans = [(mdates.date2num(s.start), mdates.date2num(s.end) -
                          mdates.date2num(s.start)) for s in row.segments if s.state == state]
                ax.broken_barh(spans, (index + 0.15, 0.7),
                               facecolors=colours.get(state, style.UNUSABLE),
                               hatch=None if state in colours else "////", linewidth=0)
        ax.set_ylim(len(rows), 0)
        ax.set_yticks([i + 0.5 for i in range(len(rows))], [row.name for row in rows],
                      fontsize=style.LABEL_SIZE)
        ax.set_xlim(mdates.date2num(start), mdates.date2num(end))
        days = (end - start).total_seconds() / 86400
        if days <= 1.5:
            ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3), tz=LOCAL_TZ))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=LOCAL_TZ))
        else:
            ax.xaxis.set_major_locator(mdates.DayLocator(tz=LOCAL_TZ))
            ax.xaxis.set_major_formatter(_weekday_formatter())
        ax.grid(axis="x", color=style.GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        _legend(fig, [(style.FREE, "boş"), (style.BUSY, "dolu"),
                      (style.UNUSABLE, "kullanılamaz"), (style.NO_DATA, "gözlem yok")])
        return style.png(plt, fig)

    def occupancy(self, minutes: list[float], median: float | None, title: str,
                  subtitle: str) -> bytes:
        if not minutes:
            return style.placeholder(style.SQUARE, title, subtitle,
                                     "Henüz temiz oturum yok.\nKayıt sürdükçe grafik dolacak.")
        plt, fig = style.figure(style.SQUARE, title, subtitle)
        ax = fig.add_axes((0.11, 0.14, 0.84, 0.68))
        ax.yaxis.set_major_locator(_integer_ticks())
        counts = [sum(low <= m and (high is None or m < high) for m in minutes)
                  for low, high, _ in OCCUPANCY_BINS]
        median_bin = None if median is None else next(
            i for i, (low, high, _) in enumerate(OCCUPANCY_BINS)
            if low <= median and (high is None or median < high))
        colours = [style.FREE if i == median_bin else style.BUSY for i in range(len(counts))]
        bars = ax.bar(range(len(counts)), counts, color=colours, width=0.75)
        for rect, n in zip(bars, counts):
            if n:
                ax.text(rect.get_x() + rect.get_width() / 2, n, str(n), ha="center",
                        va="bottom", fontsize=style.TICK_SIZE, color=style.INK)
        ax.set_xticks(range(len(counts)), [label for _, _, label in OCCUPANCY_BINS],
                      rotation=35, ha="right")
        ax.set_ylabel("Oturum", fontsize=style.LABEL_SIZE)
        ax.grid(axis="y", color=style.GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_ylim(0, max(counts) * 1.18)
        return style.png(plt, fig)

    def sites(self, loads: list[SiteLoad], others_share: float | None, title: str,
              subtitle: str) -> bytes:
        if not loads:
            return style.placeholder(style.PORTRAIT, title, subtitle, NO_DATA_TEXT)
        plt, fig = style.figure(style.PORTRAIT, title, subtitle)
        ax = fig.add_axes((0.36, 0.08, 0.58, 0.8))
        names = [_short(load.name) for load in loads]
        shares = [100 * load.busy_share for load in loads]
        colours = [style.FREE if load.home else style.UNUSABLE for load in loads]
        ax.barh(range(len(loads)), shares, color=colours, height=0.7)
        for i, share in enumerate(shares):
            ax.text(share + 1.5, i, f"%{round(share)}", va="center", fontsize=style.TICK_SIZE,
                    color=style.INK)
        ax.set_yticks(range(len(loads)), names, fontsize=10.5)
        ax.set_ylim(len(loads) - 0.5, -0.5)
        ax.set_xlim(0, 110)
        ax.set_xticks([0, 25, 50, 75, 100], ["%0", "%25", "%50", "%75", "%100"])
        if others_share is not None:
            ax.axvline(100 * others_share, color=style.MUTED, linestyle="--", linewidth=1)
            ax.text(100 * others_share + 1, len(loads) - 0.4, "diğer siteler", fontsize=9.5,
                    color=style.MUTED, va="bottom")
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.grid(axis="x", color=style.GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        return style.png(plt, fig)


def _integer_ticks():
    from matplotlib.ticker import MaxNLocator
    return MaxNLocator(integer=True)


def _absolute_ticks():
    from matplotlib.ticker import FuncFormatter
    return FuncFormatter(lambda value, _: f"{abs(int(value))}")


def _weekday_formatter():
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    def label(value, _):
        moment = mdates.num2date(value, tz=LOCAL_TZ)
        return f"{WEEKDAYS_SHORT[moment.weekday()]}\n{moment.day}"
    return FuncFormatter(label)


def _legend(fig, items: list[tuple[str, str]]) -> None:
    x = 0.3
    for colour, label in items:
        fig.patches.append(_swatch(fig, x, colour))
        fig.text(x + 0.025, 0.04, label, fontsize=10, color=style.MUTED, va="center")
        x += 0.025 + 0.012 * len(label) + 0.04


def _swatch(fig, x: float, colour: str):
    from matplotlib.patches import Rectangle
    return Rectangle((x, 0.032), 0.018, 0.016, transform=fig.transFigure, facecolor=colour,
                     edgecolor=style.GRID, figure=fig)


def _short(name: str, limit: int = 26) -> str:
    return name if len(name) <= limit else name[:limit - 1] + "…"
