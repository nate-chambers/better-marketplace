from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from better_marketplace.app import create_app
from better_marketplace.filtering import extract
from better_marketplace.jobs import Collector
from better_marketplace.models import Center, RunRequest, Search, default_search
from better_marketplace.sample import records, seed
from better_marketplace.settings import Settings
from better_marketplace.store import Store


def test_first_run_has_no_personal_search_defaults(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        boot = client.get("/api/bootstrap").json()
    criteria = boot["default_search"]
    assert criteria["makes"] == criteria["allowed_models"] == criteria["search_centers"] == []
    assert criteria["search_terms"] == []
    assert criteria["region_mode"] == "any"
    assert all(
        criteria[key] is None
        for key in ["min_year", "max_year", "min_price", "max_price", "max_miles_exclusive"]
    )
    assert boot["searches"] == []
    assert boot["observation_count"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"min_year": 2010},
        {"max_year": 2025},
        {"min_price": 10000},
        {"max_price": 30000},
        {"max_miles_exclusive": 100000},
    ],
)
def test_unset_and_single_sided_bounds_accept_explicit_vehicle(car, changes):
    criteria = Search(**changes)
    assert extract(car, criteria.rules())["status"] == "match"


def test_unrestricted_search_does_not_exclude_other_makes_or_locations():
    for record in records()[:6]:
        result = extract(record, default_search().rules())
        assert result["status"] == "match", result
        assert result["location"].endswith(", TX")


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"makes": ["Honda"]},
        {
            "search_centers": [
                Center(
                    name="Austin",
                    marketplace_location="austin",
                    latitude=30.2672,
                    longitude=-97.7431,
                )
            ]
        },
    ],
)
def test_incomplete_collection_plan_cannot_submit_paid_work(tmp_path, changes):
    collector = Collector(Store(tmp_path), Settings(data_dir=tmp_path))
    try:
        with patch("better_marketplace.jobs.apify.start") as submit:
            with pytest.raises(ValueError, match="search city"):
                collector.plan(RunRequest(search=Search(**changes), request_id="blank-search-plan"))
            submit.assert_not_called()
        assert collector.store.jobs() == []
    finally:
        collector.close()


def test_custom_terms_and_location_work_without_selected_make(tmp_path):
    collector = Collector(Store(tmp_path), Settings(data_dir=tmp_path))
    try:
        criteria = Search(
            search_terms=["electric hatchback"],
            search_centers=[
                Center(
                    name="Austin",
                    marketplace_location="austin",
                    latitude=30.2672,
                    longitude=-97.7431,
                )
            ],
        )
        plan = collector.plan(RunRequest(search=criteria, request_id="custom-term-plan"))
        assert plan["search_count"] == 1
    finally:
        collector.close()


def test_samples_do_not_create_saved_searches(tmp_path):
    store = Store(tmp_path)
    seed(store)
    seed(store)
    assert store.revision() == len(records())
    assert store.searches() == []
