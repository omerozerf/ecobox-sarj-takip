"""Turkish user-facing text for the bot and the CLI."""

from datetime import datetime, timedelta
from itertools import groupby

from ecobox.clock import local_bucket, to_local
from ecobox.estimate import ChargeEstimate
from ecobox.records import CatalogEntry, CurrentStatus
from ecobox.state import (AVAILABLE, CHARGING, FAULTED, MAINTENANCE, OFFLINE, UNAVAILABLE)
from ecobox.stats import (ConnectorShare, DurationSummary, FreeGrid, TimeDistribution,
                          WaitSummary)

STATE_TR = {
    AVAILABLE: "BOŞ",
    CHARGING: "şarjda",
    UNAVAILABLE: "kullanılamaz",
    FAULTED: "arızalı",
    OFFLINE: "çevrimdışı",
    MAINTENANCE: "bakımda",
}
WEEKDAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

def help_text(prefix: str) -> str:
    """Command list; `prefix` is how the transport expects commands ("/" or "")."""
    p = prefix
    return f"""Ecobox Hacettepe şarj takip botu

{p}durum – tüm prizlerin anlık durumu
{p}haberver [konum] – bir AC priz boşalınca bir kez haber ver (ör. {p}haberver ego, {p}haberver f blok)
{p}iptal – haber verme aboneliğini iptal et
{p}istatistik [konum] – takılma/çıkarma saatleri, süreler, boş olma olasılığı
{p}tahmin <başlangıç %> <hedef %> [konum] – şarj süresi, bitiş saati ve ücret (ör. {p}tahmin 20 80)
{p}yardim – bu mesaj

Saatler İstanbul saatidir. İstatistikler kaç gözleme dayandığını yazar; veri azken temkinli okuyun."""


HELP = help_text("/")

REFUSED = "Bu bot özel kullanım içindir."


def onboarding(chat_id: int) -> str:
    return (f"Bu sohbetin chat id'si: {chat_id}\n\n"
            "Botu kullanmak için proje klasöründeki .env dosyasına şu satırı ekleyin ve "
            f"servisi yeniden başlatın:\nTELEGRAM_CHAT_ID={chat_id}\n\n"
            "Chat id ayarlanana kadar bot başka hiçbir komuta cevap vermez.")


def duration_text(value: timedelta) -> str:
    minutes = max(0, round(value.total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} sa {minutes} dk"
    return f"{hours} sa" if hours else f"{minutes} dk"


def minutes_text(value: float) -> str:
    return duration_text(timedelta(minutes=value))


def clock_text(moment: datetime) -> str:
    return to_local(moment).strftime("%H:%M")


def kind_text(status: CurrentStatus) -> str:
    return f"{'AC' if status.entry.is_ac else 'DC'} {status.entry.power_kw:g} kW"


def status_text(statuses: list[CurrentStatus], now: datetime) -> str:
    if not statuses:
        return "Henüz kayıtlı durum yok. Kaydedici ilk okumayı yapınca tekrar deneyin."
    newest = max(s.sampled_at for s in statuses)
    lines = [f"Son okuma: {clock_text(newest)} ({duration_text(now - newest)} önce)"]
    ordered = sorted(statuses, key=lambda s: (s.entry.location_name, s.entry.display_name,
                                              s.entry.connector_order))
    for location, group in groupby(ordered, key=lambda s: s.entry.location_name):
        lines.append("")
        lines.append(location)
        for s in group:
            since = ""
            if s.since is not None:
                prefix = "en az " if s.since_uncertain else ""
                since = f" ({prefix}{duration_text(s.sampled_at - s.since)})"
            lines.append(f"  {s.entry.label} [{kind_text(s)}]: {STATE_TR[s.state]}{since}")
    ac = [s for s in statuses if s.entry.is_ac]
    free = [s for s in ac if s.state == AVAILABLE]
    lines.append("")
    lines.append(f"Boş AC priz: {len(free)}/{len(ac)}")
    return "\n".join(lines)


def notification_text(entries: list[CatalogEntry], prefix: str) -> str:
    names = ", ".join(f"{e.label} ({e.location_name})" for e in entries)
    return (f"Boşaldı: {names}\n"
            f"Abonelik bir kez çalışır ve şimdi kapandı; tekrar için {prefix}haberver.")


def unknown_command(prefix: str) -> str:
    return "Bu komutu tanımıyorum.\n\n" + help_text(prefix)


def estimate_usage(prefix: str) -> str:
    return (f"Kullanım: {prefix}tahmin <başlangıç %> <hedef %> [konum], "
            f"ör. {prefix}tahmin 20 80")


def _peaks(dist: TimeDistribution) -> str:
    if not dist.count:
        return "veri yok"
    peaks = ", ".join(f"{h:02d}:00 ({n})" for h, n in dist.peak_hours())
    return f"{dist.count} olay; en sık: {peaks}"


def _excluded(n: int, noun: str = "olay") -> str:
    return f" [{n} belirsiz {noun} hariç]" if n else ""


def durations_text(summary: DurationSummary) -> str:
    if not summary.count:
        return f"veri yok{_excluded(summary.excluded, 'oturum')}"
    text = f"medyan {minutes_text(summary.median_min)} ({summary.count} oturum"
    if summary.q1_min is not None:
        text += f"; çeyrekler {minutes_text(summary.q1_min)} – {minutes_text(summary.q3_min)}"
    return text + ")" + _excluded(summary.excluded, "oturum")


def share_text(shares: list[ConnectorShare]) -> list[str]:
    ranked = sorted((s for s in shares if s.total), key=lambda s: s.share, reverse=True)
    return [f"  {s.entry.label}: %{100 * s.share:.0f} boş ({s.total} gözlem)" for s in ranked]


def free_now_text(grid: FreeGrid, now: datetime) -> str:
    weekday, hour = local_bucket(now)
    lines = []
    for offset in range(4):
        h = (hour + offset) % 24
        wd = (weekday + (hour + offset) // 24) % 7
        cell = grid.cell(wd, h)
        if cell.total:
            lines.append(f"  {WEEKDAYS_TR[wd]} {h:02d}:00: %{100 * cell.probability:.0f} "
                         f"({cell.total} gözlem, {cell.days} gün)")
        else:
            lines.append(f"  {WEEKDAYS_TR[wd]} {h:02d}:00: henüz gözlem yok")
    return "\n".join(lines)


def wait_text(summary: WaitSummary) -> str:
    if not summary.days_observed:
        return "Bu saat için geçmiş gün gözlemi yok."
    head = (f"Geçmiş {summary.days_observed} günün {summary.days_all_busy} gününde bu saatte "
            "hepsi doluydu.")
    if not summary.count:
        return head + " Boşalma süresi ölçülebilen gün yok."
    text = f"{head} Bir priz genelde {minutes_text(summary.median_min)} içinde boşaldı"
    if summary.q1_min is not None:
        text += f" (çeyrekler {minutes_text(summary.q1_min)} – {minutes_text(summary.q3_min)})"
    return text + f"; {summary.count} güne dayanıyor."


def stats_text(scope_label: str, polls: tuple[int, int], plug_in: TimeDistribution,
               unplug: TimeDistribution, durations: DurationSummary,
               shares: list[ConnectorShare], grid: FreeGrid, now: datetime,
               wait: WaitSummary | None) -> str:
    ok, failed = polls
    lines = [f"İstatistik: {scope_label}",
             f"Kayıt: {ok} başarılı okuma, {failed} hata",
             f"Takılma saati: {_peaks(plug_in)}{_excluded(plug_in.excluded)}",
             f"Çıkarma saati: {_peaks(unplug)}{_excluded(unplug.excluded)}",
             f"Şarj süresi: {durations_text(durations)}",
             "Boş kalma oranı:"]
    lines += share_text(shares) or ["  veri yok"]
    lines.append("En az bir AC prizin boş olma olasılığı:")
    lines.append(free_now_text(grid, now))
    if wait is not None:
        lines.append("Şu an hepsi dolu. " + wait_text(wait))
    return "\n".join(lines)


def estimate_text(estimate: ChargeEstimate, connector_label: str | None,
                  price_includes_vat: bool) -> str:
    lines = [f"Şarj tahmini %{estimate.start_pct:g} → %{estimate.target_pct:g}",
             f"Enerji: {estimate.energy_kwh:.1f} kWh, {estimate.effective_kw:g} kW ile",
             f"Süre: {duration_text(estimate.duration)}",
             f"Bitiş: {clock_text(estimate.finish_at)}"]
    if estimate.cost is None:
        lines.append("Ücret: tarife bilinmiyor")
    else:
        vat = "KDV dahil varsayıldı" if price_includes_vat else "KDV eklendi"
        lines.append(f"Ücret: ~{estimate.cost:.2f} {estimate.currency} "
                     f"({connector_label} tarifesi, {vat})")
        if estimate.service_price:
            lines.append(f"Not: tarifede ayrıca {estimate.service_price:g} {estimate.currency} "
                         "hizmet bedeli var; birimi API'de belirtilmiyor, hesaba katılmadı.")
    return "\n".join(lines)
