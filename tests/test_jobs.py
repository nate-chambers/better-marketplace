from unittest.mock import patch

import pytest

from better_marketplace import apify
from better_marketplace.jobs import Collector
from better_marketplace.models import RunRequest
from better_marketplace.settings import Settings
from better_marketplace.store import Store


def test_duplicate_submission_does_not_start_twice(tmp_path, search, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-only-token")
    collector = Collector(Store(tmp_path), Settings(data_dir=tmp_path))
    with patch.object(collector.executor, "submit") as submit:
        request = RunRequest(search=search, request_id="idempotent-test")
        first = collector.start(request)
        second = collector.start(request)
        assert first["id"] == second["id"]
        assert submit.call_count == 1
    collector.close()


def test_unknown_creation_stops_waiting_searches(tmp_path, search, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-only-token")
    store = Store(tmp_path)
    collector = Collector(store, Settings(data_dir=tmp_path))
    with patch.object(collector.executor, "submit"):
        job = collector.start(RunRequest(search=search, request_id="ambiguous-test"))
    with patch("better_marketplace.jobs.apify.start", side_effect=RuntimeError("unknown")) as start:
        collector._run(job)
    assert start.call_count == 1
    saved = store.jobs()[0]
    assert saved["status"] == "needs_check"
    assert saved["tasks"][0]["status"] == "unknown"
    assert all(t["status"] == "cancelled" for t in saved["tasks"][1:])
    collector.close()


def test_restart_needs_explicit_resume(tmp_path, search, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-only-token")
    store = Store(tmp_path)
    collector = Collector(store, Settings(data_dir=tmp_path))
    with patch.object(collector.executor, "submit"):
        collector.start(RunRequest(search=search, request_id="restart-test"))
    collector.close()
    restarted = Collector(store, Settings(data_dir=tmp_path))
    assert store.jobs()[0]["status"] == "interrupted"
    restarted.close()


def test_provider_notices_and_pagination_keep_valid_records(car):
    run = {"data": {"status": "SUCCEEDED", "defaultDatasetId": "dataset"}}
    with patch(
        "better_marketplace.apify.request", side_effect=[run, [car, {"error": "Free plan limit"}]]
    ):
        listings, metadata = apify.fetch("run123")
    assert listings == [car]
    assert metadata["dataset_notices"] == [{"error": "Free plan limit"}]


def test_charge_cap_and_category_validation(tmp_path, search, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-only-token")
    collector = Collector(Store(tmp_path), Settings(data_dir=tmp_path))
    with pytest.raises(ValueError, match="server limit"):
        collector.plan(RunRequest(search=search, budget_usd=12, request_id="over-budget"))
    payload = collector.plan(RunRequest(search=search, request_id="category-test"))["tasks"][0][
        "input"
    ]
    payload["urls"] = ["https://www.facebook.com/marketplace/seattle/search/?query=BMW"]
    with patch("better_marketplace.apify.request") as request:
        with pytest.raises(ValueError, match="Cars & Trucks"):
            apify.start(payload, "bad-category", tmp_path / "runs", 0.25)
        request.assert_not_called()
    collector.close()


def test_completed_provider_run_imports_and_preserves_limit_notice(
    tmp_path, search, car, monkeypatch
):
    monkeypatch.setenv("APIFY_TOKEN", "test-only-token")
    store = Store(tmp_path)
    collector = Collector(store, Settings(data_dir=tmp_path))
    with patch.object(collector.executor, "submit"):
        job = collector.start(RunRequest(search=search, request_id="complete-test", max_jobs=1))
    job["tasks"][0].update(status="active", run_id="run123")
    run = {"status": "SUCCEEDED", "usageTotalUsd": 0.015, "finishedAt": "2026-10-01T00:00:00Z"}
    with (
        patch("better_marketplace.jobs.apify.request", return_value={"data": run}),
        patch(
            "better_marketplace.jobs.apify.fetch",
            return_value=([car], {"dataset_notices": [{"error": "Limit reached"}]}),
        ),
    ):
        collector._run(job)
    assert store.revision() == 1
    assert store.jobs()[0]["status"] == "complete"
    assert store.jobs()[0]["reported_cost_usd"] == 0.015
    assert store.jobs()[0]["tasks"][0]["notices"]
    collector.close()
