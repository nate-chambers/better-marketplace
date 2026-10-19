import pytest

from better_marketplace.models import Center, Search


@pytest.fixture(autouse=True)
def no_real_provider_token(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)


@pytest.fixture
def search():
    # Explicit regression criteria, separate from the blank first-run search.
    return Search(
        makes=["BMW"],
        allowed_models=["240i", "m240i", "340i", "440i"],
        min_year=2017,
        min_price=0,
        max_price=25000,
        max_miles_exclusive=80000,
        region_mode="radius",
        search_centers=[
            Center(
                name="Seattle",
                marketplace_location="seattle",
                latitude=47.6,
                longitude=-122.3,
                radius_km=280,
            )
        ],
    )


@pytest.fixture
def car():
    return {
        "id": "123",
        "title": "2018 BMW 440i",
        "description": "440i coupe. 72k miles. Automatic.",
        "listing_price": {"amount": 23000, "currency": "USD"},
        "location": {"city": "Seattle", "state": "WA", "latitude": 47.6, "longitude": -122.3},
    }
