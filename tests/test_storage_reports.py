import time
import json

from core.aggregator import aggregate
from database.models import ScanResult, utc_now
from reports.service import build_reports


async def test_persistence_isolation_reports_and_expiration(storage, observations):
    sid = await storage.create_search(1001, "Owner")
    result = ScanResult(sid, "Owner", utc_now(), utc_now(), 2.5, "partial", aggregate(observations), 3, 4, {"Sherlock": "completed"}, [])
    reports = build_reports(result)
    assert reports["pdf"].startswith(b"%PDF-")
    assert "Public display name" in reports["html"].decode()
    assert json.loads(reports["json"])["statistics"]["profiles"] == 2
    await storage.finish(result, reports)
    assert await storage.get_search(sid, 1002) is None
    assert len(await storage.history(1001)) == 1
    assert not await storage.history(1002)
    for fmt in reports:
        assert await storage.get_report(sid, 1001, fmt) == reports[fmt]
        assert await storage.get_report(sid, 1002, fmt) is None
    async with storage.lock:
        await storage.db.execute("UPDATE searches SET expires_at=? WHERE id=?", (time.time()-1, sid))
        await storage.db.commit()
    assert await storage.get_report(sid, 1001, "json") is None
    await storage.cleanup()
    for table in ("searches", "profiles", "reports", "scan_logs"):
        async with storage.db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
            assert (await cursor.fetchone())[0] == 0


def test_html_metadata_escaped(observations):
    from reports.html_report import render_html
    profiles = aggregate(observations)
    profiles[0].note = '<script>alert("xss")</script>'
    result = ScanResult("id", "Owner", utc_now(), utc_now(), 1, "completed", profiles, 3, 4, {}, [])
    html = render_html(result.to_dict()).decode()
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


async def test_restart_marks_unfinished_job(storage):
    sid = await storage.create_search(1001, "Owner")
    await storage.close()
    await storage.open()
    assert (await storage.get_search(sid, 1001))["status"] == "interrupted"
