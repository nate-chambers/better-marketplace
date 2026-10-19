import json

import pytest
from fastapi.testclient import TestClient

from better_marketplace import apify
from better_marketplace.account import AccountUsage, amount
from better_marketplace.app import create_app
from better_marketplace.settings import Settings


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "usage-test-token")
    state = {
        "used": 12.25,
        "limit": 100,
        "included": 29,
        "calls": [],
        "fail": False,
        "fail_plan": False,
    }

    def request(path, **kwargs):
        state["calls"].append(path)
        assert kwargs == {"timeout": 8, "attempts": 2}
        if state["fail"] or (path == "/users/me" and state["fail_plan"]):
            raise RuntimeError("must-not-leak-provider-body-or-credentials")
        if path == "/users/me/limits":
            return {
                "data": {
                    "current": {"monthlyUsageUsd": state["used"]},
                    "limits": {"maxMonthlyUsageUsd": state["limit"]},
                    "monthlyUsageCycle": {
                        "startAt": "2026-10-02T00:00:00Z",
                        "endAt": "2026-11-01T23:59:59.999Z",
                    },
                }
            }
        assert path == "/users/me"
        return {
            "data": {
                "plan": {"id": "STARTER", "monthlyUsageCreditsUsd": state["included"]},
                "email": "private-example@example.test",
                "proxy": {"password": "private-proxy-example"},
            }
        }

    monkeypatch.setattr(apify, "request", request)
    return state


def test_usage_limit_is_not_a_prepaid_credit_balance(provider):
    data = AccountUsage().get()
    assert data["status"] == "ready"
    assert data["used_usd"] == 12.25
    assert data["monthly_limit_usd"] == 100
    assert data["remaining_to_limit_usd"] == 87.75
    assert data["plan_included_credits_usd"] == 29
    assert data["credit_balance_usd"] is None
    assert data["cycle_start_at"].startswith("2026-10-02")
    encoded = json.dumps(data)
    assert "private-example" not in encoded
    assert "private-proxy-example" not in encoded
    assert "usage-test-token" not in encoded


def test_cache_and_manual_refresh_pick_up_changed_limits(provider):
    clock = [0]
    usage = AccountUsage(clock=lambda: clock[0])
    first = usage.get()
    provider.update(limit=250, included=99, used=15)
    assert usage.get() == first
    assert usage.get(refresh=True) == first  # coalesce rapid refreshes
    assert len(provider["calls"]) == 2
    clock[0] = 6
    refreshed = usage.get(refresh=True)
    assert refreshed["monthly_limit_usd"] == 250
    assert refreshed["plan_included_credits_usd"] == 99
    assert refreshed["remaining_to_limit_usd"] == 235
    provider["used"] = 16
    clock[0] = 67
    assert usage.get()["used_usd"] == 16


def test_failed_refresh_keeps_last_verified_values_and_marks_stale(provider):
    clock = [0]
    usage = AccountUsage(clock=lambda: clock[0])
    first = usage.get()
    provider["fail"] = True
    clock[0] = 61
    failed = usage.get()
    assert failed["status"] == "stale" and failed["stale"]
    assert failed["used_usd"] == first["used_usd"]
    assert failed["checked_at"] == first["checked_at"]
    assert "must-not-leak" not in json.dumps(failed)
    calls = len(provider["calls"])
    clock[0] = 70
    usage.get()
    assert len(provider["calls"]) == calls


def test_changed_token_cannot_receive_the_previous_accounts_cached_balance(provider, monkeypatch):
    usage = AccountUsage()
    usage.get()
    monkeypatch.setenv("APIFY_TOKEN", "another-test-token")
    provider["fail"] = True
    result = usage.get()
    assert result["status"] == "unavailable"
    assert result["used_usd"] is None
    assert not result["stale"]


def test_missing_fields_stay_unknown_and_optional_plan_permissions_do_not_block_usage(provider):
    provider.update(used=None, limit=None)
    result = AccountUsage().get()
    assert result["status"] == "partial"
    assert result["remaining_to_limit_usd"] is None
    assert result["monthly_limit_usd"] is None
    provider.update(used=0, limit=0, fail_plan=True)
    result = AccountUsage().get()
    assert result["used_usd"] == 0
    assert result["monthly_limit_usd"] == 0
    assert result["remaining_to_limit_usd"] == 0
    assert result["plan_included_credits_usd"] is None


def test_usage_above_limit_does_not_report_negative_headroom(provider):
    provider.update(used=101)
    assert AccountUsage().get()["remaining_to_limit_usd"] == 0


@pytest.mark.parametrize("value", [None, "29", -1, True, float("inf"), float("nan")])
def test_invalid_amount_is_not_a_balance(value):
    assert amount(value) is None


def test_no_token_does_not_call_apify(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No provider request should be made without a token")

    monkeypatch.setattr(apify, "request", forbidden)
    result = AccountUsage().get()
    assert result["status"] == "not_configured"
    assert result["used_usd"] is None


def test_usage_api_only_returns_sanitized_account_fields(tmp_path, provider):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/api/apify/usage?refresh=true")
        assert response.status_code == 200
        assert response.json()["monthly_limit_usd"] == 100
        assert response.headers["cache-control"] == "no-store"
        assert "private-example" not in response.text
        assert "private-proxy-example" not in response.text
        assert "usage-test-token" not in response.text
        assert client.get("/api/collections").json() == []
