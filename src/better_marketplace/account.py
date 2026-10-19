"""Read-only account usage. A spending limit is not a prepaid wallet balance."""

import hashlib
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from . import apify


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def empty(status):
    return {
        "status": status,
        "currency": "USD",
        "used_usd": None,
        "monthly_limit_usd": None,
        "remaining_to_limit_usd": None,
        "plan_included_credits_usd": None,
        "credit_balance_usd": None,
        "plan_name": None,
        "cycle_start_at": None,
        "cycle_end_at": None,
        "checked_at": None,
        "stale": False,
        "message": None,
    }


def read_account():
    # The /users/me response can contain credentials and personal information.
    # Only the explicit financial allowlist below leaves this function.
    with ThreadPoolExecutor(max_workers=2) as pool:
        limits_job = pool.submit(apify.request, "/users/me/limits", timeout=8, attempts=2)
        user_job = pool.submit(apify.request, "/users/me", timeout=8, attempts=2)
        limits = limits_job.result()["data"]
        try:
            user = user_job.result()["data"]
            plan = user.get("plan") if isinstance(user, dict) else None
            plan = plan if isinstance(plan, dict) else {}
        except (RuntimeError, ValueError, KeyError, TypeError):
            plan = {}
    if not isinstance(limits, dict):
        raise ValueError("Unexpected account response")
    current = limits.get("current") or {}
    caps = limits.get("limits") or {}
    cycle = limits.get("monthlyUsageCycle") or {}
    if not all(isinstance(part, dict) for part in (current, caps, cycle)):
        raise ValueError("Unexpected account usage response")
    data = empty("ready")
    used, limit = amount(current.get("monthlyUsageUsd")), amount(caps.get("maxMonthlyUsageUsd"))
    data.update(
        used_usd=used,
        monthly_limit_usd=limit,
        remaining_to_limit_usd=round(max(0, limit - used), 6)
        if used is not None and limit is not None
        else None,
        plan_included_credits_usd=amount(plan.get("monthlyUsageCreditsUsd")),
        plan_name=str(plan.get("id") or plan.get("tier") or "")[:80] or None,
        cycle_start_at=timestamp(cycle.get("startAt")),
        cycle_end_at=timestamp(cycle.get("endAt")),
        checked_at=datetime.now(UTC).isoformat(),
    )
    if used is None or limit is None:
        data.update(status="partial", message="Apify did not provide all usage or limit fields.")
    return data


class AccountUsage:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.token_key = None
        self.value = None
        self.next_refresh = 0
        self.last_attempt = float("-inf")

    def get(self, *, refresh=False):
        with self.lock:
            token = os.getenv("APIFY_TOKEN")
            key = hashlib.sha256(token.encode()).digest() if token else None
            if key != self.token_key:
                self.token_key, self.value = key, None
                self.next_refresh, self.last_attempt = 0, float("-inf")
            if not token:
                return empty("not_configured")
            now = self.clock()
            if self.value and (
                now - self.last_attempt < 5 or (not refresh and now < self.next_refresh)
            ):
                return dict(self.value)
            self.last_attempt = now
            try:
                self.value = read_account()
                self.next_refresh = self.clock() + 60
            except (RuntimeError, ValueError, KeyError, TypeError, OSError):
                previous = self.value if self.value and self.value.get("checked_at") else None
                self.value = {
                    **(previous or empty("unavailable")),
                    "status": "stale" if previous else "unavailable",
                    "stale": bool(previous),
                    "message": "Cannot refresh Apify usage. Check the token's account permissions or open Apify billing.",
                }
                self.next_refresh = self.clock() + 20
            return dict(self.value)
