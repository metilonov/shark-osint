import asyncio
from dataclasses import replace
import json
import sys

import pytest

from core.queue import ScanQueue
from core.scanner import Scanner
from database.models import Observation
from engines.process import EngineFailure, ProcessEngine
from engines.spiderfoot_engine import parse_csv, SpiderFootEngine


async def ignore(*args):
    pass


class FakeEngine:
    def __init__(self, name, observations=(), failure=False, block=False, disabled=False):
        self.name, self.observations = name, observations
        self.failure, self.block, self.disabled = failure, block, disabled

    async def run(self, username, cancel, event):
        if self.disabled:
            await event({"type": "disabled", "message": "not configured"})
            return "disabled"
        await event({"type": "state", "state": "running"})
        for observation in self.observations:
            await event({"type": "observation", "observation": observation})
        if self.block:
            await cancel.wait()
            return "cancelled"
        if self.failure:
            raise EngineFailure("simulated network outage")
        return "completed"


def fake_scanner(settings, observations=(), **kwargs):
    return Scanner(settings, [FakeEngine("Sherlock", observations, **kwargs), FakeEngine("Maigret", **kwargs)], FakeEngine("SpiderFoot", disabled=True))


async def test_real_progress_and_actual_totals(settings, observations):
    progress = []
    async def record(data):
        progress.append(data)
    result = await fake_scanner(settings, observations).scan("id", "Owner", asyncio.Event(), record)
    assert result.status == "partial"
    assert result.checked_platforms == 3
    assert result.checks == 4
    assert len(result.profiles) == 2
    assert result.to_dict()["statistics"]["accessible_pages"] == 1
    assert any(p["engines"]["Sherlock"] == "running" for p in progress)
    assert progress[-1]["found"] == 2


async def test_engine_failure_keeps_other_results(settings, observations):
    scanner = Scanner(settings, [FakeEngine("Sherlock", observations[:1]), FakeEngine("Maigret", failure=True)], FakeEngine("SpiderFoot", disabled=True))
    result = await scanner.scan("id", "Owner", asyncio.Event(), ignore)
    assert result.status == "partial" and len(result.profiles) == 1
    assert result.engines["Maigret"] == "failed"


async def test_no_internet_is_failure_not_empty_success(settings):
    result = await fake_scanner(settings, failure=True).scan("id", "Owner", asyncio.Event(), ignore)
    assert result.status == "failed"
    assert not result.profiles
    assert len(result.errors) == 3


async def test_cancellation_retains_partial_results(settings, observations):
    cancel = asyncio.Event()
    task = asyncio.create_task(fake_scanner(settings, observations[:1], block=True).scan("id", "Owner", cancel, ignore))
    await asyncio.sleep(0.02)
    cancel.set()
    result = await asyncio.wait_for(task, 1)
    assert result.status == "cancelled" and len(result.profiles) == 1


async def test_queue_rejects_bulk_and_enforces_owner(settings, storage, observations):
    queue = ScanQueue(fake_scanner(settings, observations[:1], block=True), storage, 60)
    await queue.start()
    notification = asyncio.Event()
    async def done(result):
        notification.set()
    sid = await queue.submit(1001, "Owner", ignore, done)
    try:
        with pytest.raises(ValueError):
            await queue.submit(1001, "Second", ignore, done)
        assert queue.cancel(1002, sid) is False
        assert queue.cancel(1001, sid) is True
        await asyncio.wait_for(notification.wait(), 5)
        row = await storage.get_search(sid, 1001)
        assert row["status"] == "cancelled"
        assert await storage.get_report(sid, 1001, "pdf")
        assert await storage.get_report(sid, 1002, "pdf") is None
        with pytest.raises(ValueError, match="60"):
            await queue.submit(1001, "Again", ignore, done)
    finally:
        await queue.close()


@pytest.mark.parametrize("cause", ["timeout", "cancel", "memory", "crash"])
async def test_real_process_supervision_without_network(settings, monkeypatch, cause):
    real_exec = asyncio.create_subprocess_exec
    processes = []
    async def local_child(*args, **kwargs):
        script = "import time; time.sleep(20)" if cause != "crash" else "raise SystemExit(2)"
        process = await real_exec(sys.executable, "-c", script, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", local_child)
    cfg = replace(settings, engine_timeout=0.1 if cause == "timeout" else 20, memory_mb=0 if cause == "memory" else 768)
    engine = ProcessEngine(cfg, asyncio.Semaphore(1))
    cancel = asyncio.Event()
    task = asyncio.create_task(engine.run("Owner", cancel, ignore))
    if cause == "cancel":
        await asyncio.sleep(0.05)
        cancel.set()
        assert await asyncio.wait_for(task, 3) == "cancelled"
    else:
        with pytest.raises(EngineFailure):
            await asyncio.wait_for(task, 3)
    assert processes and all(p.returncode is not None for p in processes)


def test_spiderfoot_documented_csv():
    text = 'Source,Type,Data\nsfp_accounts,ACCOUNT_EXTERNAL_OWNED,"Example (Category: social)\n<SFURL>https://example.com/u/Owner</SFURL>"\nsfp_accounts,EMAILADDR,ignore@example.com\n'
    rows = list(parse_csv(text))
    assert len(rows) == 1
    assert rows[0].engine == "SpiderFoot" and rows[0].http_status is None
    with pytest.raises(EngineFailure):
        list(parse_csv("bad,columns\n1,2\n"))


async def test_spiderfoot_disabled_does_not_spawn(settings, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("An unconfigured SpiderFoot must not start a process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    engine = SpiderFootEngine(settings)
    assert await engine.run("Owner", asyncio.Event(), ignore) == "disabled"


async def test_spiderfoot_approved_csv_import_and_target_isolation(settings, tmp_path):
    path = tmp_path / "approved.csv"
    path.write_text('Source,Type,Data\nSpiderFoot UI,USERNAME,Owner\nsfp_accounts,ACCOUNT_EXTERNAL_OWNED,"Example (Category: social)\n<SFURL>https://example.com/u/Owner</SFURL>"\n', encoding="utf-8")
    cfg = replace(settings, spiderfoot_csv_export=str(path), spiderfoot_usernames=("Owner", "Other"))
    events = []
    async def record(event):
        events.append(event)
    engine = SpiderFootEngine(cfg)
    assert await engine.run("Owner", asyncio.Event(), record) == "imported"
    assert len([e for e in events if e['type'] == 'observation']) == 1
    assert await engine.run("NotAuthorized", asyncio.Event(), record) == "disabled"
    with pytest.raises(EngineFailure, match="USERNAME"):
        await engine.run("Other", asyncio.Event(), record)


async def test_process_protocol_complete_without_network(settings, monkeypatch):
    real_exec = asyncio.create_subprocess_exec
    monkeypatch.setenv("BOT_TOKEN", "private-value-should-not-reach-worker")
    async def local_child(*args, **kwargs):
        assert "BOT_TOKEN" not in kwargs["env"]
        observation = {"engine": "Sherlock", "platform": "Example", "url": "https://example.com/Owner", "status": "claimed", "http_status": 200}
        lines = [json.dumps({"type": "planned", "count": 1}), json.dumps({"type": "observation", "data": observation}), json.dumps({"type": "done"})]
        script = "\n".join(f"print({line!r}, flush=True)" for line in lines)
        return await real_exec(sys.executable, "-c", script, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", local_child)
    events = []
    async def record(event):
        events.append(event)
    engine = ProcessEngine(settings, asyncio.Semaphore(1))
    assert await engine.run("Owner", asyncio.Event(), record) == "completed"
    assert any(e.get('observation') and e['observation'].http_status == 200 for e in events)


async def test_report_failure_preserves_json_and_queue_lives(settings, storage, observations, monkeypatch):
    def fail(*args):
        raise RuntimeError("simulated PDF error")
    monkeypatch.setattr("core.queue.build_reports", fail)
    queue = ScanQueue(fake_scanner(settings, observations[:1]), storage, 10)
    await queue.start()
    completed = asyncio.Event()
    async def done(result):
        completed.set()
    try:
        sid = await queue.submit(1001, "Owner", ignore, done)
        await asyncio.wait_for(completed.wait(), 5)
        assert await storage.get_report(sid, 1001, "json")
        assert (await storage.get_search(sid, 1001))["status"] == "partial"
        assert not queue.worker.done()
    finally:
        await queue.close()
