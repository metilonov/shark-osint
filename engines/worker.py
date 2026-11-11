"""Isolated workers for the pinned, inspected upstream Python APIs.

stdout is an NDJSON protocol; third-party output is discarded. No shell is used.
"""
import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import asdict
import importlib
import json
import logging
from pathlib import Path
import sys
import ssl

from core.validator import canonical_url, validate_username
from database.models import Observation

OUTPUT = sys.stdout
BLOCKED = ("leak", "breach", "dehashed", "snusbase", "intelx", "password", "pwned")


def emit(event):
    OUTPUT.write(json.dumps(event, ensure_ascii=False) + "\n")
    OUTPUT.flush()


def allowed_site(name, url, headers=None):
    return (not any(x in (name + " " + url).lower() for x in BLOCKED)
            and bool(canonical_url(url.replace("{}", "test").replace("{username}", "test")))
            and not any(k.lower() in {"authorization", "cookie", "x-api-key", "x-guest-token"} for k in (headers or {})))


def sherlock_sites(limit):
    package = importlib.import_module("sherlock_project")
    # Load the packaged registry locally: offline start and no hidden DB-update requests.
    data = json.loads((Path(package.__file__).parent / "resources" / "data.json").read_text(encoding="utf-8"))
    return dict(list((name, site) for name, site in data.items()
                     if name != "$schema" and not site.get("isNSFW")
                     and allowed_site(name, site.get("url", ""), site.get("headers")))[:limit])


def maigret_sites(limit):
    import maigret
    from maigret.sites import MaigretDatabase
    path = Path(maigret.__file__).parent / "resources" / "data.json"
    db = MaigretDatabase().load_from_path(str(path))
    ranked = db.ranked_sites_dict(top=100000, disabled=False)
    return dict(list((name, site) for name, site in ranked.items()
                     if not site.disabled and site.type == "username"
                     and not site.protection and not site.activation
                     and not site.source and not site.url_subpath
                     and not any(tag in {"nsfw", "porn", "leaks", "breach"} for tag in site.tags)
                     and allowed_site(name, site.url, site.headers)
                     and (not site.url_probe or allowed_site(name, site.url_probe, site.headers)))[:limit])


def http_code(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def normalise(engine, name, row):
    result = row["status"]
    status = result.status.name.lower()
    if status == "waf":
        status = "unknown"
    code = http_code(row.get("http_status"))
    # A rate limit, denial or server failure must never become a positive profile.
    if code is not None and (code in {401, 403, 429} or code >= 500):
        status = "unknown"
    metadata = {}
    if engine == "Maigret":
        metadata["tags"] = list(getattr(result, "tags", []) or [])[:20]
        ids = getattr(result, "ids_data", None) or {}
        for key in ("username", "name", "fullname", "display_name", "followers", "following", "posts_count", "created_at"):
            value = ids.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                metadata[key] = str(value)[:256]
    error = ""
    if status == "unknown":
        err = getattr(result, "error", None)
        title = getattr(err, "type", None) or getattr(err, "error_type", None)
        error = str(title or (f"HTTP {code}" if code else "Сеть, таймаут или антибот-защита; результат не определён"))[:160]
    return Observation(engine=engine, platform=name,
                       url=row.get("url_user") or result.site_url_user or "",
                       status=status, http_status=code, metadata=metadata, error=error,
                       checked_url=row.get("url_probe") or row.get("url_user") or result.site_url_user or "")


def run_sherlock(username, sites, timeout, connections):
    from sherlock_project.sherlock import sherlock, interpolate_string
    from sherlock_project.notify import QueryNotify
    names = list(sites)
    # Upstream creates up to len(batch) threads; batching bounds its concurrency.
    for offset in range(0, len(names), connections):
        batch = {name: sites[name] for name in names[offset:offset + connections]}
        rows = sherlock(username, batch, QueryNotify(), timeout=timeout)
        for name, row in rows.items():
            if batch[name].get("urlProbe"):
                row["url_probe"] = interpolate_string(batch[name]["urlProbe"], username)
            emit({"type": "observation", "data": asdict(normalise("Sherlock", name, row))})


async def run_maigret(username, sites, timeout, connections):
    import maigret
    import maigret.checking as checking
    # 0.6.6 creates an unverified SSLContext internally. Override only its connector
    # factory in this isolated worker; preserve env CA trust and reject invalid TLS.
    original_connector = checking.TCPConnector
    def verified_connector(*args, **kwargs):
        kwargs["ssl"] = ssl.create_default_context()
        return original_connector(*args, **kwargs)
    checking.TCPConnector = verified_connector
    logger = logging.getLogger("shark.worker.maigret")
    logger.setLevel(logging.CRITICAL)
    names = list(sites)
    try:
        for offset in range(0, len(names), connections):
            batch = {name: sites[name] for name in names[offset:offset + connections]}
            rows = await maigret.search(
                username, batch, logger, timeout=timeout, max_connections=connections,
                is_parsing_enabled=True, is_enrich_enabled=False,
                no_progressbar=True, retries=0, check_domains=False,
                cloudflare_bypass=None, dns_resolver="threaded",
            )
            for name, row in rows.items():
                emit({"type": "observation", "data": asdict(normalise("Maigret", name, row))})
    finally:
        checking.TCPConnector = original_connector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=["sherlock", "maigret"])
    parser.add_argument("username", nargs="?")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--connections", type=int, default=8)
    args = parser.parse_args()
    try:
        with open(__import__("os").devnull, "w") as sink, redirect_stdout(sink):
            sites = sherlock_sites(args.limit) if args.engine == "sherlock" else maigret_sites(args.limit)
            emit({"type": "planned", "count": len(sites)})
            if args.probe:
                return
            username = validate_username(args.username or "")
            if not sites:
                raise RuntimeError("Нет разрешённых платформ в локальной базе инструмента")
            if args.engine == "sherlock":
                run_sherlock(username, sites, args.timeout, args.connections)
            else:
                asyncio.run(run_maigret(username, sites, args.timeout, args.connections))
            emit({"type": "done"})
    except Exception as error:
        # Deliberately omit arbitrary exceptions/HTML/proxy URLs from upstream.
        emit({"type": "error", "message": f"Ошибка адаптера: {type(error).__name__}"})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
