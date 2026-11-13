"""Command line entry points: `python3 -m ecobox <command>`."""

import argparse
import logging
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from ecobox import texts
from ecobox.api import HttpEcoboxApi
from ecobox.bot import TelegramBot, TelegramOutbox
from ecobox.charts import ChartBook
from ecobox.commands import SLACK, TELEGRAM, CommandHandler, Conversation
from ecobox.clock import SystemClock
from ecobox.config import Config
from ecobox.digest import DigestComposer, DigestSchedule, DigestTarget, WeeklyDigest
from ecobox.estimate import ChargeEstimator
from ecobox.importer import SnapshotImporter, SnapshotImportJob
from ecobox.insights import InsightEngine
from ecobox.recorder import Recorder
from ecobox.scope import ScopeFilter
from ecobox.notifier import Outbox, SubscriptionNotifier
from ecobox.service import (Backoff, BotLoop, PeriodicLoop, RecorderLoop, SlackLoop,
                            StoppableLoop, run_service)
from ecobox.slack import HttpSlackClient
from ecobox.slack_bot import STATE_CHANNEL_ID, SlackBot, SlackOutbox
from ecobox.snapshot_source import (DirectorySource, GitSnapshotSource, SnapshotSource,
                                    run_git)
from ecobox.replies import Reply
from ecobox.reports import stats_report
from ecobox.stats import Statistics, coverage
from ecobox.storage import POLL_SNAPSHOT, POLL_STATUS, Storage
from ecobox.telegram import HttpTelegramClient
from ecobox.view import DisplayScope

DIGEST_CHECK_S = 60

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m ecobox",
        description="Ecobox şarj istasyonu durum kaydedici, istatistik, "
                    "Telegram ve Slack botu.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run",
                   help="kaydediciyi (ve token varsa Telegram/Slack botunu) sürekli çalıştır")

    poll = sub.add_parser("poll-once", help="istasyon listesini ve durumu bir kez çek, yazdır")
    poll.add_argument("--no-store", action="store_true",
                      help="veritabanına yazma (geçici bellek içi DB kullan)")

    sub.add_parser("status", help="veritabanındaki son durumu yazdır (ağ isteği yok)")

    stats = sub.add_parser("stats", help="istatistikleri yazdır (ağ isteği yok)")
    stats.add_argument("--location", help="konum filtresi, ör. ego, 'f blok'")
    stats.add_argument("--connector", help="tek bir connector id")

    est = sub.add_parser("estimate", help="şarj süresi/ücret tahmini (ağ isteği yok)")
    est.add_argument("start", type=float, help="başlangıç yüzdesi")
    est.add_argument("target", type=float, help="hedef yüzdesi")
    est.add_argument("--location", help="tarifesi kullanılacak konum")

    insights = sub.add_parser("insights", help="yorumlu analiz (botun `analiz` cevabı)")
    insights.add_argument("--location", help="konum filtresi; kapsam dışı siteler de olur")

    charts = sub.add_parser("charts", help="grafikleri PNG olarak yaz (matplotlib gerekir)")
    charts.add_argument("--out", required=True, help="PNG'lerin yazılacağı klasör")
    charts.add_argument("--kind", help="tek bir tür: isi, saat, zaman, sure, site")
    charts.add_argument("--location", help="konum filtresi")

    snapshots = sub.add_parser(
        "import-snapshots",
        help="GitHub'daki durum görüntülerini, bu bilgisayarın kaydı olmayan aralıklara ekle")
    snapshots.add_argument("--dir", help="git yerine bu klasördeki görüntüleri kullan "
                                         "(connectors.json ve gün dosyaları)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load()
    logging.basicConfig(level=config.log_level,
                        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    in_memory = args.command == "poll-once" and args.no_store
    storage = Storage(":memory:" if in_memory else config.db_path)
    try:
        return COMMANDS[args.command](args, config, storage)
    finally:
        storage.close()


def _recorder(config: Config, storage: Storage, clock: SystemClock) -> Recorder:
    """Records the record scope (every site by default), not the display scope."""
    return Recorder(HttpEcoboxApi(config.http_timeout_s), storage,
                    ScopeFilter(config.record_scope_terms), clock, config.gap_threshold_s)


@dataclass(frozen=True)
class Analysis:
    """The read-side services every front-end shares."""
    scope: DisplayScope
    statistics: Statistics
    insights: InsightEngine
    charts: ChartBook


def _analysis(config: Config, storage: Storage) -> Analysis:
    statistics = _statistics(config, storage)
    return Analysis(DisplayScope(storage, config.scope_terms), statistics,
                    InsightEngine(statistics), ChartBook(statistics))


def _commands(config: Config, storage: Storage, clock: SystemClock,
              analysis: Analysis) -> CommandHandler:
    return CommandHandler(storage, analysis.statistics, _estimator(config), clock,
                          analysis.scope, analysis.insights, analysis.charts)


def _statistics(config: Config, storage: Storage) -> Statistics:
    return Statistics(storage, config.gap_threshold_s)


def _snapshot_job(config: Config, storage: Storage, clock: SystemClock,
                  source: SnapshotSource) -> SnapshotImportJob:
    return SnapshotImportJob(source, SnapshotImporter(storage, clock, config.poll_interval_s,
                                                      config.gap_threshold_s))


def _git_source(config: Config) -> SnapshotSource | None:
    """The remote collector's checkout, or None while the repository URL is empty."""
    if config.snapshot_repo_url is None:
        return None
    return GitSnapshotSource(config.snapshot_repo_url, config.snapshot_branch,
                             config.snapshot_checkout, run_git)


def _estimator(config: Config) -> ChargeEstimator:
    return ChargeEstimator(config.car_charge_kw, config.kwh_per_percent,
                           config.price_includes_vat)


def cmd_run(_args, config: Config, storage: Storage) -> int:
    clock = SystemClock()
    recorder = _recorder(config, storage, clock)
    analysis = _analysis(config, storage)
    commands = _commands(config, storage, clock, analysis)
    outboxes: dict[str, Outbox] = {}
    front_ends: dict[str, StoppableLoop] = {}
    digest_targets: list[DigestTarget] = []
    if config.telegram_token:
        telegram = HttpTelegramClient(config.telegram_token, config.http_timeout_s)
        outboxes[TELEGRAM] = TelegramOutbox(telegram)
        digest_targets.append(DigestTarget(TELEGRAM, outboxes[TELEGRAM],
                                           lambda: config.telegram_chat_id))
        front_ends["telegram"] = BotLoop(
            telegram, TelegramBot(telegram, commands, config.telegram_chat_id),
            Backoff(5, config.backoff_cap_s))
    if config.slack_enabled:
        slack = HttpSlackClient(config.slack_token, config.http_timeout_s)
        outboxes[SLACK] = SlackOutbox(slack)
        digest_targets.append(DigestTarget(SLACK, outboxes[SLACK],
                                           lambda: storage.get_state(STATE_CHANNEL_ID)))
        front_ends["slack"] = SlackLoop(
            SlackBot(slack, commands, storage, clock, config.slack_user_id,
                     config.slack_channel_name),
            config.slack_poll_interval_s, Backoff(5, config.backoff_cap_s))
    elif config.slack_token:
        log.error("SLACK_BOT_TOKEN is set but SLACK_USER_ID is empty: Slack is not started")
    recorder.add_listener(SubscriptionNotifier(storage, analysis.scope, outboxes).on_transitions)
    if digest_targets and config.digest_weekday is not None:
        digest = WeeklyDigest(
            storage, clock,
            DigestSchedule(config.digest_weekday, config.digest_hour, config.digest_min_days),
            DigestComposer(analysis.insights, analysis.charts, analysis.scope),
            lambda: coverage(storage.ok_poll_times(POLL_STATUS, POLL_SNAPSHOT),
                             analysis.statistics.gap_threshold).days,
            digest_targets)
        front_ends["digest"] = PeriodicLoop("digest", digest.step, DIGEST_CHECK_S)
    source = _git_source(config)
    if source is not None:
        # Its own thread: a slow clone or a long backfill never holds the recorder up (the
        # importer only takes the storage lock for one short transaction per UTC day).
        job = _snapshot_job(config, storage, clock, source)
        front_ends["snapshot-import"] = PeriodicLoop("snapshot import", job.run,
                                                     config.snapshot_import_interval_s)
    loop = RecorderLoop(recorder, storage, clock, config.poll_interval_s,
                        config.station_refresh_s,
                        Backoff(config.poll_interval_s, config.backoff_cap_s))
    run_service(loop, front_ends)
    return 0


def _status_report(config: Config, storage: Storage, now: datetime) -> str:
    scope = DisplayScope(storage, config.scope_terms)
    return texts.status_text(scope.current_status(), now, scope.names())


def cmd_poll_once(_args, config: Config, storage: Storage) -> int:
    clock = SystemClock()
    recorder = _recorder(config, storage, clock)
    count = recorder.refresh_catalog()
    result = recorder.poll_once()
    print(f"Kapsamdaki connector: {count}, örnek: {result.sample_count}, "
          f"geçiş: {len(result.transitions)}, eksik: {len(result.missing_connectors)}")
    print(_status_report(config, storage, clock.now()))
    return 0


def cmd_status(_args, config: Config, storage: Storage) -> int:
    print(_status_report(config, storage, SystemClock().now()))
    return 0


def cmd_stats(args, config: Config, storage: Storage) -> int:
    stats = _statistics(config, storage)
    scope = DisplayScope(storage, config.scope_terms)
    selection = replace(scope.selection(args.location), connector_id=args.connector)
    entries = stats.entries(selection)
    if not entries:
        print("Seçimle eşleşen AC connector yok (önce `poll-once` ya da `run`).")
        return 1
    print(stats_report(stats, storage, scope, selection,
                       args.location or args.connector or "tüm AC prizler",
                       SystemClock().now()))
    return 0


def _ask(config: Config, storage: Storage, text: str) -> Reply:
    """Runs a chat command locally, exactly as the bots would answer it."""
    commands = _commands(config, storage, SystemClock(), _analysis(config, storage))
    return commands.reply(text, Conversation("cli", 0, ""))


def cmd_insights(args, config: Config, storage: Storage) -> int:
    print(_ask(config, storage, f"analiz {args.location or ''}".strip()).text)
    return 0


def cmd_charts(args, config: Config, storage: Storage) -> int:
    kinds = ChartBook.kinds()
    if args.kind:
        kind = ChartBook.resolve(args.kind)
        if kind is None:
            print(f"Bilinmeyen tür: {args.kind}. Türler: " + ", ".join(k.key for k in kinds))
            return 1
        kinds = (kind,)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        reply = _ask(config, storage, f"grafik {kind.key} {args.location or ''}".strip())
        if not reply.images:
            print(reply.text)
            return 1
        for image in reply.images:
            (out / image.filename).write_bytes(image.data)
            print(out / image.filename)
    return 0


def cmd_estimate(args, config: Config, storage: Storage) -> int:
    entries = DisplayScope(storage, config.scope_terms).selection(args.location).apply(
        storage.catalog())
    entry = entries[0] if entries else None
    now = SystemClock().now()
    estimate = _estimator(config).estimate(
        args.start, args.target, now,
        None if entry is None else entry.power_kw,
        None if entry is None else storage.current_tariff(entry.connector_id))
    print(texts.estimate_text(estimate, now))
    return 0


def cmd_import_snapshots(args, config: Config, storage: Storage) -> int:
    source = DirectorySource(Path(args.dir)) if args.dir else _git_source(config)
    if source is None:
        print(texts.SNAPSHOT_IMPORT_DISABLED)
        return 0
    result = _snapshot_job(config, storage, SystemClock(), source).run()
    if result.catalog_empty:
        print(texts.SNAPSHOT_IMPORT_NO_CATALOG)
        return 1
    threshold = timedelta(seconds=config.gap_threshold_s)
    print(texts.snapshot_import_text(
        result, coverage(storage.ok_poll_times(POLL_STATUS), threshold),
        coverage(storage.ok_poll_times(POLL_SNAPSHOT), threshold)))
    return 0


COMMANDS = {
    "run": cmd_run,
    "poll-once": cmd_poll_once,
    "status": cmd_status,
    "stats": cmd_stats,
    "estimate": cmd_estimate,
    "insights": cmd_insights,
    "charts": cmd_charts,
    "import-snapshots": cmd_import_snapshots,
}


if __name__ == "__main__":
    sys.exit(main())
