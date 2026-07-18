from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request

try:
    from google.cloud import firestore
except Exception:  # pragma: no cover
    firestore = None

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sslv-watcher")

app = Flask(__name__)


@dataclass
class Listing:
    search_name: str
    listing_type: str
    title: str
    url: str
    summary: str
    published: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    price_eur: float | None = None
    year: int | None = None
    mileage_km: int | None = None
    rooms: int | None = None
    area_m2: float | None = None

    @property
    def id(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()

    @property
    def searchable_text(self) -> str:
        attrs = " ".join(f"{k} {v}" for k, v in self.attributes.items())
        return f"{self.title} {self.summary} {attrs}".lower()


class SeenStore:
    def contains(self, listing_id: str) -> bool:
        raise NotImplementedError

    def add(self, listing: Listing, matched: bool) -> None:
        raise NotImplementedError


class MemorySeenStore(SeenStore):
    _ids: set[str] = set()

    def contains(self, listing_id: str) -> bool:
        return listing_id in self._ids

    def add(self, listing: Listing, matched: bool) -> None:
        self._ids.add(listing.id)


class FirestoreSeenStore(SeenStore):
    def __init__(self, collection: str):
        if firestore is None:
            raise RuntimeError("google-cloud-firestore is unavailable")
        self.client = firestore.Client()
        self.collection = self.client.collection(collection)

    def contains(self, listing_id: str) -> bool:
        return self.collection.document(listing_id).get().exists

    def add(self, listing: Listing, matched: bool) -> None:
        self.collection.document(listing.id).set(
            {
                "url": listing.url,
                "title": listing.title,
                "search_name": listing.search_name,
                "matched": matched,
                "price_eur": listing.price_eur,
                "seen_at": datetime.now(timezone.utc),
            },
            merge=True,
        )


class TelegramNotifier:
    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, listing: Listing) -> None:
        if not self.enabled:
            logger.warning("Telegram is not configured; matched listing: %s", listing.url)
            return

        facts = []
        if listing.price_eur is not None:
            facts.append(f"💶 {listing.price_eur:,.0f} €".replace(",", " "))
        if listing.year is not None:
            facts.append(f"📅 {listing.year}")
        if listing.mileage_km is not None:
            facts.append(f"🛣 {listing.mileage_km:,} km".replace(",", " "))
        if listing.rooms is not None:
            facts.append(f"🚪 {listing.rooms} rooms")
        if listing.area_m2 is not None:
            facts.append(f"📐 {listing.area_m2:g} m²")

        text = (
            f"🔔 <b>{html.escape(listing.search_name)}</b>\n"
            f"<b>{html.escape(listing.title[:300])}</b>\n"
            f"{' · '.join(facts)}\n\n"
            f"{html.escape(strip_html(listing.summary)[:650])}\n\n"
            f"<a href=\"{html.escape(listing.url)}\">Open listing</a>"
        )

        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        response.raise_for_status()


def load_config() -> dict[str, Any]:
    path = os.getenv("CONFIG_PATH", "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def strip_html(value: str) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def first_number(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = (
        value.replace("\xa0", " ")
        .replace("€", "")
        .replace("EUR", "")
        .replace(",", ".")
    )
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)", cleaned)
    return float(match.group(1)) if match else None


def price_number(value: str | None) -> float | None:
    if not value:
        return None

    normalized = value.replace("\xa0", " ")
    match = re.search(
        r"(?<!\d)(\d{1,3}(?:[ .]\d{3})+|\d+(?:[.,]\d+)?)\s*(?:€|EUR)",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return first_number(value)

    number = match.group(1).replace(" ", "")
    if number.count(".") > 1 or ("." in number and len(number.rsplit(".", 1)[1]) == 3):
        number = number.replace(".", "")
    return float(number.replace(",", "."))


def parse_compact_number(value: str | None) -> float | None:
    if not value:
        return None
    text = value.lower().replace("\xa0", " ").replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(tūkst\.?|тыс\.?|k)?", text)
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2):
        number *= 1000
    return number


def extract_detail_attributes(url: str, session: requests.Session, timeout: int) -> tuple[dict[str, str], str]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    attrs: dict[str, str] = {}
    for row in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.select("td, th")]
        if len(cells) == 2 and 0 < len(cells[0]) < 80 and cells[1]:
            key = cells[0].rstrip(":").strip()
            attrs.setdefault(key, cells[1].strip())

    description = ""
    for selector in ("#msg_div_msg", ".ads_opt", "[id*='msg_div']"):
        node = soup.select_one(selector)
        if node:
            description = node.get_text(" ", strip=True)
            break

    return attrs, description


def normalize_listing(listing: Listing) -> Listing:
    text = listing.searchable_text
    attrs_lower = {k.lower(): v for k, v in listing.attributes.items()}

    def attr_value(*needles: str) -> str | None:
        for key, value in attrs_lower.items():
            if any(n.lower() in key for n in needles):
                return value
        return None

    price_source = attr_value("cena", "цена") or text
    listing.price_eur = price_number(price_source)

    if listing.listing_type == "car":
        year_source = attr_value("izlaiduma gads", "gads", "год выпуска")
        year = first_number(year_source)
        if year and 1950 <= year <= 2100:
            listing.year = int(year)

        mileage_source = attr_value("nobraukums", "пробег")
        mileage = parse_compact_number(mileage_source)
        if mileage is not None:
            listing.mileage_km = int(mileage)

    if listing.listing_type == "apartment":
        rooms = first_number(attr_value("istabas", "комнат"))
        if rooms is not None:
            listing.rooms = int(rooms)

        area = first_number(attr_value("platība", "площадь"))
        if area is not None:
            listing.area_m2 = area

    return listing


def match_scalar(value: float | int | None, rule: dict[str, Any] | None) -> bool:
    if not rule:
        return True
    if value is None:
        return False
    if "min" in rule and value < rule["min"]:
        return False
    if "max" in rule and value > rule["max"]:
        return False
    return True


def find_attribute(attributes: dict[str, str], configured_name: str) -> str | None:
    target = configured_name.lower()
    for key, value in attributes.items():
        key_lower = key.lower()
        if key_lower == target or target in key_lower or key_lower in target:
            return value
    return None


def match_attribute_rule(value: str | None, rule: Any) -> bool:
    if value is None:
        return False

    if isinstance(rule, str):
        return rule.lower() in value.lower()

    if not isinstance(rule, dict):
        return True

    numeric = parse_compact_number(value)
    if "min" in rule and (numeric is None or numeric < rule["min"]):
        return False
    if "max" in rule and (numeric is None or numeric > rule["max"]):
        return False

    lowered = value.lower()
    if rule.get("contains_any"):
        if not any(str(x).lower() in lowered for x in rule["contains_any"]):
            return False
    if rule.get("contains_all"):
        if not all(str(x).lower() in lowered for x in rule["contains_all"]):
            return False
    if rule.get("not_contains_any"):
        if any(str(x).lower() in lowered for x in rule["not_contains_any"]):
            return False

    return True


def matches(listing: Listing, filters: dict[str, Any]) -> bool:
    text = listing.searchable_text

    include_any = [str(x).lower() for x in filters.get("include_any", [])]
    if include_any and not any(x in text for x in include_any):
        return False

    required_all = [str(x).lower() for x in filters.get("required_all", [])]
    if required_all and not all(x in text for x in required_all):
        return False

    exclude_any = [str(x).lower() for x in filters.get("exclude_any", [])]
    if any(x in text for x in exclude_any):
        return False

    if not match_scalar(listing.price_eur, filters.get("price_eur")):
        return False
    if not match_scalar(listing.year, filters.get("year")):
        return False
    if not match_scalar(listing.mileage_km, filters.get("mileage_km")):
        return False
    if not match_scalar(listing.rooms, filters.get("rooms")):
        return False
    if not match_scalar(listing.area_m2, filters.get("area_m2")):
        return False

    for name, rule in filters.get("detail_attributes", {}).items():
        if not match_attribute_rule(find_attribute(listing.attributes, name), rule):
            return False

    return True


def build_store(settings: dict[str, Any]) -> SeenStore:
    backend = os.getenv("STORAGE_BACKEND", settings.get("storage_backend", "firestore"))
    if backend == "memory":
        return MemorySeenStore()
    return FirestoreSeenStore(settings.get("firestore_collection", "sslv_watcher_seen"))


def run_searches() -> dict[str, Any]:
    config = load_config()
    settings = config.get("settings", {})
    timeout = int(settings.get("request_timeout_seconds", 20))
    max_items = int(settings.get("max_items_per_feed", 40))
    delay = float(settings.get("delay_between_detail_requests_seconds", 1.5))
    first_run_send_existing = bool(settings.get("first_run_send_existing", False))

    store = build_store(settings)
    notifier = TelegramNotifier()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": settings.get(
                "user_agent",
                "SSLV-Watcher/1.0 personal-use",
            )
        }
    )

    stats = {"feeds": 0, "checked": 0, "new": 0, "matched": 0, "errors": []}

    for search in config.get("searches", []):
        if not search.get("enabled", True):
            continue

        stats["feeds"] += 1
        try:
            response = session.get(search["rss_url"], timeout=timeout)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:
            logger.exception("Could not load feed %s", search.get("name"))
            stats["errors"].append(f"{search.get('name')}: feed error: {exc}")
            continue

        for entry in feed.entries[:max_items]:
            stats["checked"] += 1
            url = urljoin(search["rss_url"], entry.get("link", ""))
            listing = Listing(
                search_name=search["name"],
                listing_type=search.get("type", "generic"),
                title=strip_html(entry.get("title", "")),
                url=url,
                summary=entry.get("summary", entry.get("description", "")),
                published=entry.get("published"),
            )

            if not listing.url or store.contains(listing.id):
                continue

            stats["new"] += 1
            detail_failed = False
            try:
                attrs, detail_description = extract_detail_attributes(url, session, timeout)
                listing.attributes = attrs
                if detail_description:
                    listing.summary = detail_description
                normalize_listing(listing)
            except Exception as exc:
                detail_failed = True
                logger.warning("Detail fetch failed for %s: %s", url, exc)
                normalize_listing(listing)

            is_match = matches(listing, search.get("filters", {}))
            store.add(listing, is_match)

            # On the first execution, default behavior is to establish the baseline
            # without spamming every existing RSS entry.
            baseline_marker = f"baseline::{search['name']}"
            is_baseline_missing = not store.contains(hashlib.sha256(baseline_marker.encode()).hexdigest())
            if is_match and (first_run_send_existing or not is_baseline_missing):
                notifier.send(listing)
                stats["matched"] += 1

            if delay and not detail_failed:
                time.sleep(delay)

        baseline = Listing(
            search_name=search["name"],
            listing_type="baseline",
            title="baseline",
            url=f"baseline::{search['name']}",
            summary="",
        )
        store.add(baseline, False)

    return stats


@app.get("/")
def health() -> tuple[dict[str, Any], int]:
    return {"status": "ok", "service": "sslv-watcher"}, 200


@app.post("/run")
def trigger() -> tuple[Any, int]:
    expected = os.getenv("RUN_TOKEN")
    if expected and request.headers.get("X-Run-Token") != expected:
        return jsonify({"error": "unauthorized"}), 401

    try:
        return jsonify(run_searches()), 200
    except Exception as exc:
        logger.exception("Run failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
