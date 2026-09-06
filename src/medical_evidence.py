"""Retrieve bounded, auditable reference snapshots without trusting model-written source notes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests

from pipeline_state import atomic_json, fingerprint, read_json

TRUSTED_HOSTS = {"nhs.uk", "cdc.gov", "healthychildren.org", "aap.org", "nih.gov", "who.int",
                 "medlineplus.gov", "nice.org.uk", "mayoclinic.org", "aafp.org"}
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
MAX_TEXT_CHARACTERS = 40000
MAX_AGE_SECONDS = 24 * 60 * 60


def validate_source_url(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(host == domain or host.endswith("." + domain) for domain in TRUSTED_HOSTS)):
        raise ValueError("Medical reference must use HTTPS on a configured authoritative domain.")


class ArticleText(HTMLParser):
    IGNORED = {"script", "style", "nav", "footer", "header", "head", "noscript", "svg", "form"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.main_text = []
        self.all_text = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            position = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[position:]

    def handle_data(self, data):
        value = re.sub(r"\s+", " ", data).strip()
        if value and not self.IGNORED.intersection(self.stack):
            self.all_text.append(value)
            if "main" in self.stack or "article" in self.stack:
                self.main_text.append(value)

    def text(self):
        return "\n".join(self.main_text or self.all_text)


def fetch_reference(url: str) -> dict:
    current = url
    for _ in range(6):
        validate_source_url(current)
        with requests.get(current, timeout=(10, 30), allow_redirects=False, stream=True,
                          headers={"User-Agent": "ModocMedicalReferenceReview/1.0"}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                current = urljoin(current, response.headers["Location"])
                continue
            response.raise_for_status()
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                raise ValueError("Medical reference is not HTML; provide an accessible article URL.")
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("Medical reference exceeds the size limit.")
        parser = ArticleText()
        parser.feed(raw.decode("utf-8", errors="replace"))
        text = parser.text()
        if len(text) < 200:
            raise ValueError("Medical reference did not contain a readable article.")
        excerpt = text[:MAX_TEXT_CHARACTERS]
        return {"url": url, "resolved_url": current, "status": "retrieved", "text": excerpt,
                "text_fingerprint": fingerprint(excerpt), "truncated": len(excerpt) != len(text)}
    raise ValueError("Too many medical reference redirects.")


def collect_medical_evidence(script: dict, output_dir, refresh: bool = False) -> dict:
    sources = script.get("medical_sources", [])
    source_key = fingerprint(sources)
    path = output_dir / "medical_evidence.json"
    existing = read_json(path, {})
    now = datetime.now(timezone.utc)
    if not refresh and isinstance(existing, dict) and existing.get("source_fingerprint") == source_key:
        try:
            age = (now - datetime.fromisoformat(existing["retrieved_at"])).total_seconds()
            valid = all(s.get("status") != "retrieved" or s.get("text_fingerprint") == fingerprint(s["text"])
                        for s in existing["sources"])
            if 0 <= age < MAX_AGE_SECONDS and valid and existing.get("retrieved_count", 0) > 0:
                return existing
        except (KeyError, TypeError, ValueError):
            pass
    snapshots = []
    for source in sources:
        print(f"Retrieving medical reference: {source['url']}")
        try:
            snapshot = fetch_reference(source["url"])
        except (requests.RequestException, ValueError, KeyError) as error:
            snapshot = {"url": source["url"], "status": "unavailable", "reason": type(error).__name__}
        snapshots.append({"title": source["title"], **snapshot})
    evidence = {"source_fingerprint": source_key, "retrieved_at": now.isoformat(), "sources": snapshots,
                "retrieved_count": sum(s["status"] == "retrieved" for s in snapshots)}
    atomic_json(path, evidence)
    if not evidence["retrieved_count"]:
        raise RuntimeError(f"No readable authoritative medical sources. Publication is blocked: {path}")
    return evidence
