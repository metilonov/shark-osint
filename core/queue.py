import asyncio
from dataclasses import dataclass, field
import logging
import time

from reports.service import build_reports

logger = logging.getLogger(__name__)


@dataclass
class Job:
    sid: str
    user_id: int
    username: str
    progress: object
    finished: object
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


class ScanQueue:
    """One job per user, one running search, bounded queue and a global process limit."""
    def __init__(self, scanner, storage, cooldown=60):
        self.scanner, self.storage = scanner, storage
        self.cooldown = cooldown
        self.queue = asyncio.Queue(maxsize=1)
        self.jobs = {}
        self.last_submission = {}
        self.lock = asyncio.Lock()
        self.worker = None

    async def start(self):
        self.worker = asyncio.create_task(self._run())

    async def submit(self, user_id, username, progress, finished):
        async with self.lock:
            if user_id in self.jobs:
                raise ValueError("У вас уже есть активная проверка. Дождитесь завершения или отмените её.")
            if self.queue.full():
                raise ValueError("Очередь заполнена. Повторите позже.")
            if time.monotonic() - self.last_submission.get(user_id, -1e10) < self.cooldown:
                raise ValueError(f"Между проверками должно пройти {self.cooldown} с.")
            sid = await self.storage.create_search(user_id, username)
            job = Job(sid, user_id, username, progress, finished)
            self.jobs[user_id] = job
            self.last_submission[user_id] = time.monotonic()
            self.queue.put_nowait(job)
            return sid

    def cancel(self, user_id, sid):
        job = self.jobs.get(user_id)
        if job and job.sid == sid:
            job.cancel.set()
            return True
        return False

    async def _run(self):
        while True:
            job = await self.queue.get()
            result = None
            try:
                await self.storage.set_status(job.sid, "running")
                result = await self.scanner.scan(job.sid, job.username, job.cancel, job.progress)
                reports = await asyncio.to_thread(build_reports, result)
                await self.storage.finish(result, reports)
            except asyncio.CancelledError:
                job.cancel.set()
                await self.storage.set_status(job.sid, "interrupted")
                raise
            except Exception as error:
                logger.error("Job failure: %s", type(error).__name__)
                try:
                    if result is not None:
                        # Preserve JSON and results even if optional PDF rendering failed.
                        import json
                        result.errors.append({"engine": "Reports", "message": f"Ошибка обработки: {type(error).__name__}"})
                        result.status = "partial" if result.profiles else "failed"
                        await self.storage.finish(result, {"json": json.dumps(result.to_dict(), ensure_ascii=False).encode()})
                    else:
                        await self.storage.set_status(job.sid, "failed")
                except Exception as storage_error:
                    logger.error("Cannot persist job: %s", type(storage_error).__name__)
                    result = None
            finally:
                self.jobs.pop(job.user_id, None)
                self.queue.task_done()
            try:
                await job.finished(result)
            except Exception:
                logger.error("Cannot deliver final notification; use history")

    async def close(self):
        for job in self.jobs.values():
            job.cancel.set()
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        while not self.queue.empty():
            job = self.queue.get_nowait()
            await self.storage.set_status(job.sid, "interrupted")
            self.queue.task_done()
        self.jobs.clear()
