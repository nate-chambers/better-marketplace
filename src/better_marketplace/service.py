import csv
import io
import json
import threading
from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urlparse

from .filtering import extract
from .store import digest


def reason_group(reason):
    text = reason.lower()
    if "conflict" in text or "rebadge" in text:
        return "Conflicting evidence"
    if "outside cars" in text or "component listing" in text or "parts /" in text:
        return "Parts / non-vehicle"
    if "wanted listing" in text or "sold, pending" in text:
        return "Unavailable / wanted"
    if "non-target" in text or "not a target" in text or "different bmw" in text:
        return "Wrong make / model"
    if text.startswith("year ") and ("before" in text or "after" in text):
        return "Year out of range"
    if (text.startswith("mileage ") and "not below" in text) or "odometer values exceed" in text:
        return "Mileage too high"
    if (text.startswith("price ") and ("exceeds" in text or "minimum" in text)) or (
        text.startswith("displayed price ") and "exceeds" in text
    ):
        return "Price out of range"
    if text.startswith("outside "):
        return "Outside search area"
    if "mileage" in text or "odometer" in text:
        return "Mileage uncertain"
    if "price" in text or "currency" in text or "financing" in text:
        return "Price uncertain"
    if "stale" in text:
        return "Needs a refresh"
    if "transmission" in text:
        return "Transmission"
    if "location" in text or "coordinates" in text:
        return "Location uncertain"
    return "Missing vehicle details"


def safe_photo(raw):
    candidates = [raw.get("primary_listing_photo_url"), raw.get("image_url")]
    photo = raw.get("primary_listing_photo") or {}
    if isinstance(photo, dict) and isinstance(photo.get("image"), dict):
        candidates.append(photo["image"].get("uri"))
    for value in candidates:
        if isinstance(value, str) and (
            value.startswith("/static/demo/")
            or (urlparse(value).scheme == "https" and not urlparse(value).username)
        ):
            return value
    return None


class Listings:
    def __init__(self, store):
        self.store = store
        self.cache = {}
        self.lock = threading.RLock()

    def evaluated(self, search):
        key = (
            self.store.revision(),
            digest(search.model_dump()),
            datetime.now(UTC).strftime("%Y-%m-%d-%H"),
        )
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            rows = self.store.observations()
            latest, history = {}, {}
            # Only evaluate current observations; historical prices retain their raw source.
            for row in rows:
                raw = json.loads(row["raw_json"])
                price = raw.get("listing_price", raw.get("price"))
                price = price.get("amount") if isinstance(price, dict) else price
                entries = history.setdefault(row["listing_id"], [])
                if not entries or entries[-1]["displayed_price"] != price:
                    entries.append(
                        {
                            "observed_at": row["observed_at"],
                            "displayed_price": price,
                            "observation_id": row["id"],
                        }
                    )
                latest[row["listing_id"]] = (row, raw)
            output = []
            for identifier, (row, raw) in latest.items():
                item = extract(raw, search.rules())
                age = max(
                    0,
                    (
                        datetime.now(UTC)
                        - datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                    ).total_seconds()
                    / 86400,
                )
                if age > search.stale_after_days and item["status"] == "match":
                    item["status"] = "needs_review"
                    item["reasons"].append(
                        f"Stale observation ({int(age)} days); refresh listing details"
                    )
                item.update(
                    {
                        "id": identifier,
                        "observation_id": row["id"],
                        "observed_at": row["observed_at"],
                        "source": row["source"],
                        "image_url": safe_photo(raw),
                        "price_history": history[identifier],
                        "url": f"https://www.facebook.com/marketplace/item/{identifier}/"
                        if identifier.isdigit()
                        else None,
                        "demo": row["source"] == "sample",
                        "stale": age > search.stale_after_days,
                        "reason_groups": sorted({reason_group(r) for r in item["reasons"]}),
                        "model_evidence": sorted(
                            {str(e["value"]) for e in item["evidence"] if e["field"] == "model"}
                        ),
                        "displayed_price": next(
                            (
                                e["value"]
                                for e in item["evidence"]
                                if e["field"] == "displayed_price"
                            ),
                            None,
                        ),
                    }
                )
                output.append(item)
            self.cache = {key: output, **dict(list(self.cache.items())[-5:])}
            return output

    def query(self, query, *, all_pages=False):
        items = self.evaluated(query.search)
        bookmarks = self.store.bookmarks()
        counts = Counter(i["status"] for i in items)
        counts.update({"all": len(items), "saved": sum(i["id"] in bookmarks for i in items)})
        text = query.text.lower().strip()
        filtered = [
            i
            for i in items
            if (
                not text
                or text
                in " ".join([i["title"], i["description"], i["location"], *i["reasons"]]).lower()
            )
            and (not query.model or query.model in i["model_evidence"])
        ]
        reasons = Counter(
            r for i in filtered if i["status"] == "rejected" for r in i["reason_groups"]
        )
        models = Counter(m for i in items for m in i["model_evidence"])
        filtered = [
            i
            for i in filtered
            if (
                query.status == "all"
                or (query.status == "saved" and i["id"] in bookmarks)
                or query.status == i["status"]
            )
            and (not query.reason or query.reason in i["reason_groups"])
        ]
        sort = query.sort
        if sort in {"price_asc", "price_desc", "miles", "year"}:
            field = "price" if sort.startswith("price") else sort
            sign = -1 if sort in {"price_desc", "year"} else 1
            filtered.sort(key=lambda i: (i[field] is None, sign * (i[field] or 0), i["id"]))
        elif sort == "newest":
            filtered.sort(key=lambda i: (i["observed_at"], i["id"]), reverse=True)
        elif sort == "model":
            filtered.sort(key=lambda i: (i["model"] is None, i["model"] or "", i["id"]))
        else:
            filtered.sort(key=lambda i: (", ".join(i["reason_groups"]), i["id"]))
        total = len(filtered)
        page = (
            filtered
            if all_pages
            else filtered[(query.page - 1) * query.page_size : query.page * query.page_size]
        )
        return {
            "items": [{**i, "saved": i["id"] in bookmarks} for i in page],
            "total": total,
            "counts": dict(counts),
            "reasons": dict(reasons),
            "models": dict(models),
            "page": query.page,
            "page_size": query.page_size,
        }


def export_csv(items):
    buf = io.StringIO()
    columns = [
        "id",
        "title",
        "make",
        "model",
        "year",
        "price",
        "miles",
        "location",
        "status",
        "reasons",
        "observed_at",
        "url",
    ]
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for item in items:
        row = {k: item.get(k) for k in columns}
        row["reasons"] = "; ".join(row["reasons"])
        writer.writerow(
            {
                k: "'" + v
                if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                else v
                for k, v in row.items()
            }
        )
    return "\ufeff" + buf.getvalue()
