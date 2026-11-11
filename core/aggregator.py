from urllib.parse import urlsplit
from core.validator import canonical_url, url_key
from database.models import Observation, Profile


def aggregate(observations: list[Observation]) -> list[Profile]:
    grouped = {}
    for obs in observations:
        if obs.status != "claimed":
            continue
        url = canonical_url(obs.url)
        if not url:
            continue
        key = url_key(url)
        source = {"engine": obs.engine, "platform": obs.platform,
                  "url": url, "detected_at": obs.detected_at,
                  "checked_url": canonical_url(obs.checked_url) if obs.checked_url else url,
                  "http_status": obs.http_status, "status": obs.status}
        if key not in grouped:
            grouped[key] = Profile(
                platform=(urlsplit(url).hostname or obs.platform).removeprefix("www."),
                url=url, sources=[], metadata={}, accessible=False, confidence="низкая",
            )
        profile = grouped[key]
        profile.sources.append(source)
        if url.startswith("https:"):
            profile.url = url
        profile.metadata[obs.engine] = obs.metadata
        profile.accessible |= (obs.http_status is not None and 200 <= obs.http_status < 300
                               and url_key(source["checked_url"]) == key)
    for profile in grouped.values():
        engines = {s["engine"] for s in profile.sources}
        profile.confidence = "средняя" if profile.accessible and len(engines) >= 2 else "низкая"
    return sorted(grouped.values(), key=lambda p: (p.platform, p.url))


def checked_platforms(observations):
    return len({(urlsplit(canonical_url(o.url)).hostname or o.platform).removeprefix("www.").lower()
                for o in observations if o.status != "illegal"})
