from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from better_marketplace.app import create_app
from better_marketplace.models import Query
from better_marketplace.service import Listings
from better_marketplace.settings import Settings
from better_marketplace.store import Store

HEADERS = {"X-Marketplace-Client": "1"}


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app, headers=HEADERS) as client:
        yield client


def test_end_to_end_import_filter_bookmark_export_and_saved_search(client, car, search):
    imported = client.post("/api/listings/import", json={"records": [car]})
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    assert client.post("/api/listings/import", json={"records": [car]}).json()["imported"] == 0
    query = {"search": search.model_dump(), "status": "match"}
    data = client.post("/api/listings/query", json=query).json()
    assert data["total"] == 1
    assert data["items"][0]["price"] == 23000
    assert client.put("/api/listings/123/bookmark", json={"saved": True}).json()["saved"]
    assert (
        client.post("/api/listings/query", json={**query, "status": "saved"}).json()["total"] == 1
    )
    saved = client.post("/api/searches", json=search.model_dump()).json()
    assert client.get("/api/bootstrap").json()["searches"][0]["id"] == saved["id"]
    assert client.delete("/api/searches/" + saved["id"]).status_code == 200
    csv = client.post("/api/listings/export/csv", json=query)
    assert "2018 BMW 440i" in csv.text
    assert "attachment;" in csv.headers["content-disposition"]
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_origin_and_secret_boundary(client, monkeypatch, search):
    monkeypatch.setenv("APIFY_TOKEN", "test-only-token")
    boot = client.get("/api/bootstrap")
    assert boot.json()["token_configured"] is True
    assert "test-only-token" not in boot.text
    assert (
        client.post(
            "/api/listings/query",
            headers={"Origin": "https://untrusted.example"},
            json={"search": search.model_dump()},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/listings/query",
            headers={"X-Marketplace-Client": ""},
            json={"search": search.model_dump()},
        ).status_code
        == 403
    )
    assert client.get("/.env").status_code == 404
    assert client.get("/data/marketplace.sqlite3").status_code == 404


def test_preview_is_free_and_missing_token_blocks_collection(client, search):
    body = {
        "search": search.model_dump(),
        "request_id": "test-preview-001",
        "max_jobs": 44,
        "budget_usd": 1,
    }
    plan = client.post("/api/collections/plan", json=body).json()
    assert plan["search_count"] == 4
    assert plan["maximum_charge_usd"] == 1
    assert "/cars/" in plan["tasks"][0]["input"]["urls"][0]
    assert plan["tasks"][0]["input"]["strictFiltering"]
    assert client.post("/api/collections", json=body).status_code == 400
    assert client.get("/api/collections").json() == []


def test_invalid_bounds_and_import_are_atomic(client, car, search):
    payload = search.model_dump()
    payload["min_year"] = 2030
    payload["max_year"] = 2017
    assert client.post("/api/listings/query", json={"search": payload}).status_code == 422
    assert (
        client.post("/api/listings/import", json={"records": [car, {"title": "No ID"}]}).status_code
        == 400
    )
    assert client.get("/api/bootstrap").json()["observation_count"] == 0


def test_old_import_cannot_replace_newer_evidence_and_staleness(tmp_path, car, search):
    store = Store(tmp_path)
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    recent = datetime.now(UTC).isoformat()
    store.import_records([car], "new", recent)
    older = {**car, "listing_price": {"amount": 19000, "currency": "USD"}}
    store.import_records([older], "old", old)
    data = Listings(store).query(Query(search=search))
    assert data["items"][0]["price"] == 23000
    assert data["items"][0]["status"] == "match"
    assert len(data["items"][0]["price_history"]) == 2
    store.import_records([{**car, "id": "stale"}], "older", old)
    data = Listings(store).query(Query(search=search))
    assert next(i for i in data["items"] if i["id"] == "stale")["status"] == "needs_review"


def test_csv_formula_escape_and_text_preservation(client, car, search):
    car["title"] = '=HYPERLINK("bad") 2018 BMW 440i <script>alert(1)</script>'
    client.post("/api/listings/import", json={"records": [car]})
    response = client.post("/api/listings/export/csv", json={"search": search.model_dump()})
    assert "'=HYPERLINK" in response.text
    records = client.post("/api/listings/export/json", json={"search": search.model_dump()}).json()
    assert records[0]["title"] == car["title"]


def test_reason_facets_sorting_and_numeric_unknowns(client, car, search):
    client.post(
        "/api/listings/import",
        json={
            "records": [
                car,
                {**car, "id": "high", "description": "440i 90k miles."},
                {**car, "id": "unknown", "listing_price": {"currency": "USD"}},
            ]
        },
    )
    data = client.post(
        "/api/listings/query", json={"search": search.model_dump(), "sort": "price_asc"}
    ).json()
    assert data["items"][-1]["price"] is None
    assert data["reasons"]["Mileage too high"] == 1
    filtered = client.post(
        "/api/listings/query", json={"search": search.model_dump(), "reason": "Mileage too high"}
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == "high"


def test_empty_results(client, search):
    assert (
        client.post("/api/listings/query", json={"search": search.model_dump()}).json()["items"]
        == []
    )


def test_no_environment_parent_search(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / ".env").write_text("APIFY_TOKEN=parent-test-only-token\n")
    monkeypatch.chdir(child)
    Settings.from_env()
    import os

    assert "APIFY_TOKEN" not in os.environ


def test_api_docs_use_nonce_and_allow_header(client):
    page = client.get("/docs")
    assert page.status_code == 200
    assert '<script nonce="' in page.text
    assert "request.headers['X-Marketplace-Client'] = '1'" in page.text
    assert "cdn.jsdelivr.net" in page.headers["content-security-policy"]
    assert "nonce-" in page.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in client.get("/").headers["content-security-policy"]


def test_saved_legacy_search_uses_price_range_without_cylinder_filter(client, car, search):
    legacy = search.model_dump()
    legacy["max_price_exclusive"] = legacy.pop("max_price")
    legacy["exclude_four_cylinder"] = True
    # Existing saved JSON must remain readable after removing the old option.
    client.app.state.store.save_search("legacy", legacy)
    saved = client.get("/api/bootstrap").json()["searches"][0]["search"]
    assert saved["max_price"] == 25000
    assert "exclude_four_cylinder" not in saved
    assert "max_price_exclusive" not in saved
    car.update(description="440i 72k miles. 4-cylinder engine.")
    car["listing_price"]["amount"] = 25000
    client.post("/api/listings/import", json={"records": [car]})
    data = client.post("/api/listings/query", json={"search": legacy, "status": "match"})
    assert data.status_code == 200
    assert data.json()["total"] == 1


def test_equal_price_bounds_are_valid_and_reversed_bounds_are_rejected(client, search):
    criteria = search.model_dump()
    criteria.update(min_price=25000, max_price=25000)
    assert client.post("/api/listings/query", json={"search": criteria}).status_code == 200
    criteria["min_price"] = 25001
    assert client.post("/api/listings/query", json={"search": criteria}).status_code == 422


def test_typed_honda_accord_filters_and_collection_plan(client, car, search):
    criteria = search.model_dump()
    criteria.update(makes=["honda"], allowed_models=["accord"])
    client.post(
        "/api/listings/import",
        json={
            "records": [
                {
                    **car,
                    "id": "accord",
                    "title": "2018 Honda Accord",
                    "description": "Accord sedan. 72k miles. Automatic.",
                },
                {
                    **car,
                    "id": "civic",
                    "title": "2018 Honda Civic",
                    "description": "Civic sedan. 72k miles. Looking to trade for an Accord.",
                },
                car,
            ]
        },
    )
    data = client.post("/api/listings/query", json={"search": criteria, "status": "match"})
    assert data.status_code == 200
    assert [item["id"] for item in data.json()["items"]] == ["accord"]
    plan = client.post(
        "/api/collections/plan",
        json={
            "search": criteria,
            "request_id": "honda-accord-plan",
            "budget_usd": 0.25,
            "max_jobs": 1,
        },
    )
    assert plan.status_code == 200
    url = plan.json()["tasks"][0]["input"]["urls"][0]
    assert parse_qs(urlparse(url).query)["query"] == ["honda accord"]
    assert client.get("/api/collections").json() == []
