import asyncio
from importlib.metadata import version
from pathlib import Path
import sys
import tempfile

from config import ROOT
from database.models import ScanResult, utc_now
from database.storage import Storage
from reports.service import build_reports


async def command(*args, **kwargs):
    process = await asyncio.create_subprocess_exec(*args, **kwargs, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 30)
        return process.returncode, stdout
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def check(settings):
    for package in ("aiogram", "sherlock-project", "maigret", "aiohttp", "aiosqlite", "Jinja2", "reportlab", "psutil"):
        print(f"OK {package} {version(package)}")
    for engine in ("sherlock", "maigret"):
        code, stdout = await command(sys.executable, "-m", "engines.worker", engine, "--probe", cwd=ROOT)
        if code:
            raise RuntimeError(f"Ошибка загрузки {engine}")
        print(f"OK {engine}: локальная база: {stdout.decode().strip()}")
    with tempfile.TemporaryDirectory(prefix="shark-check-") as temp:
        storage = Storage(Path(temp) / "check.sqlite3")
        await storage.open()
        try:
            sid = await storage.create_search(1, "self_check")
            result = ScanResult(sid, "self_check", utc_now(), utc_now(), 0, "completed", [], 0, 0, {}, [])
            reports = await asyncio.to_thread(build_reports, result)
            await storage.finish(result, reports)
            for fmt in ("json", "html", "pdf"):
                assert await storage.get_report(sid, 1, fmt)
                assert await storage.get_report(sid, 2, fmt) is None
            print("OK SQLite, изоляция владельцев, HTML / JSON / PDF (пустой технический отчёт во временном каталоге)")
        finally:
            await storage.close()
    sf = Path(settings.spiderfoot_path) if settings.spiderfoot_path else ROOT / ".tools" / "spiderfoot" / "sf.py"
    executable = Path(settings.spiderfoot_python) if settings.spiderfoot_python else ROOT / ".tools" / "spiderfoot-venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if sf.is_file() and executable.is_file():
        code, stdout = await command(str(executable), str(sf), "--help")
        if code or b"-max-threads" not in stdout or b"csv" not in stdout:
            print("DISABLED SpiderFoot: CLI несовместим; основные движки доступны")
        else:
            print("OK SpiderFoot: реальный CLI --help; целевые сканы не запускались")
    else:
        print("DISABLED SpiderFoot: отдельная установка не найдена")
    print("Telegram: не подключался; для реального запуска заполните .env и выполните python main.py")
