"""Environment-driven configuration.

Only the standard library is used so the application can boot (and print a
useful error) even when optional dependencies are missing.

A tiny ``.env`` loader is included to keep native Windows execution friction
free: ``copy .env.example .env && python -m app``.  Real environment variables
always win over values from the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_DEMO_API_KEY = "sk_demo_fluxpay_2026"
DEFAULT_DEMO_WEBHOOK_SECRET = "whsec_demo_fluxpay_2026"
DEFAULT_WEBHOOK_URL = "http://127.0.0.1:8000/demo/webhook-receiver?endpoint=whe_demo"
DEFAULT_PIX_KEY = "demo@fluxpay.local"


def load_dotenv(path: Path | None = None) -> None:
    """Load a minimal ``KEY=VALUE`` dotenv file without external packages."""
    candidates = [path] if path else [Path.cwd() / ".env", BASE_DIR / ".env"]
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            # Never override real environment variables.
            os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number, got {raw!r}") from exc


def _env_schedule(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        schedule = tuple(float(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a comma-separated list of seconds") from exc
    if not schedule or any(value < 0 for value in schedule):
        raise ValueError(f"Environment variable {name} must contain non-negative values")
    return schedule


@dataclass
class Settings:
    """All runtime configuration in one immutable-ish object.

    Tests construct this directly with a temporary database path.
    """

    app_env: str = "demo"
    demo_mode: bool = True
    demo_expose_api_key: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    database_path: Path = field(default_factory=lambda: Path.cwd() / "data" / "gateway.db")
    db_busy_timeout_ms: int = 15000

    merchant_id: str = "mch_demo"
    merchant_name: str = "Loja Demo"
    api_key: str = DEFAULT_DEMO_API_KEY

    webhook_secret: str = DEFAULT_DEMO_WEBHOOK_SECRET
    webhook_url: str = DEFAULT_WEBHOOK_URL
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 6
    webhook_retry_schedule: tuple[float, ...] = (10.0, 30.0, 120.0, 600.0, 3600.0)
    webhook_jitter_ratio: float = 0.2
    webhook_signature_tolerance_seconds: int = 300
    webhook_lease_seconds: int = 60
    webhook_batch_size: int = 10

    pix_key: str = DEFAULT_PIX_KEY
    pix_city: str = "SAO PAULO"
    pix_expires_minutes: int = 30

    card_authorization_delay_ms: int = 350

    worker_enabled: bool = True
    worker_poll_seconds: float = 0.25
    dispatcher_poll_seconds: float = 0.5
    queue_batch_size: int = 10
    queue_max_attempts: int = 5
    queue_lease_seconds: int = 120

    log_level: str = "INFO"
    max_body_bytes: int = 256 * 1024

    # ---- v0.3: real-time hub / WebSocket ------------------------------------ #
    ws_enabled: bool = True
    ws_poll_seconds: float = 0.15
    ws_heartbeat_seconds: float = 20.0
    event_stream_retention_days: int = 30

    # ---- v0.3: scheduled jobs (segments, RAG sweep, rollups, retention) ------ #
    event_fanout: bool = True  # enqueue automation/RAG consumers on hub events
    jobs_enabled: bool | None = None  # None = follow worker_enabled
    jobs_segments_seconds: float = 60.0
    jobs_analytics_seconds: float = 180.0
    jobs_rag_seconds: float = 300.0
    jobs_retention_seconds: float = 900.0
    analytics_rollup_days: int = 92

    # ---- v0.3: RAG ----------------------------------------------------------- #
    rag_enabled: bool = True
    rag_specs_dir: Path = field(default_factory=lambda: BASE_DIR.parent / "specs")
    rag_index_payments_limit: int = 2000
    rag_index_customers_limit: int = 2000
    rag_index_feed_days: int = 7

    # ---- v0.3: Agent IA (LLM opcional e plugável, A-09) ---------------------- #
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    agent_llm_timeout_seconds: float = 30.0
    agent_rate_limit_per_minute: int = 30
    agent_confirm_ttl_seconds: float = 600.0
    pii_redact_email: bool = False

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def jobs_active(self) -> bool:
        return self.worker_enabled if self.jobs_enabled is None else self.jobs_enabled

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"prod", "production"}

    def validate(self) -> None:
        if not self.api_key or len(self.api_key) < 12:
            raise ValueError("API_KEY must be at least 12 characters long")
        if not self.webhook_secret or len(self.webhook_secret) < 12:
            raise ValueError("WEBHOOK_SECRET must be at least 12 characters long")
        if not str(self.webhook_url).startswith(("http://", "https://")):
            raise ValueError("WEBHOOK_URL must be an http(s) URL")
        if self.is_production and self.demo_mode:
            raise ValueError("DEMO_MODE must be false when APP_ENV=production")
        if self.is_production and self.demo_expose_api_key:
            raise ValueError("DEMO_EXPOSE_API_KEY must be false when APP_ENV=production")
        if self.webhook_max_attempts < 1:
            raise ValueError("WEBHOOK_MAX_ATTEMPTS must be >= 1")
        if len(self.webhook_retry_schedule) < self.webhook_max_attempts - 1:
            raise ValueError(
                "WEBHOOK_RETRY_SCHEDULE needs at least WEBHOOK_MAX_ATTEMPTS-1 entries"
            )
        if not 1 <= self.pix_expires_minutes <= 24 * 60:
            raise ValueError("PIX_EXPIRES_MINUTES must be between 1 and 1440")
        if self.card_authorization_delay_ms < 0:
            raise ValueError("CARD_AUTHORIZATION_DELAY_MS must be >= 0")
        if self.event_stream_retention_days < 1:
            raise ValueError("EVENT_STREAM_RETENTION_DAYS must be >= 1")
        if self.agent_rate_limit_per_minute < 1:
            raise ValueError("AGENT_RATE_LIMIT_PER_MINUTE must be >= 1")
        if self.agent_confirm_ttl_seconds < 5:
            raise ValueError("AGENT_CONFIRM_TTL_SECONDS must be >= 5")
        if self.llm_base_url and not self.llm_base_url.startswith(("http://", "https://")):
            raise ValueError("OPENAI_COMPATIBLE_BASE_URL must be an http(s) URL")
        if self.llm_base_url and not self.llm_model:
            raise ValueError("OPENAI_COMPATIBLE_MODEL must be set together with the base URL")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        app_env = os.getenv("APP_ENV", "demo").strip().lower()
        demo_default = app_env not in {"prod", "production"}
        schedule = _env_schedule("WEBHOOK_RETRY_SCHEDULE", (10.0, 30.0, 120.0, 600.0, 3600.0))
        settings = cls(
            app_env=app_env,
            demo_mode=_env_bool("DEMO_MODE", demo_default),
            demo_expose_api_key=_env_bool("DEMO_EXPOSE_API_KEY", demo_default),
            host=os.getenv("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8000),
            database_path=Path(
                os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "gateway.db"))
            ).expanduser(),
            db_busy_timeout_ms=_env_int("DB_BUSY_TIMEOUT_MS", 15000),
            merchant_id=os.getenv("MERCHANT_ID", "mch_demo"),
            merchant_name=os.getenv("MERCHANT_NAME", "Loja Demo"),
            api_key=os.getenv("API_KEY", DEFAULT_DEMO_API_KEY),
            webhook_secret=os.getenv("WEBHOOK_SECRET", DEFAULT_DEMO_WEBHOOK_SECRET),
            webhook_url=os.getenv("WEBHOOK_URL", DEFAULT_WEBHOOK_URL),
            webhook_timeout_seconds=_env_float("WEBHOOK_TIMEOUT_SECONDS", 10.0),
            webhook_max_attempts=_env_int("WEBHOOK_MAX_ATTEMPTS", len(schedule) + 1),
            webhook_retry_schedule=schedule,
            webhook_jitter_ratio=_env_float("WEBHOOK_JITTER_RATIO", 0.2),
            webhook_signature_tolerance_seconds=_env_int("WEBHOOK_SIGNATURE_TOLERANCE_SECONDS", 300),
            webhook_lease_seconds=_env_int("WEBHOOK_LEASE_SECONDS", 60),
            webhook_batch_size=_env_int("WEBHOOK_BATCH_SIZE", 10),
            pix_key=os.getenv("PIX_KEY", DEFAULT_PIX_KEY),
            pix_city=os.getenv("PIX_CITY", "SAO PAULO"),
            pix_expires_minutes=_env_int("PIX_EXPIRES_MINUTES", 30),
            card_authorization_delay_ms=_env_int("CARD_AUTHORIZATION_DELAY_MS", 350),
            worker_enabled=_env_bool("WORKER_ENABLED", True),
            worker_poll_seconds=_env_float("WORKER_POLL_SECONDS", 0.25),
            dispatcher_poll_seconds=_env_float("DISPATCHER_POLL_SECONDS", 0.5),
            queue_batch_size=_env_int("QUEUE_BATCH_SIZE", 10),
            queue_max_attempts=_env_int("QUEUE_MAX_ATTEMPTS", 5),
            queue_lease_seconds=_env_int("QUEUE_LEASE_SECONDS", 120),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            max_body_bytes=_env_int("MAX_BODY_BYTES", 256 * 1024),
            # v0.3
            event_fanout=_env_bool("EVENT_FANOUT", True),
            ws_enabled=_env_bool("WS_ENABLED", True),
            ws_poll_seconds=_env_float("WS_POLL_SECONDS", 0.15),
            ws_heartbeat_seconds=_env_float("WS_HEARTBEAT_SECONDS", 20.0),
            event_stream_retention_days=_env_int("EVENT_STREAM_RETENTION_DAYS", 30),
            jobs_enabled=(
                None
                if not os.getenv("JOBS_ENABLED")
                else _env_bool("JOBS_ENABLED", True)
            ),
            jobs_segments_seconds=_env_float("JOBS_SEGMENTS_SECONDS", 60.0),
            jobs_analytics_seconds=_env_float("JOBS_ANALYTICS_SECONDS", 180.0),
            jobs_rag_seconds=_env_float("JOBS_RAG_SECONDS", 300.0),
            jobs_retention_seconds=_env_float("JOBS_RETENTION_SECONDS", 900.0),
            analytics_rollup_days=_env_int("ANALYTICS_ROLLUP_DAYS", 92),
            rag_enabled=_env_bool("RAG_ENABLED", True),
            rag_specs_dir=Path(os.getenv("RAG_SPECS_DIR", str(BASE_DIR.parent / "specs"))).expanduser(),
            rag_index_payments_limit=_env_int("RAG_INDEX_PAYMENTS_LIMIT", 2000),
            rag_index_customers_limit=_env_int("RAG_INDEX_CUSTOMERS_LIMIT", 2000),
            rag_index_feed_days=_env_int("RAG_INDEX_FEED_DAYS", 7),
            llm_base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL", ""),
            llm_api_key=os.getenv("OPENAI_COMPATIBLE_API_KEY", ""),
            llm_model=os.getenv("OPENAI_COMPATIBLE_MODEL", ""),
            agent_llm_timeout_seconds=_env_float("AGENT_LLM_TIMEOUT_SECONDS", 30.0),
            agent_rate_limit_per_minute=_env_int("AGENT_RATE_LIMIT_PER_MINUTE", 30),
            agent_confirm_ttl_seconds=_env_float("AGENT_CONFIRM_TTL_SECONDS", 600.0),
            pii_redact_email=_env_bool("PII_REDACT_EMAIL", False),
        )
        if settings.is_production:
            for required in ("API_KEY", "WEBHOOK_SECRET"):
                if not os.getenv(required):
                    raise ValueError(f"{required} must be set explicitly in production")
        settings.validate()
        return settings
