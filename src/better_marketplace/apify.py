"""Bounded Apify adapter. Never retries a run creation with an ambiguous result."""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API = "https://api.apify.com/v2"
ACTOR = "curious_coder~facebook-marketplace"


def cars_search_url(center, term):
    """Use the actor's documented city/category URL mode, not all-category search."""
    location = center["marketplace_location"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", location):
        raise ValueError("Invalid Marketplace city slug/ID")
    query = urllib.parse.urlencode(
        {
            "query": term,
            "sortBy": "creation_time_descend",
            "radius": center["radius_km"],
            "exact": "true",
        }
    )
    return f"https://www.facebook.com/marketplace/{location}/cars/?{query}"


def is_cars_search_url(url):
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.facebook.com"
        and bool(re.fullmatch(r"/marketplace/[A-Za-z0-9_-]+/cars/", parsed.path))
        and bool(query.get("query", [""])[0])
        and query.get("radius", [""])[0].isdigit()
        and 1 <= int(query["radius"][0]) <= 500
    )


def request(path, *, payload=None, timeout=30, attempts=3):
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise ValueError(
            "Set APIFY_TOKEN in your environment, or import an Apify JSON export instead."
        )
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    # GET retries are bounded; POST never retries, including rate limits and timeouts.
    total_attempts = max(1, min(3, attempts)) if payload is None else 1
    for attempt in range(total_attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if (
                payload is None
                and exc.code in {429, 500, 502, 503, 504}
                and attempt < total_attempts - 1
            ):
                retry_after = exc.headers.get("Retry-After", "")
                delay = (
                    min(30, max(2 ** (attempt + 1), int(retry_after)))
                    if retry_after.isdigit()
                    else 2 ** (attempt + 1)
                )
                time.sleep(delay)
                continue
            raise RuntimeError(
                f"Apify HTTP {exc.code}. No run was retried; check Apify Console before starting another."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError(
                "Apify request outcome could not be confirmed. No automatic retry; check Apify Console. Credentials and response bodies are not logged."
            ) from None


def plan(config, output, broad=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    terms = config["broad_search_terms"] if broad else config["search_terms"]
    manifest = []
    for center in config["search_centers"]:
        for term in terms:
            payload = {
                "urls": [cars_search_url(center, term)],
                "getListingDetails": True,
                "getAllListingPhotos": False,
                "strictFiltering": True,
                "onlyNewListings": False,
                "maxPagesPerUrl": 2,
            }
            name = f"{'broad' if broad else 'target'}-{len(manifest):03d}.json"
            (output / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            manifest.append(
                {
                    "input": name,
                    "center": center["name"],
                    "query": term,
                    "category": "Cars & Trucks",
                }
            )
    (output / ("broad-manifest.json" if broad else "manifest.json")).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def refresh_plan(items, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    urls = [
        f"https://www.facebook.com/marketplace/item/{i['id']}/"
        for i in items
        if i["status"] != "rejected" and i["id"].isdigit()
    ]
    paths = []
    for offset in range(0, len(urls), 50):
        path = output / f"refresh-{offset // 50:03d}.json"
        path.write_text(
            json.dumps(
                {
                    "urls": urls[offset : offset + 50],
                    "getListingDetails": True,
                    "getAllListingPhotos": False,
                    "onlyNewListings": False,
                    "maxPagesPerUrl": 1,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        paths.append(str(path))
    return paths


def start(payload, run_key, journal_dir, max_charge=1.0):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_key):
        raise ValueError("Run key must contain 1–80 letters, numbers, underscores or hyphens")
    if not 0 < max_charge <= 10:
        raise ValueError("Per-run charge cap must be above $0 and at most $10")
    allowed = {
        "searchKeyword",
        "latitude",
        "longitude",
        "radiusKm",
        "sortBy",
        "getListingDetails",
        "getAllListingPhotos",
        "strictFiltering",
        "onlyNewListings",
        "maxPagesPerUrl",
        "urls",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError(
            "Use a generated plan input. Authentication/cookie/proxy overrides are not supported."
        )
    if (
        payload.get("getListingDetails") is not True
        or payload.get("getAllListingPhotos") is not False
    ):
        raise ValueError("Full listing details must be enabled and all-photo fetching disabled")
    if payload.get("onlyNewListings") is not False:
        raise ValueError("Keep onlyNewListings false to refresh description edits")
    if (
        not isinstance(payload.get("maxPagesPerUrl"), int)
        or not 1 <= payload["maxPagesPerUrl"] <= 5
    ):
        raise ValueError("maxPagesPerUrl must be between 1 and 5")
    if "urls" in payload:
        urls = payload["urls"]
        if (
            not isinstance(urls, list)
            or not 1 <= len(urls) <= 50
            or not all(
                isinstance(u, str)
                and (
                    re.fullmatch(r"https://www\.facebook\.com/marketplace/item/\d+/", u)
                    or is_cars_search_url(u)
                )
                for u in urls
            )
        ):
            raise ValueError("Use 1–50 Facebook item URLs or Cars & Trucks search URLs")
        if any(is_cars_search_url(u) for u in urls) and payload.get("strictFiltering") is not True:
            raise ValueError("Cars & Trucks searches require strictFiltering=true")
    else:
        raise ValueError("All-category searches are disabled. Regenerate a Cars & Trucks plan.")
    if not os.environ.get("APIFY_TOKEN"):
        raise ValueError("Set APIFY_TOKEN in your environment before starting a paid collection")
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = journal_dir / (run_key + ".json")
    entry = {
        "run_key": run_key,
        "status": "unknown",
        "started_at": datetime.now(UTC).isoformat(),
        "input": payload,
        "max_charge_usd": max_charge,
    }
    try:
        with journal.open("x", encoding="utf-8") as file:
            json.dump(entry, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError:
        raise ValueError(
            f"Run key already reserved: {run_key}. Inspect its journal/Apify Console; use fetch for an existing run."
        ) from None
    query = urllib.parse.urlencode(
        {"timeout": 300, "maxTotalChargeUsd": max_charge, "maxItems": 250}
    )
    data = request(f"/acts/{ACTOR}/runs?{query}", payload=payload)["data"]
    entry.update(
        {"status": data["status"], "run_id": data["id"], "dataset_id": data.get("defaultDatasetId")}
    )
    journal.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    return entry


def fetch(run_id, max_records=1000):
    if not re.fullmatch(r"[A-Za-z0-9]{1,100}", run_id):
        raise ValueError("Invalid Apify run ID")
    if not 1 <= max_records <= 10000:
        raise ValueError("Fetch limit must be 1–10,000 records")
    run = request(f"/actor-runs/{run_id}")["data"]
    if run["status"] != "SUCCEEDED":
        raise ValueError(
            f"Run status: {run['status']}. Fetch imports only successful runs. For READY/RUNNING, call fetch again later; do not start a duplicate run."
        )
    dataset = run["defaultDatasetId"]
    if not re.fullmatch(r"[A-Za-z0-9]{1,100}", dataset):
        raise ValueError("Invalid Apify dataset ID")
    records = []
    # One extra item detects truncation instead of silently pretending the job is complete.
    while len(records) <= max_records:
        limit = min(250, max_records + 1 - len(records))
        page = request(
            f"/datasets/{dataset}/items?format=json&clean=false&offset={len(records)}&limit={limit}"
        )
        if not isinstance(page, list):
            raise ValueError("Unexpected dataset format")
        records.extend(page)
        if len(records) > max_records:
            raise ValueError(
                "Dataset exceeds fetch limit. Raise --limit or export/import it in batches."
            )
        if len(page) < limit:
            break
    listings, notices = [], []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Unexpected non-object dataset record")
        if record.get("id") or record.get("listing_id") or record.get("url"):
            listings.append(record)
        elif isinstance(record.get("error"), str):
            # This actor emits plan-limit notices as dataset rows even on SUCCEEDED runs.
            notices.append(record)
        else:
            raise ValueError("Unrecognized dataset record without a listing ID")
    run["dataset_notices"] = notices
    run["dataset_record_count"] = len(records)
    return listings, run
