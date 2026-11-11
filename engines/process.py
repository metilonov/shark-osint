import asyncio
import json
import os
import signal
import sys
import time

import psutil

from config import ROOT
from database.models import Observation


async def kill_tree(process):
    """Kill and reap descendants on Windows and Unix, including grandchildren."""
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    await process.wait()


class EngineFailure(Exception):
    pass


class ProcessEngine:
    name = ""
    key = ""

    def __init__(self, settings, slots):
        self.settings = settings
        self.slots = slots

    async def run(self, username, cancel, on_event):
        async with self.slots:
            if cancel.is_set():
                return "cancelled"
            cfg = self.settings
            await on_event({"type": "state", "state": "running"})
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "engines.worker", self.key, username,
                "--limit", str(cfg.site_limit), "--timeout", str(cfg.request_timeout),
                "--connections", str(cfg.connections), cwd=ROOT,
                env={**{k: v for k, v in os.environ.items() if k not in {"BOT_TOKEN", "ADMIN_TELEGRAM_ID"}},
                     "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=os.name != "nt", limit=256 * 1024,
            )
            done = False
            async def read():
                nonlocal done
                while line := await process.stdout.readline():
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        raise EngineFailure("Повреждён ответ процесса")
                    if event.get("type") == "observation":
                        event["observation"] = Observation(**event.pop("data"))
                    if event.get("type") == "error":
                        raise EngineFailure(event["message"])
                    if event.get("type") == "done":
                        done = True
                    await on_event(event)
                code = await process.wait()
                if code or not done:
                    raise EngineFailure(f"Движок завершился без полного результата (код {code})")

            async def watch():
                started = time.monotonic()
                while True:
                    if cancel.is_set():
                        return "cancelled"
                    if time.monotonic() - started > cfg.engine_timeout:
                        raise EngineFailure("Превышен таймаут движка; сохранён частичный результат")
                    try:
                        parent = psutil.Process(process.pid)
                        tree = [parent, *parent.children(recursive=True)]
                        memory = sum(p.memory_info().rss for p in tree if p.is_running())
                        if memory > cfg.memory_mb * 1024 * 1024:
                            raise EngineFailure("Превышен лимит памяти процесса; сохранён частичный результат")
                    except psutil.NoSuchProcess:
                        pass
                    await asyncio.sleep(0.25)

            reader = asyncio.create_task(read())
            monitor = asyncio.create_task(watch())
            try:
                finished, _ = await asyncio.wait({reader, monitor}, return_when=asyncio.FIRST_COMPLETED)
                if monitor in finished:
                    return await monitor
                await reader
                return "completed"
            finally:
                reader.cancel()
                monitor.cancel()
                await kill_tree(process)
                await asyncio.gather(reader, monitor, return_exceptions=True)
