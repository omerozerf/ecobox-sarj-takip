"""Interpreted findings: statistics turned into sentences a person would say out loud.

Each finding type is one *rule*: a function from an `InsightContext` to a list of results,
each either a `Finding` (text plus the basis it rests on) or a `Missing` line saying in one
short sentence what data it still needs. New finding types are added by writing a rule and
listing it in `DEFAULT_RULES`; the order there is the order of the reply (most useful first).

Thresholds (see `Thresholds`; chosen so a finding needs at least a few hours of one pattern
and never rests on one or two events):

- free-socket hours: an hour slot counts once it has 10 polls (30 min of observation) pooled
  over its weekdays; a finding needs 3 such slots. Weekday and weekend are separate findings.
  Below 3 observed dates the finding says "so far (N days)" instead of naming a weekday rule.
- arrivals and departures: 5 plug-ins or unplugs whose time is certain.
- occupancy time: 5 clean sessions (told as "cars"); by arrival time, 3 clean sessions in at
  least 2 slots.
- per socket: 60 samples (3 h) each for the free/reliability ranking; 1 observed day each
  for plug-ins per day (counted per calendar date observed).
- very long occupancy: longer than 4 h, out of at least 5 clean sessions.
- site comparison: 3 other sites with data in the common window, observed for 1 day.
- week change: 2.5 observed days (60 h) in each of the last two weeks; a Mac that sleeps at
  night still reaches that on working days.

Honesty rules carried over from `stats`: uncertain transitions and sessions touching a gap
stay out of every timing figure; occupancy shares count only observed samples. A session's
length is *occupancy time* (the socket is busy), never charge time: the API does not say
whether a plugged car is still charging.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from ecobox.clock import to_local
from ecobox.fmt import (capitalised, decimal, hour_range, locative, minutes_text, percent,
                        percent_from, percent_in, percent_of, span_adjective, span_text)
from ecobox.labels import ShortNames
from ecobox.ranking import by_free_share
from ecobox.stats import (Coverage, FreeCell, FreeGrid, History, PollView, Selection, SiteLoad,
                          Statistics, durations_by_arrival, hour_profile)
from ecobox.state import FAULTED, MAINTENANCE, OFFLINE, UNAVAILABLE

WEEKDAYS = frozenset(range(5))
WEEKEND = frozenset({5, 6})
WEEK = timedelta(days=7)

UNUSABLE_TR = {FAULTED: "arızalı", OFFLINE: "çevrimdışı", MAINTENANCE: "bakımda",
               UNAVAILABLE: "kullanılamaz"}
OCCUPANCY_TOPIC = "prizde kalma süreleri"
SOCKET_DATA = "her prizde 3 saat kayıt"


@dataclass(frozen=True)
class Thresholds:
    min_hour_polls: int = 10
    min_ranked_hours: int = 3
    min_pattern_dates: int = 3
    """Below this many observed dates an hour pattern is told as "so far", not as a rule."""
    min_events: int = 5
    min_sessions: int = 5
    min_bucket_sessions: int = 3
    min_socket_samples: int = 60
    min_socket_days: float = 1.0
    long_occupancy: timedelta = timedelta(hours=4)
    rarely_free: float = 0.10
    min_other_sites: int = 3
    min_site_days: float = 1.0
    min_week_days: float = 2.5
    min_wait_days: int = 3
    """Measured waits needed before `durum` says how soon a socket usually frees up."""
    analysis_window: timedelta = timedelta(days=56)


@dataclass(frozen=True)
class Finding:
    key: str
    emoji: str
    text: str
    basis: str


@dataclass(frozen=True)
class Missing:
    """What a finding still waits for. `needs` is the amount of data ("5 araç", "1 günlük
    kayıt"), `current` how much there is when that is a count; without `needs`, `topic` is a
    whole sentence of its own."""
    key: str
    topic: str
    needs: str | None = None
    current: str | None = None

    @property
    def text(self) -> str:
        """The one-line form: "Prizde kalma süreleri: 5 araç gerekiyor (şu an 3)."."""
        if self.needs is None:
            return self.topic
        now = "" if self.current is None else f" (şu an {self.current})"
        return f"{capitalised(self.topic)}: {self.needs} gerekiyor{now}."


Result = Finding | Missing


@dataclass(frozen=True)
class SiteComparison:
    loads: list[SiteLoad]
    coverage: Coverage
    """The home selection's observation inside the window every site shares."""


@dataclass
class InsightContext:
    history: History
    """The window the findings describe."""
    full: History
    """A longer history ending at the same moment, for comparisons across weeks."""
    names: ShortNames
    label: str
    now: datetime
    thresholds: Thresholds
    sites: Callable[[], SiteComparison | None]
    """Lazy: other sites are only read when the rule that needs them runs."""


Rule = Callable[[InsightContext], list[Result]]


@dataclass(frozen=True)
class Analysis:
    label: str
    sockets: int
    coverage: Coverage
    findings: list[Finding] = field(default_factory=list)
    missing: list[Missing] = field(default_factory=list)


# ----- building blocks shared with the charts -----------------------------------------------

@dataclass(frozen=True)
class HourExtremes:
    """The easiest and the hardest hour to find a free socket, over hours with enough data."""
    best_hour: int
    best: FreeCell
    worst_hour: int
    worst: FreeCell
    slots: int

    @property
    def flat(self) -> bool:
        return self.best.probability == self.worst.probability


def hour_extremes(grid: FreeGrid, weekdays: frozenset[int],
                  t: Thresholds) -> HourExtremes | None:
    profile = hour_profile(grid, set(weekdays))
    hours = [(h, c) for h, c in enumerate(profile) if c.total >= t.min_hour_polls]
    if len(hours) < t.min_ranked_hours:
        return None
    best_hour, best = max(hours, key=lambda hc: (hc[1].probability, -hc[0]))
    worst_hour, worst = min(hours, key=lambda hc: (hc[1].probability, hc[0]))
    return HourExtremes(best_hour, best, worst_hour, worst, len(hours))


def observed_dates(polls: list[PollView], weekdays: frozenset[int]) -> int:
    """Distinct local dates with a poll on one of the given weekdays."""
    return len({to_local(p.ts).date() for p in polls if to_local(p.ts).weekday() in weekdays})


def peak_window(by_hour: list[int], span: int = 2) -> tuple[int, int]:
    """Start hour and event count of the busiest `span`-hour window (wrapping midnight)."""
    sums = [(sum(by_hour[(h + i) % 24] for i in range(span)), -h) for h in range(24)]
    count, negative_hour = max(sums)
    return -negative_hour, count


def easy_hard_text(ext: HourExtremes) -> str:
    """"en kolay 16:00–17:00 (%100), en zor 13:00–14:00 (%20)"."""
    return (f"en kolay {hour_range(ext.best_hour)} ({percent(ext.best.probability)}), "
            f"en zor {hour_range(ext.worst_hour)} ({percent(ext.worst.probability)})")


# ----- rules --------------------------------------------------------------------------------

def free_hours(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    groups = [(name, days, hour_extremes(ctx.history.grid, days, t))
              for name, days in (("Hafta içi", WEEKDAYS), ("Hafta sonu", WEEKEND))]
    found = [(name, days, ext) for name, days, ext in groups if ext is not None]
    if not found:
        return [Missing("free_hours", "boş priz saatleri",
                        f"{t.min_ranked_hours} farklı saatte yarımşar saat kayıt")]
    results: list[Result] = []
    for name, days, ext in found:
        dates = observed_dates(ctx.history.polls, days)
        group = name if len(found) == 2 else None
        results.append(_free_hours_finding(name, group, ext, dates, ctx.history.coverage, t))
    for name, days, ext in groups:
        if ext is None:
            seen = observed_dates(ctx.history.polls, days)
            results.append(Missing(f"free_hours_{name}",
                                   f"{name} kaydı henüz {'az' if seen else 'yok'}."))
    return results


def _free_hours_finding(name: str, group: str | None, ext: HourExtremes, dates: int,
                        cov: Coverage, t: Thresholds) -> Finding:
    """A settled pattern is told by weekday group; a thin one only as "so far (N days)"."""
    record = f"{span_adjective(cov.days)} kayıt"
    if dates < t.min_pattern_dates:
        where = f" {group.lower()}" if group else ""
        head = f"Şimdiye kadar{where} ({dates} gün): "
        if ext.flat:
            text = f"{head}gözlenen her saatte boş priz bulma şansı {percent(ext.best.probability)}."
        else:
            text = f"{head}boş priz {easy_hard_text(ext)}."
        return Finding(f"free_hours_{name}", "🟢", text, record)
    if ext.flat:
        text = f"{name} gözlenen her saatte boş priz bulma şansı {percent(ext.best.probability)}."
    else:
        text = f"{name} boş priz {easy_hard_text(ext)}"
        text += (": o saatte gidersen büyük ihtimalle beklersin."
                 if ext.worst.probability < 0.25 else ".")
    return Finding(f"free_hours_{name}", "🟢", text, f"{ext.slots} saat dilimi · {record}")


def socket_ranking(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    profiles = [p for p in ctx.history.profiles if p.total >= t.min_socket_samples]
    if len(profiles) < 2:
        return [Missing("socket_ranking", "priz sıralaması", SOCKET_DATA)]
    ranked = by_free_share(profiles, lambda p: p.free_share, lambda p: ctx.names.of(p.entry))
    top = [p for p in ranked if p.free_share >= t.rarely_free][:3]
    rarely = [p for p in reversed(ranked) if p.free_share < t.rarely_free]
    if top:
        first = top[0]
        text = (f"İlk bakılacak yer: {ctx.names.of(first.entry)} (zamanın "
                f"{percent_in(first.free_share)} boş).")
        if len(top) > 1:
            text += " Sonra " + ", ".join(f"{ctx.names.of(p.entry)} {percent(p.free_share)}"
                                          for p in top[1:]) + "."
    elif not any(p.free_share for p in ranked):
        text, rarely = "Gözlenen sürede hiçbir priz boş görülmedi.", []
    else:
        text = f"Hiçbir priz zamanın {percent_from(t.rarely_free)} fazla boş değil."
    if rarely:
        more = f" ve {len(rarely) - 4} priz daha" if len(rarely) > 4 else ""
        text += " Nadiren boş: " + _grouped_rarely(rarely[:4], ctx.names) + more + "."
    days = min(p.days for p in profiles)
    return [Finding("socket_ranking", "📍", text,
                    f"{len(profiles)} priz · her biri en az {span_text(days)} kayıtlı")]


def _rarely_note(profile) -> str:
    note = percent(profile.free_share)
    if profile.unusable_share >= 0.5:
        note += f", {UNUSABLE_TR[profile.main_unusable_state]}"
    return note


def _grouped_rarely(profiles, names: ShortNames) -> str:
    """"Ego 4, Ego 1 (%4), F Blok 3 (%8)": neighbours with the same figure share it."""
    groups: list[tuple[str, list[str]]] = []
    for profile in profiles:
        note = _rarely_note(profile)
        if groups and groups[-1][0] == note:
            groups[-1][1].append(names.of(profile.entry))
        else:
            groups.append((note, [names.of(profile.entry)]))
    return ", ".join(f"{', '.join(members)} ({note})" for note, members in groups)


def arrivals_departures(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    plug, unplug = ctx.history.plug_in, ctx.history.unplug
    if plug.count < t.min_events and unplug.count < t.min_events:
        return [Missing("arrivals", "geliş ve ayrılış saatleri",
                        f"{t.min_events} takma ya da çıkarma",
                        str(max(plug.count, unplug.count)))]
    parts = []
    if plug.count >= t.min_events:
        hour, n = peak_window(plug.by_hour)
        parts.append(f"en çok {hour_range(hour, 2)} arası takıyor "
                     f"(takmaların {percent_of(n / plug.count)})")
    if unplug.count >= t.min_events:
        hour, n = peak_window(unplug.by_hour)
        parts.append(f"{hour_range(hour, 2)} arası çıkarıyor "
                     f"(çıkarmaların {percent_of(n / unplug.count)})")
    text = "İnsanlar " + ", ".join(parts) + "."
    if len(parts) == 2:
        text = text.replace(", ", ", en çok ", 1)
    basis = f"{plug.count} takma, {unplug.count} çıkarma"
    if plug.excluded + unplug.excluded:
        basis += f"; saati kesin olmayan {plug.excluded + unplug.excluded} sayılmadı"
    return [Finding("arrivals", "🚗", text, basis)]


def _occupancy_missing(key: str, ctx: InsightContext, count: int) -> Missing:
    return Missing(key, OCCUPANCY_TOPIC, f"{ctx.thresholds.min_sessions} araç", str(count))


def occupancy_time(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    d = ctx.history.durations
    if d.count < t.min_sessions:
        return [_occupancy_missing("occupancy", ctx, d.count)]
    text = f"Bir araç prizde genelde {minutes_text(d.median_min)} kalıyor"
    if d.q1_min is not None and d.q1_min != d.q3_min:
        text += f" (çoğu {minutes_text(d.q1_min)} – {minutes_text(d.q3_min)})"
    text += "."
    if d.median_min >= 180:
        text += " Dolu bir prizin kısa sürede boşalmasını bekleme."
    elif d.median_min <= 60:
        text += " Prizler hızlı dönüyor; dolu bir priz kısa sürede boşalabilir."
    basis = f"{d.count} araç"
    if d.excluded:
        basis += f"; kayıt kesintisine denk gelen {d.excluded} araç sayılmadı"
    return [Finding("occupancy", "⏱", text, basis)]


def occupancy_by_arrival(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    if ctx.history.durations.count < t.min_sessions:
        return [_occupancy_missing("occupancy_by_arrival", ctx, ctx.history.durations.count)]
    usable = [(b, d) for b, d in durations_by_arrival(ctx.history.sessions)
              if d.count >= t.min_bucket_sessions]
    if len(usable) < 2:
        return [Missing("occupancy_by_arrival", "geliş saatine göre kalma",
                        f"iki zaman diliminde {t.min_bucket_sessions}'er araç")]
    text = "Prizde kalma, geliş saatine göre: " + " · ".join(
        f"{b.label} {minutes_text(d.median_min)}" for b, d in usable) + "."
    longest = max(usable, key=lambda bd: bd[1].median_min)
    shortest = min(usable, key=lambda bd: bd[1].median_min)
    ratio = longest[1].median_min / shortest[1].median_min
    if ratio >= 2:
        text += (f" {capitalised(longest[0].label)} gelenler, {shortest[0].label} gelenlerden "
                 f"{decimal(ratio)} kat uzun kalıyor.")
    return [Finding("occupancy_by_arrival", "🌅", text,
                    " + ".join(str(d.count) for _, d in usable) + " araç")]


def turnover(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    # A socket that is mostly faulted or offline is a reliability finding, not a quiet one.
    profiles = [p for p in ctx.history.profiles
                if p.days >= t.min_socket_days and p.unusable_share < 0.5]
    if len(profiles) < 2:
        return [Missing("turnover", "priz başına kullanım",
                        f"{span_adjective(t.min_socket_days)} kayıt")]
    ranked = sorted(profiles, key=lambda p: (-p.plug_ins_per_day, ctx.names.of(p.entry)))
    busiest, quietest = ranked[0], ranked[-1]
    total = sum(p.plug_ins_per_day for p in profiles)
    text = (f"En hareketli priz {ctx.names.of(busiest.entry)}: günde "
            f"{decimal(busiest.plug_ins_per_day)} kullanım. En durgun "
            f"{ctx.names.of(quietest.entry)}: günde {decimal(quietest.plug_ins_per_day)}. "
            f"Hepsi birlikte günde ~{decimal(total)} takma.")
    return [Finding("turnover", "🔄", text,
                    f"{len(profiles)} priz · yalnızca saati kesin takmalar sayıldı")]


def long_occupancy(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    limit = t.long_occupancy.total_seconds() / 60
    minutes = [m for p in ctx.history.profiles for m in p.clean_minutes]
    if len(minutes) < t.min_sessions:
        return [_occupancy_missing("long_occupancy", ctx, len(minutes))]
    hours = round(limit / 60)
    share = sum(m > limit for m in minutes) / len(minutes)
    if not share:
        text = f"{hours} saati aşan araç görülmedi."
    else:
        text = f"Araçların {percent_of(share)} {hours} saatten uzun kalıyor."
        per_socket = sorted(
            ((sum(m > limit for m in p.clean_minutes) / len(p.clean_minutes), p)
             for p in ctx.history.profiles if len(p.clean_minutes) >= t.min_bucket_sessions),
            key=lambda sp: (-sp[0], ctx.names.of(sp[1].entry)))
        leaders = [(s, p) for s, p in per_socket if s > 0][:3]
        if leaders:
            text += " En çok: " + ", ".join(f"{ctx.names.of(p.entry)} {percent(s)}"
                                            for s, p in leaders) + "."
    basis = f"{len(minutes)} araç"
    if ctx.history.durations.excluded:
        basis += "; kayıt kesintisine denk gelenler sayılmadı"
    return [Finding("long_occupancy", "🐢", text, basis)]


def reliability(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    profiles = [p for p in ctx.history.profiles if p.total >= t.min_socket_samples]
    if not profiles:
        return [Missing("reliability", "arıza durumu", SOCKET_DATA)]
    bad = sorted((p for p in profiles if p.unusable_share >= 0.01),
                 key=lambda p: (-p.unusable_share, ctx.names.of(p.entry)))
    if not bad:
        text = "Arıza yok: hiçbir priz arızalı, çevrimdışı ya da bakımda görünmedi."
    else:
        text = "Kullanılamayan prizler: " + ", ".join(
            f"{ctx.names.of(p.entry)} {percent(p.unusable_share)} "
            f"{UNUSABLE_TR[p.main_unusable_state]}" for p in bad) + "."
        healthy = len(profiles) - len(bad)
        if healthy:
            text += f" Diğer {healthy} priz sorunsuz."
    return [Finding("reliability", "🛠", text,
                    f"{len(profiles)} priz · {span_adjective(ctx.history.coverage.days)} kayıt")]


@dataclass(frozen=True)
class SiteVerdict:
    """The comparison once its thresholds are met: home's busy share against the others'."""
    home: SiteLoad
    others: list[SiteLoad]
    overall: float
    """Busy share of every other site's AC sockets pooled."""
    days: float


def site_verdict(comparison: SiteComparison | None, t: Thresholds,
                 home_days: float) -> SiteVerdict | Missing:
    """Shared by the `analiz` finding and the site chart, so both wait for the same data.

    `home_days` is the home selection's whole record: while it is short itself, the wait is
    told as plain record time, not as the common window."""
    others = [] if comparison is None else [l for l in comparison.loads if not l.home]
    home = None if comparison is None else next((l for l in comparison.loads if l.home), None)
    if home is None or len(others) < t.min_other_sites:
        return Missing("sites", "Diğer Ecobox siteleri henüz yeterince kaydedilmedi.")
    days = comparison.coverage.days
    if days < t.min_site_days:
        common = " ortak" if home_days >= t.min_site_days else ""
        return Missing("sites", "site karşılaştırması",
                       f"{span_adjective(t.min_site_days)}{common} kayıt")
    overall = sum(l.busy for l in others) / sum(l.total for l in others)
    return SiteVerdict(home, others, overall, days)


def site_comparison(ctx: InsightContext) -> list[Result]:
    verdict = site_verdict(ctx.sites(), ctx.thresholds, ctx.history.coverage.days)
    if isinstance(verdict, Missing):
        return [verdict]
    home, overall = verdict.home.busy_share, verdict.overall
    text = (f"{locative(ctx.label)} AC prizler zamanın {percent_in(home)} dolu; diğer "
            f"Ecobox sitelerinde ortalama {percent(overall)}.")
    if home > 1.3 * overall:
        text += f" {ctx.label} ortalamadan belirgin yoğun."
    elif home < overall / 1.3:
        text += f" {ctx.label} ortalamadan belirgin sakin."
    else:
        text += " Ortalamaya yakın."
    sockets = sum(l.sockets for l in verdict.others)
    return [Finding("sites", "🗺", text,
                    f"{len(verdict.others)} site, {sockets} AC priz · "
                    f"{span_adjective(verdict.days)} ortak kayıt")]


def week_change(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    this_week = ctx.full.between(ctx.now - WEEK)
    last_week = ctx.full.between(ctx.now - 2 * WEEK, ctx.now - WEEK)
    days = (this_week.coverage.days, last_week.coverage.days)
    if min(days) < t.min_week_days:
        return [Missing("week_change", "haftalık değişim", "2 haftalık kayıt")]
    now_free, before_free = this_week.free_share, last_week.free_share
    diff = round(100 * now_free) - round(100 * before_free)
    if abs(diff) < 3:
        text = (f"Bu hafta boş priz bulma şansı {percent(now_free)}, geçen haftayla aynı "
                f"düzeyde.")
    else:
        direction = "arttı" if diff > 0 else "düştü"
        text = (f"Bu hafta boş priz bulma şansı {percent(now_free)}, geçen hafta "
                f"{percent(before_free)}: {abs(diff)} puan {direction}.")
    per_day = [w.plug_in.count / w.coverage.dates for w in (last_week, this_week)]
    text += f" Günde ortalama takma: {decimal(per_day[0])} → {decimal(per_day[1])}."
    return [Finding("week_change", "📈", text,
                    f"son 7 gün ({span_adjective(days[0])} kayıt) ve önceki 7 gün "
                    f"({span_text(days[1])})")]


DEFAULT_RULES: tuple[Rule, ...] = (
    free_hours,
    socket_ranking,
    occupancy_time,
    arrivals_departures,
    occupancy_by_arrival,
    turnover,
    long_occupancy,
    reliability,
    site_comparison,
    week_change,
)


# ----- engine -------------------------------------------------------------------------------

class InsightEngine:
    def __init__(self, statistics: Statistics, thresholds: Thresholds = Thresholds(),
                 rules: tuple[Rule, ...] = DEFAULT_RULES):
        self._stats = statistics
        self._thresholds = thresholds
        self._rules = rules

    @property
    def thresholds(self) -> Thresholds:
        return self._thresholds

    def analyse(self, home: Selection, label: str, names: ShortNames, now: datetime,
                since: datetime | None = None) -> Analysis:
        """Findings about `home` from `since` (default: the analysis window) to the newest
        poll."""
        since = since or now - self._thresholds.analysis_window
        full_since = min(since, now - 2 * WEEK)
        full = self._stats.history(home.window(full_since, None))  # up to the newest poll
        history = full.between(since)
        ctx = InsightContext(history, full, names, label, now, self._thresholds,
                             lambda: self._sites(home, label, history, since, now))
        analysis = Analysis(label, len(history.entries), history.coverage)
        for rule in self._rules:
            for result in rule(ctx):
                target = analysis.findings if isinstance(result, Finding) else analysis.missing
                target.append(result)
        return analysis

    def _sites(self, home: Selection, label: str, history: History, since: datetime,
               now: datetime) -> SiteComparison | None:
        """Compares over the window in which the other sites were recorded too."""
        common = self._stats.comparison_start(home, since)
        if common is None:
            return None
        loads = self._stats.site_loads(home, label, common)
        return SiteComparison(loads, history.between(common).coverage)
