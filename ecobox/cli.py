"""Command line entry points: `python3 -m ecobox <command>`."""

import argparse
import logging
import sys

from ecobox import texts
from ecobox.api import HttpEcoboxApi
from ecobox.bot import TelegramBot, TelegramOutbox
from ecobox.commands import SLACK, TELEGRAM, CommandHandler
from ecobox.clock import SystemClock
from ecobox.config import Config
from ecobox.estimate import ChargeEstimator
from ecobox.recorder import Recorder
from ecobox.scope import ScopeFilter
from ecobox.notifier import Outbox, SubscriptionNotifier
from ecobox.service import Backoff, BotLoop, RecorderLoop, SlackLoop, StoppableLoop, run_service
from ecobox.slack import HttpSlackClient
from ecobox.slack_bot import SlackBot, SlackOutbox
from ecobox.reports import stats_report
from ecobox.stats import Selection, Statistics
from ecobox.storage import Storage
from ecobox.telegram import HttpTelegramClient

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
    return Recorder(HttpEcoboxApi(config.http_timeout_s), storage,
                    ScopeFilter(config.scope_terms), clock, config.gap_threshold_s)


def _statistics(config: Config, storage: Storage) -> Statistics:
    return Statistics(storage, config.gap_threshold_s)


def _estimator(config: Config) -> ChargeEstimator:
    return ChargeEstimator(config.car_charge_kw, config.kwh_per_percent,
                           config.price_includes_vat)


def cmd_run(_args, config: Config, storage: Storage) -> int:
    clock = SystemClock()
    recorder = _recorder(config, storage, clock)
    commands = CommandHandler(storage, _statistics(config, storage), _estimator(config), clock,
                              config.price_includes_vat)
    outboxes: dict[str, Outbox] = {}
    front_ends: dict[str, StoppableLoop] = {}
    if config.telegram_token:
        telegram = HttpTelegramClient(config.telegram_token, config.http_timeout_s)
        outboxes[TELEGRAM] = TelegramOutbox(telegram)
        front_ends["telegram"] = BotLoop(
            telegram, TelegramBot(telegram, commands, config.telegram_chat_id),
            Backoff(5, config.backoff_cap_s))
    if config.slack_enabled:
        slack = HttpSlackClient(config.slack_token, config.http_timeout_s)
        outboxes[SLACK] = SlackOutbox(slack)
        front_ends["slack"] = SlackLoop(
            SlackBot(slack, commands, storage, clock, config.slack_user_id,
                     config.slack_channel_name),
            config.slack_poll_interval_s, Backoff(5, config.backoff_cap_s))
    elif config.slack_token:
        log.error("SLACK_BOT_TOKEN is set but SLACK_USER_ID is empty: Slack is not started")
    recorder.add_listener(SubscriptionNotifier(storage, outboxes).on_transitions)
    loop = RecorderLoop(recorder, storage, clock, config.poll_interval_s,
                        config.station_refresh_s,
                        Backoff(config.poll_interval_s, config.backoff_cap_s))
    run_service(loop, front_ends)
    return 0


def cmd_poll_once(_args, config: Config, storage: Storage) -> int:
    clock = SystemClock()
    recorder = _recorder(config, storage, clock)
    count = recorder.refresh_catalog()
    result = recorder.poll_once()
    print(f"Kapsamdaki connector: {count}, örnek: {result.sample_count}, "
          f"geçiş: {len(result.transitions)}, eksik: {len(result.missing_connectors)}")
    print(texts.status_text(storage.current_status(), clock.now()))
    return 0


def cmd_status(_args, config: Config, storage: Storage) -> int:
    print(texts.status_text(storage.current_status(), SystemClock().now()))
    return 0


def cmd_stats(args, config: Config, storage: Storage) -> int:
    stats = _statistics(config, storage)
    selection = Selection(location_term=args.location, connector_id=args.connector)
    entries = stats.entries(selection)
    if not entries:
        print("Seçimle eşleşen AC connector yok (önce `poll-once` ya da `run`).")
        return 1
    print(stats_report(stats, storage, selection,
                       args.location or args.connector or "tüm AC prizler",
                       SystemClock().now()))
    return 0


def cmd_estimate(args, config: Config, storage: Storage) -> int:
    entries = Selection(location_term=args.location).apply(storage.catalog())
    entry = entries[0] if entries else None
    estimate = _estimator(config).estimate(
        args.start, args.target, SystemClock().now(),
        None if entry is None else entry.power_kw,
        None if entry is None else storage.current_tariff(entry.connector_id))
    print(texts.estimate_text(estimate, None if entry is None else entry.label,
                              config.price_includes_vat))
    return 0


COMMANDS = {
    "run": cmd_run,
    "poll-once": cmd_poll_once,
    "status": cmd_status,
    "stats": cmd_stats,
    "estimate": cmd_estimate,
}


if __name__ == "__main__":
    sys.exit(main())
