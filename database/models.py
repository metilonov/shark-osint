from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Observation:
    engine: str
    platform: str
    url: str
    status: str
    http_status: int | None = None
    detected_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    checked_url: str = ""


@dataclass
class Profile:
    platform: str
    url: str
    sources: list[dict]
    metadata: dict
    accessible: bool
    confidence: str
    possible_false_positive: bool = True
    note: str = "Совпадение ника не подтверждает личность или владельца аккаунта."


@dataclass
class ScanResult:
    search_id: str
    username: str
    started_at: str
    finished_at: str
    duration_seconds: float
    status: str
    profiles: list[Profile]
    checked_platforms: int
    checks: int
    engines: dict[str, str]
    errors: list[dict]

    def to_dict(self):
        data = asdict(self)
        data["statistics"] = {
            "checked_platforms": self.checked_platforms,
            "checks": self.checks,
            "profiles": len(self.profiles),
            "accessible_pages": sum(p.accessible for p in self.profiles),
            "uncertain_pages": sum(not p.accessible for p in self.profiles),
            "multi_source_matches": sum(len({s['engine'] for s in p.sources}) > 1 for p in self.profiles),
        }
        data["notice"] = "Доступность страницы и согласие инструментов не доказывают принадлежность одному человеку. Все совпадения требуют ручной проверки."
        return data
