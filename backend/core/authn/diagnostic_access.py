from __future__ import annotations

import ipaddress
import socket
from typing import Iterable
from urllib.parse import urlparse

from fastapi import HTTPException, status

from core.settings.config import settings

_LOCAL_HOSTS = {"localhost", "localhost.localdomain"}
_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
}


def _resolve_host_ips(hostname: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unable to resolve host: {hostname}") from exc

    resolved: set[str] = set()
    for entry in infos:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        ip = sockaddr[0]
        if ip:
            resolved.add(ip)
    return resolved


def _is_blocked_ip(ip_values: Iterable[str]) -> bool:
    for raw_ip in ip_values:
        try:
            ip_addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if any(
            (
                ip_addr.is_private,
                ip_addr.is_loopback,
                ip_addr.is_link_local,
                ip_addr.is_multicast,
                ip_addr.is_reserved,
                ip_addr.is_unspecified,
            )
        ):
            return True
    return False


def validate_outbound_http_url(raw_url: str, *, allow_private_networks: bool | None = None) -> str:
    candidate = (raw_url or "").strip()
    if not candidate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target URL is required")

    normalized = candidate if "://" in candidate else f"https://{candidate}"
    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only HTTP(S) targets are allowed")

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target host is required")

    if allow_private_networks is None:
        allow_private_networks = settings.IS_DEVELOPMENT

    if allow_private_networks:
        return normalized

    if hostname in _LOCAL_HOSTS or hostname in _METADATA_HOSTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Local or metadata targets are not allowed")

    resolved_ips = _resolve_host_ips(hostname)
    if _is_blocked_ip(resolved_ips):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private or loopback targets are not allowed")

    return normalized
