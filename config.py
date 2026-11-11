from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    token: str = ""
    admin_id: int = 0
    data_dir: Path = ROOT / "data"
    retention_days: int = 7
    engine_timeout: int = 600
    request_timeout: int = 12
    max_processes: int = 2
    memory_mb: int = 768
    site_limit: int = 500
    connections: int = 8
    cooldown_seconds: int = 60
    spiderfoot_python: str = ""
    spiderfoot_path: str = ""
    spiderfoot_csv_export: str = ""
    spiderfoot_usernames: tuple[str, ...] = ()

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        def number(name, default, lo, hi):
            value = int(os.getenv(name) or default)
            if not lo <= value <= hi:
                raise ValueError(f"{name}: допустимый диапазон {lo}–{hi}")
            return value
        return cls(
            token=os.getenv("BOT_TOKEN", "").strip(),
            admin_id=number("ADMIN_TELEGRAM_ID", 0, 0, 2**63 - 1),
            data_dir=Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve(),
            retention_days=number("RETENTION_DAYS", 7, 1, 365),
            engine_timeout=number("ENGINE_TIMEOUT", 600, 5, 3600),
            request_timeout=number("REQUEST_TIMEOUT", 12, 1, 60),
            max_processes=number("MAX_PROCESSES", 2, 1, 2),
            memory_mb=number("PROCESS_MEMORY_MB", 768, 128, 4096),
            site_limit=number("SITE_LIMIT", 500, 1, 3000),
            connections=number("CONNECTIONS", 8, 1, 20),
            cooldown_seconds=number("COOLDOWN_SECONDS", 60, 10, 3600),
            spiderfoot_python=os.getenv("SPIDERFOOT_PYTHON", ""),
            spiderfoot_path=os.getenv("SPIDERFOOT_PATH", ""),
            spiderfoot_csv_export=os.getenv("SPIDERFOOT_CSV_EXPORT", ""),
            spiderfoot_usernames=tuple(x.strip() for x in os.getenv("SPIDERFOOT_ALLOWED_USERNAMES", "").split(",") if x.strip()),
        )

    def validate_bot(self):
        if not self.token or self.admin_id <= 0:
            raise ValueError("Укажите BOT_TOKEN и ADMIN_TELEGRAM_ID в локальном .env (см. .env.example).")
