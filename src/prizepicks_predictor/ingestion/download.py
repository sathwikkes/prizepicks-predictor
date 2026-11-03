"""Cached download of a public release asset."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from prizepicks_predictor.ingestion.base import ProviderError

log = logging.getLogger(__name__)


def download_file(client: httpx.Client, url: str, dest: Path, *, force: bool = False) -> bool:
    """Download ``url`` to ``dest`` unless a non-empty cached copy exists.

    Returns True when a download happened.
    """
    if dest.is_file() and dest.stat().st_size > 0 and not force:
        log.info("using cached %s", dest)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("downloading %s", url)
    try:
        with client.stream("GET", url) as response:
            if response.status_code == 404:
                raise ProviderError(f"not published: {url} (404)")
            if response.status_code >= 400:
                raise ProviderError(f"HTTP {response.status_code} downloading {url}")
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise ProviderError(f"download failed for {url}: {exc}") from exc
    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise ProviderError(f"empty download for {url}")
    tmp.replace(dest)
    return True
