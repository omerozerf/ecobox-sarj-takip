"""Interpreted findings: statistics turned into sentences a person would say out loud.

Each finding type is one *rule*: a function from an `InsightContext` to a list of results,
each either a `Finding` (text plus the basis it rests on) or a `Missing` line saying in one
short sentence what data it still needs. New finding types are added by writing a rule and
listing it in `DEFAULT_RULES`; the order there is the order of the reply (most useful first).

Thresholds (see `Thresholds`; chosen so a finding needs at least a few hours of one pattern
and never rests on one or two events):

- free-socket hours: an hour slot counts once it has 10 polls (30 min of observation) pooled
  over its weekdays; a finding needs 3 such slots. Weekday and weekend are separate findings.
- arrivals and departures: 5 plug-ins or unplugs whose time is certain.
- occupancy time: 5 clean sessions; by arrival time, 3 clean sessions in at least 2 slots.
- per socket: 60 samples (3 h) each for the free/reliability ranking; 1 observed day each
  for plug-ins per day (counted per calendar date observed).
- very long occupancy: longer than 4 h, out of at least 5 clean sessions.
- site comparison: 3 other sites with data in the common window.
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

from ecobox.fmt import (capitalised, decimal, hour_range, minutes_text, percent, percent_of,
                        span_text)
from ecobox.labels import ShortNames
from ecobox.stats import (Coverage, History, Selection, SiteLoad, Statistics,
                          durations_by_arrival, hour_profile)
from ecobox.state import FAULTED, MAINTENANCE, OFFLINE, UNAVAILABLE

WEEKDAYS = frozenset(range(5))
WEEKEND = frozenset({5, 6})
WEEK = timedelta(days=7)

UNUSABLE_TR = {FAULTED: "arızalı", OFFLINE: "çevrimdışı", MAINTENANCE: "bakımda",
               UNAVAILABLE: "kullanılamaz"}


@dataclass(frozen=True)
class Thresholds:
    min_hour_polls: int = 10
    min_ranked_hours: int = 3
    min_events: int = 5
    min_sessions: int = 5
    min_bucket_sessions: int = 3
    min_socket_samples: int = 60
    min_socket_days: float = 1.0
    long_occupancy: timedelta = timedelta(hours=4)
    rarely_free: float = 0.10
    min_other_sites: int = 3
    min_week_days: float = 2.5
    analysis_window: timedelta = timedelta(days=56)


@dataclass(frozen=True)
class Finding:
    key: str
    emoji: str
    text: str
    basis: str


@dataclass(frozen=True)
class Missing:
    key: str
    text: str


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


# ----- rules --------------------------------------------------------------------------------

def free_hours(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    results: list[Result] = []
    qualified = {}
    for name, days in (("Hafta içi", WEEKDAYS), ("Hafta sonu", WEEKEND)):
        profile = hour_profile(ctx.history.grid, set(days))
        qualified[name] = [(h, c) for h, c in enumerate(profile) if c.total >= t.min_hour_polls]
    for name, hours in qualified.items():
        if len(hours) >= t.min_ranked_hours:
            results.append(_free_hours_finding(name, hours, ctx.history.coverage))
    if not results:
        return [Missing("free_hours", "Boş priz saatleri: en az 3 saat diliminde yarımşar saat "
                                      "gözlem gerekiyor.")]
    for name, hours in qualified.items():
        if len(hours) < t.min_ranked_hours:
            results.append(Missing(f"free_hours_{name}",
                                   f"{name} saatleri: henüz yeterli gözlem yok."))
    return results


def _free_hours_finding(group: str, hours, cov: Coverage) -> Finding:
    best_hour, best = max(hours, key=lambda hc: (hc[1].probability, -hc[0]))
    worst_hour, worst = min(hours, key=lambda hc: (hc[1].probability, hc[0]))
    if best.probability == worst.probability:
        text = (f"{group} gözlenen her saatte boş priz bulma şansı "
                f"{percent(best.probability)}.")
    else:
        text = (f"{group} boş priz bulmak en kolay {hour_range(best_hour)} arası "
                f"({percent(best.probability)}), en zor {hour_range(worst_hour)} arası "
                f"({percent(worst.probability)}).")
        if worst.probability < 0.25:
            text += f" {hour_range(worst_hour)} arası gidersen büyük ihtimalle beklersin."
    return Finding(f"free_hours_{group}", "🟢", text,
                   f"{len(hours)} saat dilimi · {span_text(cov.days)} gözlem")


def socket_ranking(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    profiles = [p for p in ctx.history.profiles if p.total >= t.min_socket_samples]
    if len(profiles) < 2:
        return [Missing("socket_ranking", "Priz sıralaması: her priz için en az 3 saat gözlem "
                                          "gerekiyor.")]
    ranked = sorted(profiles, key=lambda p: (-p.free_share, ctx.names.of(p.entry)))
    top = [p for p in ranked[:3] if p.free_share > 0]
    if not top:
        text = "Gözlenen sürede hiçbir priz boş görülmedi."
    else:
        text = "En sık boş: " + ", ".join(
            f"{ctx.names.of(p.entry)} {percent(p.free_share)}" for p in top) + "."
    rarely = [p for p in reversed(ranked) if p.free_share < t.rarely_free and p not in top]
    if rarely and top:
        shown = [_rarely_free(p, ctx.names) for p in rarely[:4]]
        more = f" ve {len(rarely) - 4} priz daha" if len(rarely) > 4 else ""
        text += " Neredeyse hiç boş değil: " + ", ".join(shown) + more + "."
    if top:
        text += f" İlk bakılacak yer: {ctx.names.of(top[0].entry)}."
    days = min(p.days for p in profiles)
    return [Finding("socket_ranking", "📍", text,
                    f"{len(profiles)} priz · her biri en az {span_text(days)} gözlendi")]


def _rarely_free(profile, names: ShortNames) -> str:
    text = f"{names.of(profile.entry)} {percent(profile.free_share)}"
    if profile.unusable_share >= 0.5:
        text += f" ({UNUSABLE_TR[profile.main_unusable_state]})"
    return text


def arrivals_departures(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    plug, unplug = ctx.history.plug_in, ctx.history.unplug
    if plug.count < t.min_events and unplug.count < t.min_events:
        return [Missing("arrivals", f"Geliş ve ayrılış saatleri: en az {t.min_events} kesin "
                                    f"takma ya da çıkarma gerekiyor (şu an "
                                    f"{max(plug.count, unplug.count)}).")]
    parts = []
    if plug.count >= t.min_events:
        hour, n = _peak_window(plug.by_hour)
        parts.append(f"en çok {hour_range(hour, 2)} arası takıyor "
                     f"(takmaların {percent_of(n / plug.count)})")
    if unplug.count >= t.min_events:
        hour, n = _peak_window(unplug.by_hour)
        parts.append(f"{hour_range(hour, 2)} arası çıkarıyor "
                     f"(çıkarmaların {percent_of(n / unplug.count)})")
    text = "İnsanlar " + ", ".join(parts) + "."
    if len(parts) == 2:
        text = text.replace(", ", ", en çok ", 1)
    basis = f"{plug.count} takma, {unplug.count} çıkarma"
    if plug.excluded + unplug.excluded:
        basis += f"; {plug.excluded + unplug.excluded} belirsiz sayılmadı"
    return [Finding("arrivals", "🚗", text, basis)]


def _peak_window(by_hour: list[int], span: int = 2) -> tuple[int, int]:
    """Start hour and event count of the busiest `span`-hour window (wrapping midnight)."""
    sums = [(sum(by_hour[(h + i) % 24] for i in range(span)), -h) for h in range(24)]
    count, negative_hour = max(sums)
    return -negative_hour, count


def occupancy_time(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    d = ctx.history.durations
    if d.count < t.min_sessions:
        return [Missing("occupancy", f"Prizi işgal süresi: en az {t.min_sessions} temiz oturum "
                                     f"gerekiyor (şu an {d.count}).")]
    text = f"Bir araç prizi genelde {minutes_text(d.median_min)} işgal ediyor"
    if d.q1_min is not None and d.q1_min != d.q3_min:
        text += f" (çoğu {minutes_text(d.q1_min)} – {minutes_text(d.q3_min)})"
    text += "."
    if d.median_min >= 180:
        text += " Dolu bir prizin kısa sürede boşalmasını bekleme."
    elif d.median_min <= 60:
        text += " Prizler hızlı dönüyor; dolu bir priz kısa sürede boşalabilir."
    basis = f"{d.count} temiz oturum"
    if d.excluded:
        basis += f"; {d.excluded} boşluklu ya da belirsiz sayılmadı"
    return [Finding("occupancy", "⏱", text, basis)]


def occupancy_by_arrival(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    usable = [(b, d) for b, d in durations_by_arrival(ctx.history.sessions)
              if d.count >= t.min_bucket_sessions]
    if len(usable) < 2:
        return [Missing("occupancy_by_arrival", "Geliş saatine göre işgal süresi: iki zaman "
                                                "diliminde en az 3'er temiz oturum gerekiyor.")]
    text = "Prizde kalma, geliş saatine göre: " + " · ".join(
        f"{b.label} {minutes_text(d.median_min)}" for b, d in usable) + "."
    longest = max(usable, key=lambda bd: bd[1].median_min)
    shortest = min(usable, key=lambda bd: bd[1].median_min)
    ratio = longest[1].median_min / shortest[1].median_min
    if ratio >= 2:
        text += (f" {capitalised(longest[0].label)} gelenler, {shortest[0].label} gelenlerden "
                 f"{decimal(ratio)} kat uzun kalıyor.")
    return [Finding("occupancy_by_arrival", "🌅", text,
                    " + ".join(str(d.count) for _, d in usable) + " temiz oturum")]


def turnover(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    # A socket that is mostly faulted or offline is a reliability finding, not a quiet one.
    profiles = [p for p in ctx.history.profiles
                if p.days >= t.min_socket_days and p.unusable_share < 0.5]
    if len(profiles) < 2:
        most = max((p.days for p in ctx.history.profiles), default=0.0)
        return [Missing("turnover", f"Priz başına günlük kullanım: her priz için en az 1 günlük "
                                    f"gözlem gerekiyor (şu an {span_text(most)}).")]
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
        return [Missing("long_occupancy", f"Çok uzun işgaller: en az {t.min_sessions} temiz "
                                          f"oturum gerekiyor (şu an {len(minutes)}).")]
    hours = round(limit / 60)
    share = sum(m > limit for m in minutes) / len(minutes)
    if not share:
        text = f"{hours} saati aşan oturum görülmedi."
    else:
        text = f"Oturumların {percent_of(share)} {hours} saatten uzun."
        per_socket = sorted(
            ((sum(m > limit for m in p.clean_minutes) / len(p.clean_minutes), p)
             for p in ctx.history.profiles if len(p.clean_minutes) >= t.min_bucket_sessions),
            key=lambda sp: (-sp[0], ctx.names.of(sp[1].entry)))
        leaders = [(s, p) for s, p in per_socket if s > 0][:3]
        if leaders:
            text += " En çok: " + ", ".join(f"{ctx.names.of(p.entry)} {percent(s)}"
                                            for s, p in leaders) + "."
    basis = f"{len(minutes)} temiz oturum"
    if ctx.history.durations.excluded:
        basis += "; boşluğa denk gelen uzun oturumlar sayılamadı"
    return [Finding("long_occupancy", "🐢", text, basis)]


def reliability(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    profiles = [p for p in ctx.history.profiles if p.total >= t.min_socket_samples]
    if not profiles:
        return [Missing("reliability", "Arıza durumu: her priz için en az 3 saat gözlem "
                                       "gerekiyor.")]
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
                    f"{len(profiles)} priz · {span_text(ctx.history.coverage.days)} gözlem")]


def site_comparison(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    comparison = ctx.sites()
    others = [] if comparison is None else [l for l in comparison.loads if not l.home]
    home = None if comparison is None else next((l for l in comparison.loads if l.home), None)
    if home is None or len(others) < t.min_other_sites:
        return [Missing("sites", "Diğer sitelerle karşılaştırma: diğer Ecobox siteleri henüz "
                                 "yeterince kaydedilmedi.")]
    overall = sum(l.busy for l in others) / sum(l.total for l in others)
    ranked = sorted(comparison.loads, key=lambda l: -l.busy_share)
    rank = ranked.index(home) + 1
    text = (f"{ctx.label} AC prizlerinin doluluk oranı {percent(home.busy_share)}, diğer Ecobox "
            f"sitelerinde {percent(overall)}. {len(ranked)} site arasında yoğunlukta "
            f"{rank}. sırada.")
    if home.busy_share > 1.3 * overall:
        text += f" {ctx.label} ortalamadan belirgin yoğun."
    elif home.busy_share < overall / 1.3:
        text += f" {ctx.label} ortalamadan belirgin sakin."
    else:
        text += " Ortalamaya yakın."
    sockets = sum(l.sockets for l in others)
    return [Finding("sites", "🗺", text,
                    f"{len(others)} site, {sockets} AC priz · ortak "
                    f"{span_text(comparison.coverage.days)} gözlem")]


def week_change(ctx: InsightContext) -> list[Result]:
    t = ctx.thresholds
    this_week = ctx.full.between(ctx.now - WEEK)
    last_week = ctx.full.between(ctx.now - 2 * WEEK, ctx.now - WEEK)
    days = (this_week.coverage.days, last_week.coverage.days)
    if min(days) < t.min_week_days:
        return [Missing("week_change", f"Haftalık değişim: iki haftalık veri gerekiyor (şu an "
                                       f"{span_text(ctx.full.coverage.days)}).")]
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
                    f"son 7 gün ({span_text(days[0])} gözlem) ve önceki 7 gün "
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
