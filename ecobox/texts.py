"""Turkish user-facing text for the bot and the CLI.

Plain text only: the same string goes to Telegram, Slack and the terminal. Every reply puts
the answer first and keeps each line short (a phone shows about 34 characters per line). The
reader is addressed as "sen" everywhere, error and set-up messages included.

One vocabulary throughout: a use of a socket is a car ("araç"), its events are plugging in
and unplugging ("takma / çıkarma"), and how much data a figure rests on is "N saatlik/günlük
kayıt".
"""

from datetime import datetime, timedelta

from ecobox.clock import local_bucket, to_local
from ecobox.estimate import ChargeEstimate
from ecobox.fmt import (bar, capitalised, decimal, duration_text, hour_range, locative,
                        minutes_text, number_locative, percent, span_adjective)
from ecobox.importer import ImportResult
from ecobox.insights import Analysis, Finding, Missing, Thresholds, peak_window
from ecobox.labels import ShortNames
from ecobox.ranking import by_free_share
from ecobox.records import CatalogEntry, CurrentStatus
from ecobox.state import AVAILABLE, CHARGING, FAULTED, MAINTENANCE, OFFLINE, UNAVAILABLE
from ecobox.stats import (ConnectorShare, Coverage, DurationSummary, FreeCell, FreeGrid,
                          TimeDistribution, WaitSummary)
from ecobox.stats_view import StatsView

STATE_TR = {
    AVAILABLE: "boş",
    CHARGING: "şarjda",
    UNAVAILABLE: "kullanılamaz",
    FAULTED: "arızalı",
    OFFLINE: "çevrimdışı",
    MAINTENANCE: "bakımda",
}
WEEKDAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
WEEKDAYS_SHORT_TR = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
             "Eylül", "Ekim", "Kasım", "Aralık"]

STALE_AFTER = timedelta(minutes=10)
JUST_NOW = timedelta(minutes=1)
TOP_SHARES = 5
FREE_CHANCE_HOURS = 8
SETTLED_DAYS = 7
"""Below this many observed days the analysis says the picture is still forming."""
DIGEST_FINDINGS = 4
NO_RECORD = "henüz kayıt yok"
LOCATION_EXAMPLE = "ego"

# (command, arguments, what it answers): the help text and Telegram's "/" menu both read it.
COMMANDS = (
    ("durum", "[konum]", "şu an hangi priz boş"),
    ("haberver", "[konum]", "boşalınca haber ver (ör. {p}haberver ego)"),
    ("iptal", "", "haber vermeyi kapat"),
    ("istatistik", "[konum]", "şimdi gidersem boş bulur muyum"),
    ("analiz", "[konum]", "geçmiş: ne zaman boş, kaç saat kalıyorlar"),
    ("grafik", "[tür]", "resimli özet ({kinds})"),
    ("tahmin", "20 80", "süre, bitiş saati, ücret"),
    ("yardim", "", "bu liste"),
)
CHART_KINDS = "harita, saat, zaman, sure, site"


def _command_description(arguments: str, answer: str, prefix: str) -> str:
    answer = answer.format(p=prefix, kinds=CHART_KINDS)
    return f"{arguments} – {answer}" if arguments else answer


def help_text(prefix: str) -> str:
    """Command list; `prefix` is how the transport expects commands ("/" or "")."""
    lines = [f"{prefix}{name}{' ' if args else ' – '}"
             + _command_description(args, answer, prefix)
             for name, args, answer in COMMANDS]
    return "\n".join(["Komutlar", "", *lines, "",
                      f"Konum yazman da yeter, ör. {LOCATION_EXAMPLE}"])


def bot_commands() -> list[tuple[str, str]]:
    """(command, description) pairs for Telegram's command menu, same words as the help."""
    return [(name, _command_description(args, answer, "/")) for name, args, answer in COMMANDS]


HELP = help_text("/")

REFUSED = "Bu bot özel kullanım içindir."


def onboarding(chat_id: int) -> str:
    return (f"Bu sohbetin chat id'si: {chat_id}\n\n"
            "Botu açmak için .env dosyasına şu satırı ekle ve servisi yeniden başlat:\n"
            f"TELEGRAM_CHAT_ID={chat_id}\n\n"
            "O zamana kadar bot hiçbir komuta cevap vermez.")


def clock_text(moment: datetime) -> str:
    return to_local(moment).strftime("%H:%M")


def currency_text(code: str) -> str:
    return "TL" if code == "TRY" else code


def _command(prefix: str, name: str, term: str | None = None) -> str:
    return f"{prefix}{name}" + (f" {term}" if term else "")


# ----- durum ------------------------------------------------------------------------------

def _age(status: CurrentStatus) -> timedelta | None:
    return None if status.since is None else status.sampled_at - status.since


def _age_suffix(status: CurrentStatus) -> str:
    age = _age(status)
    if age is None:
        return ""
    if age < JUST_NOW:
        return " · az önce"
    at_least = "en az " if status.since_uncertain else ""
    return f" · {at_least}{duration_text(age)}"


def _by_age(statuses: list[CurrentStatus], names: ShortNames,
            longest_first: bool) -> list[CurrentStatus]:
    """Statuses ordered by how long they have been in this state; unknown ages go last."""
    def key(s: CurrentStatus):
        age = _age(s)
        seconds = 0.0 if age is None else age.total_seconds()
        return age is None, -seconds if longest_first else seconds, names.of(s.entry)
    return sorted(statuses, key=key)


def _free_lines(free: list[CurrentStatus], names: ShortNames) -> list[str]:
    return [f"🟢 {names.of(s.entry)}{_age_suffix(s)}"
            for s in _by_age(free, names, longest_first=False)]


def _busy_lines(busy: list[CurrentStatus], names: ShortNames) -> list[str]:
    return [f"🔴 {names.of(s.entry)}{_age_suffix(s)}"
            for s in _by_age(busy, names, longest_first=True)]


def _unavailable_lines(other: list[CurrentStatus], names: ShortNames) -> list[str]:
    return [f"⚪ {names.of(s.entry)} · {STATE_TR[s.state]}"
            for s in sorted(other, key=lambda s: names.of(s.entry))]


def _headline(free: int, total: int, label: str | None) -> str:
    where = f"{label}: " if label else ""
    if free:
        return f"🟢 {where}{free}/{total} AC priz boş"
    if label:
        return f"🔴 {label}: boş AC priz yok (0/{total})"
    return f"🔴 Boş AC priz yok (0/{total})"


def _last_read_line(newest: datetime, now: datetime) -> str:
    age = now - newest
    ago = "az önce" if age < timedelta(minutes=1) else f"{duration_text(age)} önce"
    line = f"Son okuma {clock_text(newest)} ({ago})"
    return f"⚠️ {line}, veri eski olabilir" if age > STALE_AFTER else line


def _section(title: str, lines: list[str]) -> str | None:
    return "\n".join([title, *lines]) if lines else None


def free_soon_text(wait: WaitSummary, min_days: int, prefix: str,
                   term: str | None) -> str | None:
    """"Bu saatte genelde 1 sa 10 dk içinde biri boşalıyor · /haberver"; None while fewer
    than `min_days` past days had a measurable wait at this time of day."""
    if wait.count < min_days:
        return None
    return (f"Bu saatte genelde {minutes_text(wait.median_min)} içinde biri boşalıyor · "
            f"{_command(prefix, 'haberver', term)}")


def status_text(statuses: list[CurrentStatus], now: datetime, names: ShortNames,
                label: str | None = None, free_soon: str | None = None) -> str:
    """`label` names a location the reply was narrowed to; `free_soon` is the history line
    shown when no AC socket is free (see `free_soon_text`)."""
    if not statuses:
        return "Henüz kayıtlı durum yok. Kaydedici ilk okumayı yapınca tekrar dene."
    ac = [s for s in statuses if s.entry.is_ac]
    dc = [s for s in statuses if not s.entry.is_ac]
    free = [s for s in ac if s.state == AVAILABLE]
    busy = [s for s in ac if s.state == CHARGING]
    other = [s for s in ac if s.state not in (AVAILABLE, CHARGING)]
    newest = max(s.sampled_at for s in statuses)
    head = [_headline(len(free), len(ac), label), _last_read_line(newest, now)]
    if free_soon and not free:
        head.append(free_soon)
    blocks = [
        "\n".join(head),
        _section("Boş · ne zamandır", _free_lines(free, names)),
        _section("Dolu · ne zamandır", _busy_lines(busy, names)),
        _section("Kullanılamıyor", _unavailable_lines(other, names)),
        f"⚡ DC: {sum(s.state == AVAILABLE for s in dc)}/{len(dc)} boş" if dc else None,
    ]
    return "\n\n".join(block for block in blocks if block)


# ----- haberver, iptal and other short replies ---------------------------------------------

def notification_text(entries: list[CatalogEntry], at: datetime, free: int, total: int,
                      prefix: str, names: ShortNames, term: str | None) -> str:
    """Sent once when a subscribed socket frees up; keeps the location it was set up for."""
    freed = ", ".join(names.of(e) for e in entries)
    return "\n".join([f"🟢 {freed} boşaldı · {clock_text(at)}",
                      f"Şu an {free}/{total} boş · {_command(prefix, 'durum', term)}",
                      f"Tekrar haber için: {_command(prefix, 'haberver', term)}"])


def collector_stopped(newest: datetime, now: datetime) -> str:
    """The GitHub collector wrote nothing for a while; the Mac's own record is unaffected."""
    return "\n".join([
        "⚠️ GitHub toplayıcı durmuş",
        f"Son kayıt {clock_text(newest)} ({duration_text(now - newest)} önce)",
        "Mac açıkken veri kaybı yok.",
        "Başlatmak için: gh workflow run collect.yml"])


def collector_back(newest: datetime, _now: datetime) -> str:
    return f"✅ GitHub toplayıcı yeniden çalışıyor\nSon kayıt {clock_text(newest)}"


def unknown_command(prefix: str) -> str:
    return (f"Bu komutu tanımıyorum.\nKomutlar için: {prefix}yardim\n"
            f"Bir konum yazman da yeter, ör. {LOCATION_EXAMPLE}")


def estimate_usage(prefix: str) -> str:
    return (f"Kullanım: {prefix}tahmin <başlangıç %> <hedef %> [konum]\n"
            f"ör. {prefix}tahmin 20 80")


def subscribed_text(label: str | None, free_names: list[str]) -> str:
    where = f"{locative(label)} bir priz" if label else "bir AC priz"
    reply = f"Tamam: {where} boşalınca bir kez haber vereceğim."
    if free_names:
        reply += "\nŞu an boş: " + ", ".join(free_names)
    return reply


UNSUBSCRIBED = "Tamam, artık haber vermeyeceğim."


def no_subscription(prefix: str) -> str:
    return f"Bekleyen bir haber isteğin yok. Kurmak için: {prefix}haberver"


NO_CATALOG = "Henüz istasyon listesi kaydedilmedi. Kaydedici ilk kez çalışınca tekrar dene."


def unknown_location(term: str | None, terms: list[str]) -> str:
    head = f"'{term}' diye bir konum bulamadım." if term else "Bu konumu bulamadım."
    if not terms:
        return head
    return f"{head}\nŞunlardan birini yaz: " + " · ".join(terms)


# ----- istatistik -------------------------------------------------------------------------

def _events_text(dist: TimeDistribution, t: Thresholds) -> str:
    """The same 2-hour window `analiz` names, once there are as many events as it needs."""
    if not dist.count:
        return NO_RECORD
    if dist.count < t.min_events:
        return f"henüz az kayıt ({dist.count})"
    hour, _ = peak_window(dist.by_hour)
    return f"en çok {hour_range(hour, 2)}"


def _stay_text(summary: DurationSummary, t: Thresholds) -> str:
    if not summary.count:
        return "henüz ölçülen araç yok"
    if summary.count < t.min_sessions:
        return f"henüz az araç ({summary.count})"
    text = f"genelde {minutes_text(summary.median_min)}"
    if summary.q1_min is not None and summary.q1_min != summary.q3_min:
        text += f" (çoğu {minutes_text(summary.q1_min)} – {minutes_text(summary.q3_min)})"
    return text


def _share_lines(shares: list[ConnectorShare], names: ShortNames) -> list[str]:
    ranked = by_free_share((s for s in shares if s.total), lambda s: s.share,
                           lambda s: names.of(s.entry))
    lines = [f"{bar(s.share)} {percent(s.share)} · {names.of(s.entry)}"
             for s in ranked[:TOP_SHARES]]
    return lines or [NO_RECORD]


def _pooled(grid: FreeGrid, hour: int) -> FreeCell:
    cells = [grid.cell(wd, hour) for wd in range(7)]
    return FreeCell(sum(c.free for c in cells), sum(c.total for c in cells),
                    sum(c.days for c in cells))


def _chance_rows(grid: FreeGrid, now: datetime) -> tuple[list[tuple[str, str | None]], bool]:
    """(hour label, bar line or None for no data) for the next hours, and whether any row
    borrowed the same hour of other weekdays."""
    weekday, hour = local_bucket(now)
    rows, borrowed = [], False
    for offset in range(FREE_CHANCE_HOURS):
        h = (hour + offset) % 24
        wd = (weekday + (hour + offset) // 24) % 7
        label = ("" if wd == weekday else f"{WEEKDAYS_SHORT_TR[wd]} ") + f"{h:02d}:00"
        cell, mark = grid.cell(wd, h), ""
        if not cell.total and _pooled(grid, h).total:
            cell, mark, borrowed = _pooled(grid, h), "≈", True
        line = (f"{bar(cell.probability)} {percent(cell.probability)}{mark}"
                if cell.total else None)
        rows.append((label, line))
    return rows, borrowed


def _collapsed(rows: list[tuple[str, str | None]]) -> list[str]:
    """Bars as they are; each run of hours without data on one line."""
    lines, index = [], 0
    while index < len(rows):
        label, line = rows[index]
        if line is not None:
            lines.append(f"{label} {line}")
            index += 1
            continue
        end = index
        while end + 1 < len(rows) and rows[end + 1][1] is None:
            end += 1
        if end == len(rows) - 1:
            lines.append(f"{label} sonrası: {NO_RECORD}")
        elif end == index:
            lines.append(f"{label}: {NO_RECORD}")
        else:
            lines.append(f"{label}–{rows[end + 1][0]}: {NO_RECORD}")
        index = end + 1
    return lines


def _free_now_block(grid: FreeGrid, now: datetime) -> str:
    """Bars for the next hours: that weekday's own figure, or the same hour on other days (≈)
    when the weekday has none yet."""
    rows, borrowed = _chance_rows(grid, now)
    title = f"Boş priz bulma şansı · {WEEKDAYS_TR[local_bucket(now)[0]]}"
    note = ["≈ bu gün için kayıt yok; diğer günlerin aynı saati"] if borrowed else []
    return "\n".join([title, *_collapsed(rows), *note])


def wait_text(summary: WaitSummary) -> str:
    if not summary.days_observed:
        return "Bu saat için geçmiş gün kaydı yok."
    head = (f"Geçmiş {summary.days_observed} günün {summary.days_all_busy} gününde bu saatte "
            "hepsi doluydu.")
    if not summary.count:
        return head + " Boşalma süresi ölçülebilen gün yok."
    text = f"{head} Bir priz genelde {minutes_text(summary.median_min)} içinde boşaldı"
    if summary.q1_min is not None:
        text += f" (çoğu {minutes_text(summary.q1_min)} – {minutes_text(summary.q3_min)})"
    return text + f"; {summary.count} güne dayanıyor."


def _stats_head(view: StatsView) -> str:
    head = f"📊 {view.label}"
    if view.current is None:
        return head
    if view.current.all_busy:
        return head + " · şu an hepsi dolu\n" + wait_text(view.wait)
    return head + f" · şu an {view.current.free}/{view.current.total} priz boş"


def _stats_basis(cov: Coverage, view: StatsView) -> str:
    if not cov.polls:
        return "Henüz kayıt yok."
    return (f"{capitalised(span_adjective(cov.days))} kayda dayanıyor · "
            f"{view.plug_in.count} takma, {view.unplug.count} çıkarma, "
            f"{view.durations.count} araç.")


def stats_text(view: StatsView) -> str:
    """`istatistik`: will I find a free socket if I go now, and in the next hours?"""
    t = view.thresholds
    blocks = [
        _stats_head(view),
        _free_now_block(view.grid, view.now),
        "\n".join([f"Takma: {_events_text(view.plug_in, t)}",
                   f"Çıkarma: {_events_text(view.unplug, t)}",
                   f"Prizde kalma: {_stay_text(view.durations, t)}"]),
        "\n".join(["En sık boş olanlar", *_share_lines(view.shares, view.names)]),
        _stats_basis(view.coverage, view),
    ]
    return "\n\n".join(blocks)


# ----- analiz, haftalık özet ---------------------------------------------------------------

def _analysis_head(analysis: Analysis) -> str:
    cov = analysis.coverage
    if not cov.polls:
        return f"🔎 {analysis.label}\nHenüz kayıt yok. Kaydedici veri topladıkça analiz dolacak."
    head = (f"🔎 {analysis.label} · {analysis.sockets} AC priz\n"
            f"{capitalised(span_adjective(cov.days))} kayda dayanıyor.")
    if cov.days < SETTLED_DAYS:
        head += " Kayıt biriktikçe yorumlar netleşecek."
    return head


def _finding_block(finding: Finding) -> str:
    return f"{finding.emoji} {finding.text}\n↳ {finding.basis}"


def missing_text(missing: list[Missing]) -> str:
    """"⏳ 5 araç dolunca (şu an 3): prizde kalma süreleri. 1 günlük kayıt dolunca: …":
    what is still missing, grouped by the data it waits for."""
    groups: dict[str, list[Missing]] = {}
    notes: list[str] = []
    for item in missing:
        if item.needs is None:
            notes.append(item.topic)
        else:
            groups.setdefault(item.needs, []).append(item)
    sentences = []
    for needs, items in groups.items():
        current = next((m.current for m in items if m.current is not None), None)
        now = f" (şu an {current})" if current is not None else ""
        topics = list(dict.fromkeys(m.topic for m in items))
        sentences.append(f"{capitalised(needs)} dolunca{now}: {', '.join(topics)}.")
    return "⏳ " + " ".join(sentences + notes)


def analysis_body(analysis: Analysis) -> list[str]:
    blocks = [_finding_block(f) for f in analysis.findings]
    if analysis.missing:
        blocks.append(missing_text(analysis.missing))
    return blocks


def analysis_text(analysis: Analysis) -> str:
    if not analysis.coverage.polls:
        return _analysis_head(analysis)
    return "\n\n".join([_analysis_head(analysis), *analysis_body(analysis)])


def _date_text(moment: datetime) -> str:
    local = to_local(moment)
    return f"{local.day} {MONTHS_TR[local.month - 1]}"


def _date_range(first: datetime, last: datetime) -> str:
    """"6–12 Kasım"; "30 Ekim – 5 Kasım" across months."""
    a, b = to_local(first), to_local(last)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {MONTHS_TR[b.month - 1]}"
    return f"{_date_text(first)} – {_date_text(last)}"


def digest_findings(findings: list[Finding]) -> list[Finding]:
    """The week's change first, then the rest in rule order; at most `DIGEST_FINDINGS`."""
    ordered = sorted(findings, key=lambda f: f.key != "week_change")
    return ordered[:DIGEST_FINDINGS]


def digest_text(analysis: Analysis, since: datetime, now: datetime) -> str:
    """The weekly digest: no "still missing" block, which would repeat every week."""
    head = (f"📬 Haftalık özet · {_date_range(since, now - timedelta(seconds=1))}\n"
            f"{analysis.label} · {analysis.sockets} AC priz · "
            f"{span_adjective(analysis.coverage.days)} kayıt")
    return "\n\n".join([head, *map(_finding_block, digest_findings(analysis.findings))])


# ----- grafik -----------------------------------------------------------------------------

CHARTS_UNAVAILABLE = ("Grafik çizilemiyor: matplotlib kurulu değil. Kurmak için "
                      "scripts/setup_venv.sh çalıştır ve servisi .venv ile yeniden başlat "
                      "(scripts/install_launchd.sh). Metin komutları çalışmaya devam ediyor.")


def image_failed(missing_scope: bool) -> str:
    if missing_scope:
        return ("Grafik gönderilemedi: Slack uygulamasına files:write izni eklenmeli "
                "(README, Slack botunu kurma).")
    return "Grafik gönderilemedi; biraz sonra tekrar dene."


def chart_usage(prefix: str, kinds: list[tuple[str, str]]) -> str:
    lines = [f"{key} – {description}" + (" (varsayılan)" if index == 0 else "")
             for index, (key, description) in enumerate(kinds)]
    return "\n".join([f"Kullanım: {prefix}grafik [tür] [konum]", f"ör. {prefix}grafik saat ego",
                      "", "Türler", *lines])


def other_charts_line(prefix: str, keys: list[str]) -> str:
    return f"Diğer: {prefix}grafik " + " · ".join(keys)


# ----- tahmin -----------------------------------------------------------------------------

def _locative(moment: datetime) -> str:
    """'te/'de/'da/'ta by the last number word of the time read aloud (23:25 -> 'te)."""
    local = to_local(moment)
    return number_locative(local.minute or local.hour)  # 17:00 is read "on yedi": the hour


def _day_word(moment: datetime, now: datetime) -> str:
    days = (to_local(moment).date() - to_local(now).date()).days
    if days == 0:
        return "bugün"
    if days == 1:
        return "yarın"
    return WEEKDAYS_TR[to_local(moment).weekday()]


def _finish_text(finish_at: datetime, now: datetime) -> str:
    return f"{_day_word(finish_at, now)} {clock_text(finish_at)}{_locative(finish_at)} hazır"


def estimate_text(estimate: ChargeEstimate, now: datetime) -> str:
    lines = [f"🔋 %{estimate.start_pct:g} → %{estimate.target_pct:g}",
             f"⏱ {duration_text(estimate.duration)} · {_finish_text(estimate.finish_at, now)}"]
    if estimate.cost is None:
        lines.append("💰 tarife bilinmiyor")
    else:
        lines.append(f"💰 ~{round(estimate.cost)} {currency_text(estimate.currency)}")
    lines.append(f"⚡ {decimal(estimate.energy_kwh)} kWh, {decimal(estimate.effective_kw)} kW ile")
    if estimate.service_price:
        # Not added in: the API does not say whether it is per kWh or per session.
        lines.append(f"Not: {decimal(estimate.service_price)} "
                     f"{currency_text(estimate.currency)} hizmet bedeli dahil değil.")
    return "\n".join(lines)


# ----- uzak kaynaktan içe aktarma ----------------------------------------------------------

SNAPSHOT_IMPORT_DISABLED = ("ECOBOX_SNAPSHOT_REPO_URL boş: GitHub görüntüleri içe aktarılmıyor. "
                            "Açmak için .env'e depo adresini yaz ya da --dir ile bir klasör "
                            "ver.")
SNAPSHOT_IMPORT_NO_CATALOG = ("Katalog boş: önce `poll-once` ya da `run` ile istasyon listesini "
                              "çek. Görüntüler bir sonraki çalıştırmada alınır.")


def _clock_date(moment: datetime) -> str:
    local = to_local(moment)
    return f"{_date_text(moment)} {local:%H:%M}"


def _coverage_line(label: str, cov: Coverage) -> str:
    if not cov.polls:
        return f"· {label}: kayıt yok"
    return (f"· {label}: {cov.polls} okuma, {span_adjective(cov.days)} kayıt "
            f"({_clock_date(cov.first)} – {_clock_date(cov.last)})")


def snapshot_import_text(result: ImportResult, local: Coverage, remote: Coverage) -> str:
    lines = [
        f"📥 GitHub görüntüleri: {result.read} okundu, {result.used} kullanıldı "
        f"({result.samples} örnek)",
        f"Atlanan: {result.skipped_local} yerel kayıt varken, "
        f"{result.skipped_imported} zaten alınmış, {result.skipped_empty} kayıtlı priz içermiyor; "
        f"{result.deferred} henüz çok yeni (sonraki çalıştırmada)",
    ]
    if result.first_used is not None:
        lines.append(f"Doldurulan aralık: {_clock_date(result.first_used)} – "
                     f"{_clock_date(result.last_used)}")
    if result.unknown_connectors:
        lines.append(f"Yerel katalogda olmayan {len(result.unknown_connectors)} priz atlandı.")
    lines += ["Kapsam:", _coverage_line("bu bilgisayar", local),
              _coverage_line("GitHub", remote)]
    return "\n".join(lines)
