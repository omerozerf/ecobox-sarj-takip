"""Turkish user-facing text for the bot and the CLI.

Plain text only: the same string goes to Telegram, Slack and the terminal. Every reply puts
the answer first and keeps each line short.
"""

from datetime import datetime, timedelta

from ecobox.clock import local_bucket, to_local
from ecobox.estimate import ChargeEstimate
from ecobox.fmt import (bar, capitalised, duration_text, minutes_text,  # noqa: F401
                        percent, span_adjective, span_text)
from ecobox.importer import ImportResult
from ecobox.insights import Analysis
from ecobox.labels import ShortNames
from ecobox.records import CatalogEntry, CurrentStatus
from ecobox.state import AVAILABLE, CHARGING, FAULTED, MAINTENANCE, OFFLINE, UNAVAILABLE
from ecobox.stats import (ConnectorShare, Coverage, DurationSummary, FreeCell, FreeGrid,
                          TimeDistribution, WaitSummary)

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

# Locative suffix (-de/-da/-te/-ta) of the last number word when a clock time is read aloud:
# 23:25 is "yirmi üç yirmi beş", so it takes the suffix of "beş": "23:25'te".
_LOCATIVE_BY_UNIT = {1: "de", 2: "de", 3: "te", 4: "te", 5: "te", 6: "da", 7: "de", 8: "de",
                     9: "da"}
_LOCATIVE_BY_TENS = {0: "da", 1: "da", 2: "de", 3: "da", 4: "ta", 5: "de"}  # sıfır, on, yirmi...


def help_text(prefix: str) -> str:
    """Command list; `prefix` is how the transport expects commands ("/" or "")."""
    p = prefix
    return f"""Komutlar

{p}durum – hangi priz boş
{p}haberver [konum] – boşalınca haber ver (ör. {p}haberver ego)
{p}iptal – haber vermeyi kapat
{p}istatistik [konum] – takma ve alma saatleri
{p}analiz [konum] – prizler nasıl kullanılıyor, yorumlu
{p}grafik [tür] [konum] – grafik; tür: isi, saat, zaman, sure, site
{p}tahmin 20 80 – süre, bitiş saati, ücret
{p}yardim – bu liste"""


HELP = help_text("/")

REFUSED = "Bu bot özel kullanım içindir."


def onboarding(chat_id: int) -> str:
    return (f"Bu sohbetin chat id'si: {chat_id}\n\n"
            "Botu açmak için .env dosyasına şu satırı ekleyip servisi yeniden başlatın:\n"
            f"TELEGRAM_CHAT_ID={chat_id}\n\n"
            "O zamana kadar bot hiçbir komuta cevap vermez.")


def clock_text(moment: datetime) -> str:
    return to_local(moment).strftime("%H:%M")


def currency_text(code: str) -> str:
    return "TL" if code == "TRY" else code


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


def _headline(free: int, total: int) -> str:
    if free:
        return f"🟢 {free}/{total} AC priz boş"
    return f"🔴 Boş AC priz yok ({free}/{total})"


def _last_read_line(newest: datetime, now: datetime) -> str:
    age = now - newest
    line = f"Son okuma {clock_text(newest)} ({duration_text(age)} önce)"
    return f"⚠️ {line}, veri eski olabilir" if age > STALE_AFTER else line


def _section(title: str, lines: list[str]) -> str | None:
    return "\n".join([title, *lines]) if lines else None


def status_text(statuses: list[CurrentStatus], now: datetime, names: ShortNames) -> str:
    if not statuses:
        return "Henüz kayıtlı durum yok. Kaydedici ilk okumayı yapınca tekrar deneyin."
    ac = [s for s in statuses if s.entry.is_ac]
    dc = [s for s in statuses if not s.entry.is_ac]
    free = [s for s in ac if s.state == AVAILABLE]
    busy = [s for s in ac if s.state == CHARGING]
    other = [s for s in ac if s.state not in (AVAILABLE, CHARGING)]
    newest = max(s.sampled_at for s in statuses)
    blocks = [
        _headline(len(free), len(ac)) + "\n" + _last_read_line(newest, now),
        _section("Boş", _free_lines(free, names)),
        _section("Dolu", _busy_lines(busy, names)),
        _section("Kullanılamıyor", _unavailable_lines(other, names)),
        f"⚡ DC: {sum(s.state == AVAILABLE for s in dc)}/{len(dc)} boş" if dc else None,
    ]
    return "\n\n".join(block for block in blocks if block)


# ----- haberver and other short replies ----------------------------------------------------

def notification_text(entries: list[CatalogEntry], prefix: str, names: ShortNames) -> str:
    freed = ", ".join(names.of(e) for e in entries)
    return f"🟢 Boşaldı: {freed}\nTekrar haber için: {prefix}haberver"


def unknown_command(prefix: str) -> str:
    return "Bu komutu tanımıyorum.\n\n" + help_text(prefix)


def estimate_usage(prefix: str) -> str:
    return (f"Kullanım: {prefix}tahmin <başlangıç %> <hedef %> [konum]\n"
            f"ör. {prefix}tahmin 20 80")


def subscribed_text(term: str | None, free_names: list[str]) -> str:
    where = f"{term} konumunda " if term else ""
    reply = f"Tamam, {where}bir AC priz boşalınca bir kez haber vereceğim."
    if free_names:
        reply += "\nŞu an boş: " + ", ".join(free_names)
    return reply


def no_match_text(term: str | None, locations: list[str]) -> str:
    return f"'{term}' ile eşleşen AC priz yok. Konumlar:\n" + "\n".join(locations)


# ----- istatistik -------------------------------------------------------------------------

def _peak_hours_text(dist: TimeDistribution) -> str:
    if not dist.count:
        return "veri yok"
    return "En çok " + ", ".join(f"{h:02d}:00" for h, _ in dist.peak_hours())


def _stay_text(summary: DurationSummary) -> str:
    if not summary.count:
        return "veri yok"
    text = f"Genelde {minutes_text(summary.median_min)}"
    if summary.q1_min is not None and summary.q1_min != summary.q3_min:
        text += f" (çoğu {minutes_text(summary.q1_min)} – {minutes_text(summary.q3_min)})"
    return text


def _share_lines(shares: list[ConnectorShare], names: ShortNames) -> list[str]:
    ranked = sorted((s for s in shares if s.total), key=lambda s: s.share, reverse=True)
    lines = [f"{bar(s.share)} {percent(s.share)} · {names.of(s.entry)}"
             for s in ranked[:TOP_SHARES]]
    return lines or ["veri yok"]


def _pooled(grid: FreeGrid, hour: int) -> FreeCell:
    cells = [grid.cell(wd, hour) for wd in range(7)]
    return FreeCell(sum(c.free for c in cells), sum(c.total for c in cells),
                    sum(c.days for c in cells))


def _free_now_block(grid: FreeGrid, now: datetime) -> str:
    """Bars for the next hours: that weekday's own figure, or the same hour on other days (≈)
    when the weekday has none yet."""
    weekday, hour = local_bucket(now)
    lines, borrowed = [], False
    for offset in range(FREE_CHANCE_HOURS):
        h = (hour + offset) % 24
        wd = (weekday + (hour + offset) // 24) % 7
        day = "" if wd == weekday else f"{WEEKDAYS_SHORT_TR[wd]} "
        cell, mark = grid.cell(wd, h), ""
        if not cell.total and _pooled(grid, h).total:
            cell, mark, borrowed = _pooled(grid, h), "≈", True
        if cell.total:
            lines.append(f"{day}{h:02d}:00 {bar(cell.probability)} "
                         f"{percent(cell.probability)}{mark}")
        else:
            lines.append(f"{day}{h:02d}:00 {bar(None)} veri yok")
    title = f"Boş priz bulma şansı · {WEEKDAYS_TR[weekday]}"
    note = ["≈ bu gün için veri yok; diğer günlerin aynı saati"] if borrowed else []
    return "\n".join([title, *lines, *note])


def wait_text(summary: WaitSummary) -> str:
    if not summary.days_observed:
        return "Bu saat için geçmiş gün gözlemi yok."
    head = (f"Geçmiş {summary.days_observed} günün {summary.days_all_busy} gününde bu saatte "
            "hepsi doluydu.")
    if not summary.count:
        return head + " Boşalma süresi ölçülebilen gün yok."
    text = f"{head} Bir priz genelde {minutes_text(summary.median_min)} içinde boşaldı"
    if summary.q1_min is not None:
        text += f" (çoğu {minutes_text(summary.q1_min)} – {minutes_text(summary.q3_min)})"
    return text + f"; {summary.count} güne dayanıyor."


def _basis_text(polls: tuple[int, int], plug_in: TimeDistribution, unplug: TimeDistribution,
                durations: DurationSummary) -> str:
    ok, failed = polls
    parts = [f"{ok} okuma", f"{plug_in.count} takma", f"{unplug.count} alma",
             f"{durations.count} oturum"]
    if failed:
        parts.append(f"{failed} hata")
    text = "Dayanak: " + ", ".join(parts)
    uncertain = plug_in.excluded + unplug.excluded + durations.excluded
    if uncertain:
        text += f"; {uncertain} belirsiz kayıt sayılmadı"
    return text + "."


def stats_text(scope_label: str, polls: tuple[int, int], plug_in: TimeDistribution,
               unplug: TimeDistribution, durations: DurationSummary,
               shares: list[ConnectorShare], grid: FreeGrid, now: datetime,
               wait: WaitSummary | None, names: ShortNames) -> str:
    blocks = [
        f"📊 {capitalised(scope_label)}",
        "\n".join([f"Ne zaman takıyorlar? {_peak_hours_text(plug_in)}",
                   f"Ne zaman alıyorlar? {_peak_hours_text(unplug)}",
                   f"Ne kadar kalıyorlar? {_stay_text(durations)}"]),
        _free_now_block(grid, now),
        "\n".join(["En sık boş olanlar", *_share_lines(shares, names)]),
        None if wait is None else "Şu an hepsi dolu. " + wait_text(wait),
        _basis_text(polls, plug_in, unplug, durations),
    ]
    return "\n\n".join(block for block in blocks if block)


# ----- analiz, grafik, haftalık özet -----------------------------------------------------

def _analysis_head(analysis: Analysis) -> str:
    cov = analysis.coverage
    if not cov.polls:
        return f"🔎 {analysis.label}\nHenüz kayıt yok. Kaydedici veri topladıkça analiz dolacak."
    head = (f"🔎 {analysis.label} · {analysis.sockets} AC priz\n"
            f"{span_adjective(cov.days)} veriye dayanıyor ({cov.polls} okuma).")
    if cov.days < SETTLED_DAYS:
        head += " Veri biriktikçe yorumlar netleşecek."
    return head


def analysis_body(analysis: Analysis) -> list[str]:
    blocks = [f"{f.emoji} {f.text}\n↳ {f.basis}" for f in analysis.findings]
    if analysis.missing:
        blocks.append("\n".join(["⏳ Henüz erken:", *(f"· {m.text}" for m in analysis.missing)]))
    return blocks


def analysis_text(analysis: Analysis) -> str:
    if not analysis.coverage.polls:
        return _analysis_head(analysis)
    return "\n\n".join([_analysis_head(analysis), *analysis_body(analysis)])


def _date_text(moment: datetime) -> str:
    local = to_local(moment)
    return f"{local.day} {MONTHS_TR[local.month - 1]}"


def digest_text(analysis: Analysis, since: datetime, now: datetime) -> str:
    last_day = now - timedelta(seconds=1)
    head = (f"📬 Haftalık özet · {_date_text(since)} – {_date_text(last_day)}\n"
            f"{analysis.label} · {analysis.sockets} AC priz · "
            f"{span_text(analysis.coverage.days)} gözlem")
    return "\n\n".join([head, *analysis_body(analysis)])


CHARTS_UNAVAILABLE = ("Grafik çizilemiyor: matplotlib kurulu değil. Kurmak için "
                      "scripts/setup_venv.sh çalıştırın ve servisi .venv ile yeniden başlatın "
                      "(scripts/install_launchd.sh). Metin komutları çalışmaya devam ediyor.")


def image_failed(missing_scope: bool) -> str:
    if missing_scope:
        return ("Grafik gönderilemedi: Slack uygulamasına files:write izni eklenmeli "
                "(README, Slack botunu kurma).")
    return "Grafik gönderilemedi; biraz sonra tekrar deneyin."


def chart_usage(prefix: str, kinds: list[tuple[str, str]]) -> str:
    lines = [f"{key} – {description}" + (" (varsayılan)" if index == 0 else "")
             for index, (key, description) in enumerate(kinds)]
    return "\n".join([f"Kullanım: {prefix}grafik [tür] [konum]", f"ör. {prefix}grafik saat ego",
                      "", "Türler", *lines])


# ----- tahmin -----------------------------------------------------------------------------

def _locative(moment: datetime) -> str:
    """'te/'de/'da/'ta by the last number word of the time read aloud (23:25 -> 'te)."""
    local = to_local(moment)
    number = local.minute or local.hour  # 17:00 is read "on yedi": the hour
    suffix = _LOCATIVE_BY_UNIT[number % 10] if number % 10 else _LOCATIVE_BY_TENS[number // 10]
    return "'" + suffix


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
    lines.append(f"⚡ {estimate.energy_kwh:.1f} kWh, {estimate.effective_kw:g} kW ile")
    if estimate.service_price:
        lines.append(f"Not: {estimate.service_price:g} {currency_text(estimate.currency)} "
                     "hizmet bedeli hesaba katılmadı.")
    return "\n".join(lines)


# ----- uzak kaynaktan içe aktarma ----------------------------------------------------------

SNAPSHOT_IMPORT_DISABLED = ("ECOBOX_SNAPSHOT_REPO_URL boş: GitHub görüntüleri içe aktarılmıyor. "
                            "Açmak için .env'e depo adresini yazın ya da --dir ile bir klasör "
                            "verin.")
SNAPSHOT_IMPORT_NO_CATALOG = ("Katalog boş: önce `poll-once` ya da `run` ile istasyon listesini "
                              "çekin. Görüntüler bir sonraki çalıştırmada alınır.")


def _clock_date(moment: datetime) -> str:
    local = to_local(moment)
    return f"{_date_text(moment)} {local:%H:%M}"


def _coverage_line(label: str, cov: Coverage) -> str:
    if not cov.polls:
        return f"· {label}: kayıt yok"
    return (f"· {label}: {cov.polls} okuma, {span_text(cov.days)} gözlem "
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
