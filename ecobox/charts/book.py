"""Which charts exist, what data each one reads, and how a chat word picks one.

`ChartBook` turns a request (selection, label, now) into `Image`s; drawing is delegated to a
renderer (`MatplotlibRenderer` by default), so tests can swap it for a fake.

Each chart's title is its takeaway ("Hafta içi boş priz en kolay 16:00–17:00"), computed with
the same building blocks and thresholds as `analiz`; until a threshold is met the title stays
descriptive and the caption says that data is still short. The caption is what the chat shows
under the photo.
"""

import statistics as pystats
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from ecobox.charts.render import MatplotlibRenderer, Segment, SiteBar, TimelineRow
from ecobox.fmt import (capitalised, clock_range, hour_range, minutes_text, percent,
                        span_adjective, span_text)
from ecobox.insights import (WEEKDAYS, WEEKEND, HourExtremes, Missing, SiteComparison,
                             Thresholds, hour_extremes, observed_dates, peak_window,
                             site_verdict)
from ecobox.labels import ShortNames
from ecobox.records import Sample
from ecobox.replies import Image
from ecobox.scope import fold
from ecobox.state import AVAILABLE
from ecobox.stats import History, Selection, SiteLoad, Statistics, TimeDistribution

DAY = timedelta(days=1)
WEEK = timedelta(days=7)
FRESH = timedelta(minutes=10)
SITE_NEIGHBOURS = 4
"""Sites shown above and below the home site in the ranking."""
MIN_SITE_SOCKETS = 2
"""Single-socket sites swing between 0 and 100 % and crowd the ranking: left out."""
SHORT_DATA = "henüz az kayıt"


@dataclass(frozen=True)
class ChartRequest:
    home: Selection
    label: str
    names: ShortNames
    now: datetime
    since: datetime | None = None
    """Start of the history the gün × saat chart reads; None: the book's window."""


@dataclass(frozen=True)
class ChartKind:
    key: str
    description: str
    aliases: tuple[str, ...]
    build: Callable[["ChartBook", ChartRequest], list[Image]]


@dataclass(frozen=True)
class Takeaway:
    title: str
    caption: str


class ChartBook:
    def __init__(self, statistics: Statistics, renderer=None,
                 window: timedelta = timedelta(days=56), thresholds: Thresholds = Thresholds()):
        self._stats = statistics
        self._renderer = renderer or MatplotlibRenderer()
        self._window = window
        self._t = thresholds

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

    def _history(self, request: ChartRequest, since: datetime) -> History:
        return self._stats.history(request.home.window(since, None))

    def heatmap(self, request: ChartRequest) -> list[Image]:
        history = self._history(request, request.since or request.now - self._window)
        takeaway = self._free_hours_takeaway(history)
        subtitle = (_basis(request, history)
                    + "\nSayı: o saatte boş priz bulma yüzdesi · taralı: kayıt yok")
        data = self._renderer.heatmap(history.grid, takeaway.title, subtitle)
        return [Image(data, "bos-priz-isi-haritasi.png", takeaway.caption)]

    def _free_hours_takeaway(self, history: History) -> Takeaway:
        descriptive = "Gün × saat boş priz şansı"
        for group, days in (("Hafta içi", WEEKDAYS), ("Hafta sonu", WEEKEND)):
            ext = hour_extremes(history.grid, days, self._t)
            if ext is None:
                continue
            dates = observed_dates(history.polls, days)
            if dates < self._t.min_pattern_dates:
                return Takeaway(descriptive,
                                f"🟢 Şimdiye kadar ({dates} gün): boş priz {_easy_hard(ext)}")
            title = (f"{group} her saatte boş priz şansı {percent(ext.best.probability)}"
                     if ext.flat else f"{group} boş priz en kolay {hour_range(ext.best_hour)}")
            return Takeaway(title, f"🟢 {group} boş priz {_easy_hard(ext)}")
        return Takeaway(descriptive, f"🟢 {descriptive} · {SHORT_DATA}")

    def arrivals(self, request: ChartRequest) -> list[Image]:
        history = self._history(request, request.now - self._window)
        plug, unplug = history.plug_in, history.unplug
        takeaway = self._arrivals_takeaway(plug, unplug)
        subtitle = (_basis(request, history)
                    + f"\n{plug.count} takma, {unplug.count} çıkarma (saati kesin olanlar)")
        data = self._renderer.arrivals(plug, unplug, takeaway.title, subtitle)
        return [Image(data, "takma-cikarma-saatleri.png", takeaway.caption)]

    def _arrivals_takeaway(self, plug: TimeDistribution,
                           unplug: TimeDistribution) -> Takeaway:
        parts = []
        for word, dist in (("takma", plug), ("çıkarma", unplug)):
            if dist.count >= self._t.min_events:
                hour, _ = peak_window(dist.by_hour)
                parts.append(f"{word} {clock_range(hour, hour + 2)}")
        if not parts:
            return Takeaway("Saate göre takma ve çıkarma",
                            f"🚗 Takma ve çıkarma saatleri · {SHORT_DATA}")
        title = capitalised(", ".join(parts) + " arası yoğun")
        return Takeaway(title, f"🚗 {title}")

    def timeline(self, request: ChartRequest) -> list[Image]:
        week = self._history(request, request.now - WEEK)
        title = _now_free_title(week, request.now)
        spans = [(DAY, "Son 24 saat", "priz-zaman-cizelgesi-24s.png")]
        note = ""
        if week.coverage.days >= 1:
            spans.append((WEEK, "Son 7 gün", "priz-zaman-cizelgesi-7g.png"))
        else:
            note = "\n7 günlük çizelge 1 gün kayıttan sonra gelecek."
        images = []
        for span, name, filename in spans:
            history = week.between(request.now - span)
            rows = _timeline_rows(history, request.names, self._stats.gap_threshold)
            data = self._renderer.timeline(rows, request.now - span, request.now, title,
                                           f"{name} · " + _basis(request, history))
            images.append(Image(data, filename, f"🕒 {name}"))
        images[0] = Image(images[0].data, images[0].filename, f"🕒 {title}{note}")
        return images

    def occupancy(self, request: ChartRequest) -> list[Image]:
        history = self._history(request, request.now - self._window)
        minutes = [s.duration.total_seconds() / 60 for s in history.sessions if s.is_clean]
        durations = history.durations
        subtitle = _basis(request, history) + f"\n{durations.count} araç"
        if durations.median_min is not None:
            subtitle += f" · çizgi: ortanca {minutes_text(durations.median_min)}"
        if durations.count >= self._t.min_sessions:
            title = f"Araçlar genelde {minutes_text(durations.median_min)} kalıyor"
            caption = f"⏱ {title}"
        else:
            title = "Prizde kalma süresi"
            caption = f"⏱ {title} · henüz az araç ({durations.count})"
        data = self._renderer.occupancy(minutes, durations.median_min, title, subtitle)
        return [Image(data, "isgal-suresi.png", caption)]

    def sites(self, request: ChartRequest) -> list[Image]:
        since = request.now - self._window
        common = self._stats.comparison_start(request.home, since)
        home = self._history(request, since)
        comparison = None
        if common is not None:
            comparison = SiteComparison(
                self._stats.site_loads(request.home, request.label, common),
                home.between(common).coverage)
        verdict = site_verdict(comparison, self._t, home.coverage.days)
        filename = "site-karsilastirma.png"
        if isinstance(verdict, Missing):
            title, message, caption = _sites_waiting(verdict, comparison, self._t)
            data = self._renderer.notice(title, f"{request.label} ve diğer Ecobox siteleri",
                                         message)
            return [Image(data, filename, caption)]
        eligible = [l for l in comparison.loads if l.home or l.sockets >= MIN_SITE_SOCKETS]
        bars = _site_bars(eligible, len(verdict.others), verdict.overall)
        title = (f"{request.label} {percent(verdict.home.busy_share)} dolu, diğer siteler "
                 f"{percent(verdict.overall)}")
        subtitle = (f"{len(eligible)} site, en az {MIN_SITE_SOCKETS} AC prizli · "
                    f"{span_adjective(verdict.days)} ortak kayıt\nTuruncu: {request.label} · "
                    "kesik çizgi: diğerleri")
        data = self._renderer.sites(bars, verdict.overall, title, subtitle)
        return [Image(data, filename, f"🗺 {title}")]


def _ascii(text: str) -> str:
    """Chart words are matched without Turkish diacritics: "süre" and "sure" are one word."""
    return text.translate(str.maketrans("çğışöü", "cgisou"))


def _basis(request: ChartRequest, history: History) -> str:
    return (f"{request.label} · {len(history.entries)} AC priz · "
            f"{span_adjective(history.coverage.days)} kayıt")


def _easy_hard(ext: HourExtremes) -> str:
    if ext.flat:
        return f"her saatte {percent(ext.best.probability)}"
    return f"en kolay {hour_range(ext.best_hour)}, en zor {hour_range(ext.worst_hour)}"


def _now_free_title(history: History, now: datetime) -> str:
    """"Şu an 5/12 priz boş" from the newest reading; "Son okumada" once it is stale."""
    if not history.samples:
        return "Hangi priz ne zaman doluydu"
    newest = max(s.ts for s in history.samples)
    latest = [s for s in history.samples if s.ts == newest]
    free = sum(s.state == AVAILABLE for s in latest)
    when = "Şu an" if now - newest <= FRESH else "Son okumada"
    return f"{when} {free}/{len(history.entries)} priz boş"


def _sites_waiting(missing: Missing, comparison: SiteComparison | None,
                   t: Thresholds) -> tuple[str, str, str]:
    """Title, message and caption while the comparison waits for data."""
    title = "Ecobox siteleri: AC doluluk"
    if missing.needs is None:
        return (title, "Diğer Ecobox siteleri henüz yeterince kaydedilmedi.\nKayıt kapsamı "
                       "genişleyince karşılaştırma dolacak.",
                f"🗺 Site karşılaştırması · {SHORT_DATA}")
    now = span_text(comparison.coverage.days)
    return (title, f"Karşılaştırma için en az {span_text(t.min_site_days)} ortak kayıt "
                   f"gerekiyor (şu an {now}).",
            f"🗺 Site karşılaştırması · {SHORT_DATA} ({now})")


def _site_bars(eligible: list[SiteLoad], others: int, overall: float) -> list[SiteBar]:
    """The home site with its neighbours in the busy ranking, then the others' average."""
    ranked = sorted(eligible, key=lambda l: (-l.busy_share, l.name))
    index = next(i for i, l in enumerate(ranked) if l.home)
    shown = ranked[max(0, index - SITE_NEIGHBOURS):index + SITE_NEIGHBOURS + 1]
    bars = [SiteBar(l.name, l.busy_share, l.home) for l in shown]
    return bars + [SiteBar(f"Diğer {others} site ortalaması", overall, average=True)]


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
    ChartKind("harita", "gün × saat boş priz şansı", ("isi", "isi haritasi"),
              ChartBook.heatmap),
    ChartKind("saat", "takma ve çıkarma saatleri", ("gelis", "takma", "cikarma"),
              ChartBook.arrivals),
    ChartKind("zaman", "priz zaman çizelgesi, 24 saat ve 7 gün", ("cizelge", "priz"),
              ChartBook.timeline),
    ChartKind("sure", "prizde kalma süresi", ("isgal", "kalma"),
              ChartBook.occupancy),
    ChartKind("site", "Ecobox siteleri karşılaştırması", ("siteler", "karsilastir"),
              ChartBook.sites),
)
