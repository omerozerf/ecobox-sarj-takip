"""Which charts exist, what data each one reads, and how a chat word picks one.

`ChartBook` turns a request (selection, label, now) into `Image`s; drawing is delegated to a
renderer (`MatplotlibRenderer` by default), so tests can swap it for a fake.
"""

import statistics as pystats
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from ecobox.charts.render import MatplotlibRenderer, Segment, TimelineRow
from ecobox.fmt import minutes_text, span_text
from ecobox.labels import ShortNames
from ecobox.records import Sample
from ecobox.replies import Image
from ecobox.scope import fold
from ecobox.stats import History, Selection, Statistics

DAY = timedelta(days=1)
WEEK = timedelta(days=7)
MAX_SITES = 15


@dataclass(frozen=True)
class ChartRequest:
    home: Selection
    label: str
    names: ShortNames
    now: datetime


@dataclass(frozen=True)
class ChartKind:
    key: str
    description: str
    aliases: tuple[str, ...]
    build: Callable[["ChartBook", ChartRequest], list[Image]]


class ChartBook:
    def __init__(self, statistics: Statistics, renderer=None,
                 window: timedelta = timedelta(days=56)):
        self._stats = statistics
        self._renderer = renderer or MatplotlibRenderer()
        self._window = window

    # ----- lookup ---------------------------------------------------------------------

    @staticmethod
    def kinds() -> tuple[ChartKind, ...]:
        return KINDS

    @staticmethod
    def resolve(word: str) -> ChartKind | None:
        folded = _ascii(fold(word))
        return next((k for k in KINDS if folded == k.key or folded in k.aliases), None)

    def build(self, kind: ChartKind, request: ChartRequest) -> list[Image]:
        """Raises `ChartsUnavailable` when matplotlib is missing."""
        return kind.build(self, request)

    # ----- chart builders -------------------------------------------------------------

    def _history(self, request: ChartRequest, span: timedelta) -> History:
        return self._stats.history(request.home.window(request.now - span, None))

    def heatmap(self, request: ChartRequest) -> list[Image]:
        history = self._history(request, self._window)
        data = self._renderer.heatmap(
            history.grid, f"{request.label}: boş priz şansı",
            _basis(request, history) + "\nGün × saat; koyu yeşil = boş priz bulmak kolay")
        return [Image(data, "bos-priz-isi-haritasi.png",
                      "Gün ve saate göre boş AC priz bulma şansı")]

    def arrivals(self, request: ChartRequest) -> list[Image]:
        history = self._history(request, self._window)
        plug, unplug = history.plug_in, history.unplug
        data = self._renderer.arrivals(
            plug, unplug, f"{request.label}: ne zaman takıp çıkarıyorlar",
            _basis(request, history) + f"\n{plug.count} takma, {unplug.count} çıkarma; "
                                       "saati belirsiz olanlar yok sayıldı")
        return [Image(data, "takma-cikarma-saatleri.png", "Saate göre takma ve çıkarma sayısı")]

    def timeline(self, request: ChartRequest) -> list[Image]:
        week = self._history(request, WEEK)
        images = []
        for span, title, filename in ((DAY, "son 24 saat", "priz-zaman-cizelgesi-24s.png"),
                                      (WEEK, "son 7 gün", "priz-zaman-cizelgesi-7g.png")):
            history = week.between(request.now - span)
            rows = _timeline_rows(history, request.names, self._stats.gap_threshold)
            data = self._renderer.timeline(
                rows, request.now - span, request.now,
                f"{request.label}: hangi priz ne zaman doluydu",
                f"{title} · " + _basis(request, history))
            images.append(Image(data, filename, f"Priz zaman çizelgesi, {title}"))
        return images

    def occupancy(self, request: ChartRequest) -> list[Image]:
        history = self._history(request, self._window)
        minutes = [s.duration.total_seconds() / 60 for s in history.sessions if s.is_clean]
        durations = history.durations
        subtitle = _basis(request, history) + f"\n{durations.count} temiz oturum"
        if durations.excluded:
            subtitle += f", {durations.excluded} belirsiz sayılmadı"
        if durations.median_min is not None:
            subtitle += f" · yeşil: ortanca {minutes_text(durations.median_min)}"
        data = self._renderer.occupancy(minutes, durations.median_min,
                                        f"{request.label}: prizi işgal süresi", subtitle)
        return [Image(data, "isgal-suresi.png",
                      "Prizin dolu kaldığı süre (şarj süresi değil); yeşil: ortanca")]

    def sites(self, request: ChartRequest) -> list[Image]:
        since = request.now - self._window
        common = self._stats.comparison_start(request.home, since)
        loads = [] if common is None else self._stats.site_loads(request.home, request.label,
                                                                 common)
        others = [load for load in loads if not load.home]
        others_share = (sum(l.busy for l in others) / sum(l.total for l in others)
                        if others else None)
        ranked = sorted(loads, key=lambda load: -load.busy_share)
        shown = ranked[:MAX_SITES]
        home = next((load for load in ranked if load.home), None)
        if home is not None and home not in shown:
            shown[-1] = home
        if common is None:
            subtitle = (f"Diğer Ecobox siteleri henüz kaydedilmiyor; yalnızca {request.label} "
                        "var.\nKayıt kapsamı genişleyince karşılaştırma dolacak.")
        else:
            days = (request.now - common).total_seconds() / 86400
            subtitle = (f"{len(loads)} site, en yoğun {len(shown)} · son {span_text(days)}\n"
                        f"Yeşil: {request.label}; kesik çizgi: diğer sitelerin ortalaması")
        data = self._renderer.sites(shown, others_share, "Ecobox siteleri: AC doluluk oranı",
                                    subtitle)
        return [Image(data, "site-karsilastirma.png", "Sitelere göre AC priz doluluk oranı")]


def _ascii(text: str) -> str:
    """Chart words are matched without Turkish diacritics: "süre" and "sure" are one word."""
    return text.translate(str.maketrans("çğışöü", "cgisou"))


def _basis(request: ChartRequest, history: History) -> str:
    return (f"{len(history.entries)} AC priz · {span_text(history.coverage.days)} gözlem "
            f"({history.coverage.polls} okuma)")


def _timeline_rows(history: History, names: ShortNames,
                   gap_threshold: timedelta) -> list[TimelineRow]:
    by_connector: dict[str, list[Sample]] = {}
    for sample in history.samples:
        by_connector.setdefault(sample.connector_id, []).append(sample)
    steps = [b.ts - a.ts for rows in by_connector.values() for a, b in zip(rows, rows[1:])
             if b.ts - a.ts <= gap_threshold]
    step = pystats.median(steps) if steps else gap_threshold / 2.5
    rows = []
    for entry in sorted(history.entries, key=names.of):
        samples = by_connector.get(entry.connector_id, [])
        segments: list[Segment] = []
        for sample, following in zip(samples, [*samples[1:], None]):
            end = (following.ts if following is not None
                   and following.ts - sample.ts <= gap_threshold else sample.ts + step)
            last = segments[-1] if segments else None
            if last is not None and last.state == sample.state and last.end == sample.ts:
                segments[-1] = Segment(last.start, end, sample.state)
            else:
                segments.append(Segment(sample.ts, end, sample.state))
        rows.append(TimelineRow(names.of(entry), tuple(segments)))
    return rows


KINDS = (
    ChartKind("isi", "gün × saat boş priz şansı", ("harita", "isi haritasi"),
              ChartBook.heatmap),
    ChartKind("saat", "takma ve çıkarma saatleri", ("gelis", "takma", "cikarma"),
              ChartBook.arrivals),
    ChartKind("zaman", "priz zaman çizelgesi, 24 saat ve 7 gün", ("cizelge", "priz"),
              ChartBook.timeline),
    ChartKind("sure", "prizi işgal süresi dağılımı", ("isgal",),
              ChartBook.occupancy),
    ChartKind("site", "Ecobox siteleri karşılaştırması", ("siteler", "karsilastir"),
              ChartBook.sites),
)
