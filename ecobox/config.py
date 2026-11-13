"""Configuration from `.env` plus process environment (environment wins)."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser: comments, blank lines, optional quotes, `export`."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class Config:
    db_path: Path
    poll_interval_s: int = 180
    gap_factor: float = 2.5
    station_refresh_s: int = 24 * 3600
    http_timeout_s: float = 20.0
    backoff_cap_s: int = 15 * 60
    scope_terms: tuple[str, ...] = ("hacettepe",)
    """Display scope: what the bot commands and the default analysis show."""
    record_scope_terms: tuple[str, ...] = ()
    """Record scope: which charge points are sampled; empty means every Ecobox site."""
    telegram_token: str | None = None
    telegram_chat_id: int | None = None
    slack_token: str | None = None
    slack_user_id: str | None = None
    slack_channel_name: str = "ecobox-sarj"
    slack_poll_interval_s: float = 3.0
    car_charge_kw: float = 10.6
    kwh_per_percent: float = 0.95
    price_includes_vat: bool = True
    log_level: str = "INFO"
    digest_weekday: int | None = 0
    """Local weekday of the weekly digest, 0 = Monday; None turns the digest off."""
    digest_hour: int = 9
    digest_min_days: float = 3.0
    """The digest stays silent while the display scope has fewer observed days than this."""
    snapshot_repo_url: str | None = None
    """Public git repository of the remote collector; None turns the snapshot import off."""
    snapshot_branch: str = "data"
    snapshot_checkout: Path = PROJECT_ROOT / "data" / "remote"
    snapshot_import_interval_s: float = 3600.0

    @property
    def gap_threshold_s(self) -> float:
        """A sample older than this before the next one means the gap hides the true time."""
        return self.poll_interval_s * self.gap_factor

    @property
    def slack_enabled(self) -> bool:
        """Slack needs both the bot token and the owner's member id."""
        return bool(self.slack_token and self.slack_user_id)

    @classmethod
    def load(cls, env_file: Path | None = None,
             environ: dict[str, str] | None = None) -> "Config":
        values = parse_env_file(env_file or PROJECT_ROOT / ".env")
        values.update(os.environ if environ is None else environ)

        def get(key: str) -> str | None:
            value = values.get(key, "").strip()
            return value or None

        db_path = Path(get("ECOBOX_DB_PATH") or PROJECT_ROOT / "data" / "ecobox.sqlite3")
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        scope = _terms(values.get("ECOBOX_SCOPE"), cls.scope_terms)
        record_scope = _terms(values.get("ECOBOX_RECORD_SCOPE"), cls.record_scope_terms)
        chat_id = get("TELEGRAM_CHAT_ID")
        weekday = int(get("DIGEST_WEEKDAY") or 1)
        return cls(
            db_path=db_path,
            poll_interval_s=int(get("ECOBOX_POLL_INTERVAL_S") or cls.poll_interval_s),
            gap_factor=float(get("ECOBOX_GAP_FACTOR") or cls.gap_factor),
            station_refresh_s=int(get("ECOBOX_STATION_REFRESH_S") or cls.station_refresh_s),
            http_timeout_s=float(get("ECOBOX_HTTP_TIMEOUT_S") or cls.http_timeout_s),
            backoff_cap_s=int(get("ECOBOX_BACKOFF_CAP_S") or cls.backoff_cap_s),
            scope_terms=scope,
            record_scope_terms=record_scope,
            telegram_token=get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=int(chat_id) if chat_id else None,
            slack_token=get("SLACK_BOT_TOKEN"),
            slack_user_id=get("SLACK_USER_ID"),
            slack_channel_name=get("SLACK_CHANNEL_NAME") or cls.slack_channel_name,
            slack_poll_interval_s=float(get("SLACK_POLL_INTERVAL_S")
                                        or cls.slack_poll_interval_s),
            car_charge_kw=float(get("CAR_CHARGE_KW") or cls.car_charge_kw),
            kwh_per_percent=float(get("KWH_PER_PERCENT") or cls.kwh_per_percent),
            price_includes_vat=(get("PRICE_INCLUDES_VAT") or "true").lower()
            in ("1", "true", "yes", "evet"),
            log_level=get("ECOBOX_LOG_LEVEL") or cls.log_level,
            digest_weekday=_weekday(weekday),
            digest_hour=int(get("DIGEST_HOUR") or cls.digest_hour),
            digest_min_days=float(get("DIGEST_MIN_DAYS") or cls.digest_min_days),
            snapshot_repo_url=get("ECOBOX_SNAPSHOT_REPO_URL"),
            snapshot_branch=get("ECOBOX_SNAPSHOT_BRANCH") or cls.snapshot_branch,
            snapshot_import_interval_s=float(get("ECOBOX_SNAPSHOT_IMPORT_S")
                                             or cls.snapshot_import_interval_s),
        )


def _terms(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    """Comma-separated terms; a missing key keeps the default, an empty value means none."""
    if raw is None:
        return default
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def _weekday(value: int) -> int | None:
    """DIGEST_WEEKDAY is 1 (Pazartesi) .. 7 (Pazar); 0 turns the digest off."""
    if value == 0:
        return None
    if not 1 <= value <= 7:
        raise ValueError(f"DIGEST_WEEKDAY must be 0..7, got {value}")
    return value - 1
