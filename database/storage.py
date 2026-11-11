import asyncio
import json
import time
import uuid
from pathlib import Path

import aiosqlite

SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA secure_delete=ON;
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
 telegram_id INTEGER PRIMARY KEY, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(telegram_id),
 username TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
 expires_at REAL NOT NULL, result_json TEXT
);
CREATE TABLE IF NOT EXISTS profiles (
 id INTEGER PRIMARY KEY, search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
 url TEXT NOT NULL, platform TEXT NOT NULL, data_json TEXT NOT NULL,
 UNIQUE(search_id, url)
);
CREATE TABLE IF NOT EXISTS reports (
 search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
 format TEXT NOT NULL, content BLOB NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(search_id, format)
);
CREATE TABLE IF NOT EXISTS scan_logs (
 id INTEGER PRIMARY KEY, search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
 created_at REAL NOT NULL, event TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS searches_owner ON searches(user_id, created_at);
CREATE INDEX IF NOT EXISTS searches_expiry ON searches(expires_at);
"""


class Storage:
    def __init__(self, path: Path, retention_days=7):
        self.path = path
        self.retention_days = retention_days
        self.lock = asyncio.Lock()
        self.db = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        await self.db.execute("UPDATE searches SET status='interrupted' WHERE status IN ('queued','running')")
        await self.db.commit()
        await self.cleanup()

    async def close(self):
        if self.db:
            await self.db.close()

    async def create_search(self, user_id, username):
        sid = uuid.uuid4().hex
        now = time.time()
        async with self.lock:
            await self.db.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user_id, now))
            await self.db.execute("INSERT INTO searches VALUES (?,?,?,?,?,?,NULL)",
                                  (sid, user_id, username, "queued", now, now + self.retention_days * 86400))
            await self.db.commit()
        return sid

    async def set_status(self, sid, status):
        async with self.lock:
            await self.db.execute("UPDATE searches SET status=? WHERE id=?", (status, sid))
            await self.db.execute("INSERT INTO scan_logs(search_id,created_at,event) VALUES (?,?,?)", (sid, time.time(), status))
            await self.db.commit()

    async def finish(self, result, reports):
        data = result.to_dict()
        sid = result.search_id
        async with self.lock:
            try:
                await self.db.execute("UPDATE searches SET status=?,result_json=? WHERE id=?",
                                      (result.status, json.dumps(data, ensure_ascii=False), sid))
                await self.db.execute("INSERT INTO scan_logs(search_id,created_at,event) VALUES (?,?,?)",
                                      (sid, time.time(), result.status))
                for profile in data["profiles"]:
                    await self.db.execute("INSERT OR REPLACE INTO profiles(search_id,url,platform,data_json) VALUES (?,?,?,?)",
                                          (sid, profile["url"], profile["platform"], json.dumps(profile, ensure_ascii=False)))
                for fmt, content in reports.items():
                    await self.db.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?)", (sid, fmt, content, time.time()))
                for error in result.errors:
                    await self.db.execute("INSERT INTO scan_logs(search_id,created_at,event) VALUES (?,?,?)",
                                          (sid, time.time(), json.dumps(error, ensure_ascii=False)))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def history(self, user_id, limit=10):
        async with self.lock:
            async with self.db.execute("SELECT id,username,status,created_at FROM searches WHERE user_id=? AND expires_at>? ORDER BY created_at DESC LIMIT ?",
                                       (user_id, time.time(), limit)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def get_search(self, sid, user_id):
        async with self.lock:
            async with self.db.execute("SELECT * FROM searches WHERE id=? AND user_id=? AND expires_at>?", (sid, user_id, time.time())) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_report(self, sid, user_id, fmt):
        async with self.lock:
            async with self.db.execute("SELECT r.content FROM reports r JOIN searches s ON s.id=r.search_id WHERE s.id=? AND s.user_id=? AND s.expires_at>? AND r.format=?",
                                       (sid, user_id, time.time(), fmt)) as cursor:
                row = await cursor.fetchone()
                return bytes(row[0]) if row else None

    async def cleanup(self):
        async with self.lock:
            await self.db.execute("DELETE FROM searches WHERE expires_at<=?", (time.time(),))
            await self.db.commit()
            await self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
