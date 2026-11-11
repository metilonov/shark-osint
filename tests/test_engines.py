from datetime import timedelta
import importlib
import ssl

from aioresponses import aioresponses
import pytest
import requests

from engines import worker


def test_sherlock_real_library_with_mock_http(monkeypatch):
    emitted = []
    monkeypatch.setattr(worker, "emit", emitted.append)
    def response(session, method, url, **kwargs):
        result = requests.Response()
        result.status_code = 200
        result._content = b"Public profile Owner"
        result.url = url
        result.elapsed = timedelta(seconds=0.1)
        return result
    monkeypatch.setattr(requests.Session, "request", response)
    sites = {"Example": {"urlMain": "https://example.com", "url": "https://example.com/u/{}", "errorType": "status_code", "errorCode": 404}}
    worker.run_sherlock("Owner", sites, 1, 1)
    assert emitted[0]["data"]["status"] == "claimed"
    assert emitted[0]["data"]["http_status"] == 200


def test_sherlock_real_library_without_internet(monkeypatch):
    emitted = []
    monkeypatch.setattr(worker, "emit", emitted.append)
    def offline(*args, **kwargs):
        raise requests.ConnectionError("simulated offline")
    monkeypatch.setattr(requests.Session, "request", offline)
    sites = {"Example": {"urlMain": "https://example.com", "url": "https://example.com/u/{}", "errorType": "status_code", "errorCode": 404}}
    worker.run_sherlock("Owner", sites, 1, 1)
    assert emitted[0]["data"]["status"] == "unknown"
    assert emitted[0]["data"]["error"]


async def test_maigret_real_library_with_mock_http(monkeypatch):
    from maigret.sites import MaigretSite
    import maigret.checking as checking
    emitted = []
    monkeypatch.setattr(worker, "emit", emitted.append)
    connectors = []
    original = checking.TCPConnector
    def capture(*args, **kwargs):
        connectors.append(kwargs.get("ssl"))
        return original(*args, **kwargs)
    monkeypatch.setattr(checking, "TCPConnector", capture)
    site = MaigretSite("Example", {"urlMain": "https://example.com", "url": "https://example.com/u/{username}",
                                  "checkType": "message", "presenseStrs": ["Public profile"], "absenceStrs": ["Not Found"]})
    with aioresponses() as mock:
        mock.get("https://example.com/u/Owner", body="<html>Public profile Owner</html>", status=200)
        await worker.run_maigret("Owner", {"Example": site}, 1, 1)
    assert emitted[0]["data"]["status"] == "claimed"
    assert emitted[0]["data"]["http_status"] == 200
    assert connectors and all(c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname for c in connectors)


async def test_maigret_no_internet_records_unknown(monkeypatch):
    from maigret.sites import MaigretSite
    emitted = []
    monkeypatch.setattr(worker, "emit", emitted.append)
    site = MaigretSite("Example", {"urlMain": "https://example.com", "url": "https://example.com/u/{username}", "checkType": "status_code"})
    with aioresponses() as mock:
        mock.get("https://example.com/u/Owner", exception=OSError("offline"))
        await worker.run_maigret("Owner", {"Example": site}, 1, 1)
    assert emitted[0]["data"]["status"] == "unknown"


def test_rate_limit_cannot_be_claimed():
    from sherlock_project.result import QueryResult, QueryStatus
    result = QueryResult("Owner", "Example", "https://example.com/u/Owner", QueryStatus.CLAIMED)
    observation = worker.normalise("Sherlock", "Example", {"status": result, "http_status": 429})
    assert observation.status == "unknown"


def test_bundled_sites_do_not_enable_bypass_or_credentials():
    assert worker.sherlock_sites(500)
    sites = worker.maigret_sites(500)
    assert len(sites) == 500
    assert all(not site.activation and not site.protection and not site.source for site in sites.values())
    assert not worker.allowed_site("Leaks", "https://leaks.example/u/{username}")
    assert not worker.allowed_site("Auth", "https://example.com/u/{username}", {"Authorization": "secret"})
