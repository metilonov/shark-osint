import pytest

from core.aggregator import aggregate, checked_platforms
from core.validator import canonical_url, validate_username
from database.models import Observation


def test_merge_sources_and_never_claim_identity(observations):
    profiles = aggregate(observations)
    assert len(profiles) == 2
    merged = next(p for p in profiles if p.platform == "example.com")
    assert len(merged.sources) == 2
    assert merged.confidence == "средняя"
    assert merged.possible_false_positive is True
    assert merged.accessible is True
    assert merged.metadata["Maigret"]["display_name"] == "Public display name"
    assert all(s["detected_at"] for s in merged.sources)
    assert checked_platforms(observations) == 3


def test_urls_preserve_case_and_identity_parameters():
    profiles = aggregate([Observation("Sherlock", "X", url, "claimed", 200) for url in
                          ["https://example.com/Owner", "https://example.com/owner",
                           "https://example.com/user?id=1", "https://example.com/user?id=2"]])
    assert len(profiles) == 4
    assert canonical_url("https://EXAMPLE.com/u/a/?utm_source=x#fragment") == "https://example.com/u/a"


def test_separate_probe_api_does_not_confirm_profile_page():
    profiles = aggregate([Observation("Maigret", "Example", "https://example.com/Owner", "claimed", 200,
                                      checked_url="https://api.example.com/user?name=Owner")])
    assert profiles[0].accessible is False
    assert profiles[0].sources[0]["checked_url"].startswith("https://api.example.com/")


@pytest.mark.parametrize("value", ["", "1234567", "+79991234567", "127.0.0.1", "two names", "a\nb", "foo@example.com", "https://example.com", "a;whoami", "a" * 33])
def test_username_rejects_bulk_phone_and_injection(value):
    with pytest.raises(ValueError):
        validate_username(value)


@pytest.mark.parametrize("value", ["Shadow_777", "owner.name", "test-user", "ab", "Owner01"])
def test_valid_username(value):
    assert validate_username(value) == value


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://localhost/test", "http://127.0.0.1/test", "http://10.0.0.1/u", "https://user:password@example.com/u", "https://hidden.onion/u"])
def test_unsafe_urls_rejected(url):
    assert canonical_url(url) == ""
