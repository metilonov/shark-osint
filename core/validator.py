import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def validate_username(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{1,31}", value):
        raise ValueError("Введите один ник: 2–32 символа, латинские буквы, цифры, _, . или -. Без ссылок, пробелов и @.")
    if value.isdigit() or re.fullmatch(r"[\d.\-]+", value):
        raise ValueError("Нужен никнейм, а не номер телефона или IP-адрес.")
    return value


def canonical_url(value: str) -> str:
    try:
        if len(value) > 2048:
            return ""
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
            return ""
        if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".onion", ".i2p")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host:
                return ""
        port = parts.port
        netloc = host if port in {None, 80, 443} else f"{host}:{port}"
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}])
        return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/") or "/", query, ""))
    except (ValueError, TypeError):
        return ""


def url_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return urlunsplit(("", host + (f":{parts.port}" if parts.port else ""), parts.path, parts.query, ""))
