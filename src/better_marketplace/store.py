import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "marketplace.sqlite3"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            for migration in sorted(
                Path(__file__).with_name("migrations").joinpath("sqlite").glob("*.sql")
            ):
                db.executescript(migration.read_text())

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_records(self, records, source, observed_at=None):
        if not isinstance(records, list) or len(records) > 10000:
            raise ValueError("Import a JSON array of at most 10,000 listings")
        timestamp = observed_at or now()
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Missing timezone")
            timestamp = parsed.astimezone(UTC).isoformat()
        except (ValueError, TypeError):
            raise ValueError("Observation timestamp must include a timezone") from None
        validated = []
        for raw in records:
            if not isinstance(raw, dict):
                raise ValueError("Each listing must be an object")
            identifier = str(raw.get("id") or raw.get("listing_id") or "")
            if not identifier:
                match = re.search(r"facebook\.com/marketplace/item/(\d+)", str(raw.get("url", "")))
                identifier = match[1] if match else ""
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
                raise ValueError("Each listing needs an id or a Facebook item URL")
            validated.append((identifier, raw))
        with self.connection() as db:
            before = db.total_changes
            for identifier, raw in validated:
                db.execute(
                    "INSERT OR IGNORE INTO observations(id,listing_id,source,observed_at,raw_json) VALUES (?,?,?,?,?)",
                    (
                        digest([source, identifier, raw]),
                        identifier,
                        source,
                        timestamp,
                        json.dumps(raw),
                    ),
                )
            return db.total_changes - before

    def observations(self):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM observations ORDER BY observed_at, sequence")
            ]

    def revision(self):
        with self.connection() as db:
            return db.execute("SELECT COALESCE(MAX(sequence),0) FROM observations").fetchone()[0]

    def searches(self):
        with self.connection() as db:
            return [
                {
                    "id": row["id"],
                    "search": json.loads(row["config_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in db.execute("SELECT * FROM saved_searches ORDER BY updated_at DESC")
            ]

    def save_search(self, identifier, config):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO saved_searches VALUES (?,?,?)",
                (identifier, json.dumps(config), now()),
            )

    def delete_search(self, identifier):
        with self.connection() as db:
            return db.execute("DELETE FROM saved_searches WHERE id=?", (identifier,)).rowcount

    def bookmarks(self):
        with self.connection() as db:
            return {row[0] for row in db.execute("SELECT listing_id FROM bookmarks")}

    def bookmark(self, identifier, saved):
        with self.connection() as db:
            if saved:
                db.execute("INSERT OR IGNORE INTO bookmarks VALUES (?,?)", (identifier, now()))
            else:
                db.execute("DELETE FROM bookmarks WHERE listing_id=?", (identifier,))

    def job(self, state):
        with self.connection() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                (state["id"], state["request_id"], json.dumps(state), now()),
            )

    def jobs(self):
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT state_json FROM jobs ORDER BY updated_at DESC LIMIT 100"
                )
            ]

    def job_by_request(self, identifier):
        with self.connection() as db:
            row = db.execute(
                "SELECT state_json FROM jobs WHERE request_id=?", (identifier,)
            ).fetchone()
            return json.loads(row[0]) if row else None
