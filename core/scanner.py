import asyncio
import time

from config import Settings
from core.aggregator import aggregate, checked_platforms
from core.validator import canonical_url, url_key
from database.models import ScanResult, utc_now
from engines.maigret_engine import MaigretEngine
from engines.sherlock_engine import SherlockEngine
from engines.spiderfoot_engine import SpiderFootEngine


class Scanner:
    def __init__(self, settings: Settings, engines=None, spiderfoot=None):
        slots = asyncio.Semaphore(settings.max_processes)
        self.engines = engines if engines is not None else [SherlockEngine(settings, slots), MaigretEngine(settings, slots)]
        self.spiderfoot = spiderfoot if spiderfoot is not None else SpiderFootEngine(settings)

    async def scan(self, sid, username, cancel, progress):
        started_at, started = utc_now(), time.monotonic()
        observations, errors = [], []
        found_urls = set()
        states = {engine.name: "waiting" for engine in [*self.engines, self.spiderfoot]}
        planned = {}

        async def publish():
            await progress({"username": username, "engines": dict(states),
                            "found": len(found_urls),
                            "elapsed": time.monotonic() - started, "planned": dict(planned),
                            "checks": len(observations)})

        async def execute(engine):
            if cancel.is_set():
                states[engine.name] = "cancelled"
                return
            async def event(data):
                kind = data.get("type")
                if kind == "state":
                    states[engine.name] = data["state"]
                elif kind == "planned":
                    planned[engine.name] = data["count"]
                elif kind == "observation":
                    obs = data["observation"]
                    observations.append(obs)
                    if obs.status == "claimed" and (url := canonical_url(obs.url)):
                        found_urls.add(url_key(url))
                    if obs.error:
                        errors.append({"engine": engine.name, "platform": obs.platform, "message": obs.error, "at": obs.detected_at})
                elif kind in {"disabled", "notice"}:
                    errors.append({"engine": engine.name, "message": data["message"], "at": utc_now()})
                # Telegram editing is throttled by the bot layer.
                await publish()
            try:
                states[engine.name] = await engine.run(username, cancel, event)
            except asyncio.CancelledError:
                states[engine.name] = "cancelled"
                raise
            except Exception as error:
                from engines.process import EngineFailure
                message = str(error) if isinstance(error, EngineFailure) else f"Ошибка движка: {type(error).__name__}"
                states[engine.name] = "failed"
                errors.append({"engine": engine.name, "message": message, "at": utc_now()})
            await publish()

        await publish()
        # Structured concurrency ensures cancellation cannot leave child engines running.
        async with asyncio.TaskGroup() as group:
            for engine in self.engines:
                group.create_task(execute(engine))
        await execute(self.spiderfoot)
        checked = [obs for obs in observations if obs.status != "illegal" and obs.engine != "SpiderFoot"]
        meaningful = [obs for obs in checked if obs.status in {"claimed", "available"}]
        if cancel.is_set():
            status = "cancelled"
        elif not meaningful:
            status = "failed"
        elif any(states[e.name] == "failed" for e in self.engines) or any(o.status == "unknown" for o in checked) or states[self.spiderfoot.name] == "failed":
            status = "partial"
        else:
            status = "completed"
        return ScanResult(sid, username, started_at, utc_now(), time.monotonic() - started,
                          status, aggregate(observations), checked_platforms(checked), len(checked), states, errors)
