"""Independent adapter for the documented SpiderFoot CLI CSV export.

Upstream sfp_accounts at 0f815a2 makes random-username probes and uses
verify=False. Its documented CLI cannot disable these behaviors. Automatic
scanning is therefore disabled; approved existing CSV exports can be imported.
No private API or internal SpiderFoot database is accessed.
"""
import asyncio
import csv
import io
from pathlib import Path
import re

from core.validator import canonical_url
from database.models import Observation
from engines.process import EngineFailure

MAX_EXPORT_BYTES = 16 * 1024 * 1024


def parse_csv(text, username=None):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["Source", "Type", "Data"]:
        raise EngineFailure("SpiderFoot: неизвестный формат CSV (нужны Source,Type,Data)")
    rows = []
    for row in reader:
        rows.append(row)
        if len(rows) > 10000:
            raise EngineFailure("SpiderFoot: превышен лимит строк экспорта")
    if username is not None:
        targets = {r.get("Data", "").strip().strip('"') for r in rows if r.get("Type") == "USERNAME"}
        if targets != {username}:
            raise EngineFailure("SpiderFoot: CSV должен содержать USERNAME только для запрошенного разрешённого ника")
    for row in rows:
        if row.get("Source") != "sfp_accounts" or row.get("Type") != "ACCOUNT_EXTERNAL_OWNED":
            continue
        data = row.get("Data", "")
        match = re.search(r"<SFURL>(.*?)</SFURL>", data, re.S)
        if match and (url := canonical_url(match.group(1))):
            yield Observation("SpiderFoot", data.split(" (Category:", 1)[0][:100], url, "claimed",
                              metadata={"imported_from": "SpiderFoot CLI CSV", "timestamp_kind": "import_time"})


class SpiderFootEngine:
    name = "SpiderFoot"

    def __init__(self, settings):
        self.settings = settings

    def disabled_reason(self, username):
        cfg = self.settings
        if not cfg.spiderfoot_csv_export:
            return ("Автосканирование SpiderFoot отключено: upstream sfp_accounts делает запросы со случайными никами "
                    "и verify=False. Доступен импорт разрешённого CSV через SPIDERFOOT_CSV_EXPORT")
        if username not in cfg.spiderfoot_usernames:
            return "Ник не включён в SPIDERFOOT_ALLOWED_USERNAMES; импорт отключён"
        if not Path(cfg.spiderfoot_csv_export).is_file():
            return "Файл CSV SpiderFoot не найден; Sherlock и Maigret продолжают работу"
        return ""

    async def run(self, username, cancel, on_event):
        reason = self.disabled_reason(username)
        if reason:
            await on_event({"type": "disabled", "message": reason})
            return "disabled"
        if cancel.is_set():
            return "cancelled"
        await on_event({"type": "state", "state": "running"})
        path = Path(self.settings.spiderfoot_csv_export)
        def load():
            with path.open("rb") as source:
                data = source.read(MAX_EXPORT_BYTES + 1)
            if len(data) > MAX_EXPORT_BYTES:
                raise EngineFailure("SpiderFoot: CSV превышает 16 МБ")
            return list(parse_csv(data.decode("utf-8-sig"), username))
        observations = await asyncio.to_thread(load)
        await on_event({"type": "notice", "message": "Импорт CSV: время записи — время импорта; число отрицательных проверок SpiderFoot неизвестно и не включено в статистику. Исходный CSV управляется владельцем и не удаляется ботом."})
        for obs in observations:
            if cancel.is_set():
                return "cancelled"
            await on_event({"type": "observation", "observation": obs})
        return "imported"
