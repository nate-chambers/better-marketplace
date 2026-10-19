import pytest

from better_marketplace.filtering import extract


@pytest.mark.parametrize(
    "changes,status",
    [
        ({}, "match"),
        ({"title": "2016 BMW 440i"}, "rejected"),
        ({"description": "440i 80k miles."}, "rejected"),
        ({"description": "440i 79,999 miles."}, "match"),
        ({"listing_price": {"amount": 25000, "currency": "USD"}}, "match"),
        ({"listing_price": {"amount": 25001, "currency": "USD"}}, "rejected"),
        ({"description": "440i coupe."}, "needs_review"),
        ({"description": ""}, "needs_review"),
        ({"description": "440i 0 miles."}, "needs_review"),
        ({"description": "440i 72k miles. True mileage unknown."}, "needs_review"),
        (
            {
                "title": "2017 BMW 328i",
                "description": "Looking to trade for a 340i or 440i. 65k miles.",
            },
            "rejected",
        ),
        ({"title": "2018 BMW 340i", "description": "Actually a 330i. 65k miles."}, "rejected"),
        ({"title": "2018 BMW M340i", "description": "M340i 65k miles."}, "rejected"),
        (
            {"title": "BMW floor mats", "description": "Fits 240i 340i 440i. Parts only."},
            "rejected",
        ),
        ({"description": "440i, 150k on chassis, replacement engine has 60k miles."}, "rejected"),
        ({"description": "440i replacement engine has 60k miles."}, "needs_review"),
        ({"description": "440i 72k miles. Price $2,500 down."}, "needs_review"),
        ({"description": "440i 72k miles. $2,500 down, cash price $29,995."}, "rejected"),
        ({"description": "440i 72k miles. Asking $22,000."}, "needs_review"),
        ({"listing_price": {"amount": 23000}}, "needs_review"),
        ({"listing_price": {"amount": 23000, "currency": "CAD"}}, "needs_review"),
        ({"listing_price": {"amount": 1, "currency": "USD"}}, "needs_review"),
        ({"description": "440i 100,000 km."}, "match"),
        ({"description": "440i 130,000 kilometers."}, "rejected"),
        ({"vehicle_odometer_data": {"value": 85000, "unit": "MILES"}}, "needs_review"),
        ({"location": {}}, "needs_review"),
        (
            {
                "location": {
                    "city": "Beaverton",
                    "state": "OR",
                    "latitude": 45.49,
                    "longitude": -122.8,
                }
            },
            "match",
        ),
        (
            {"location": {"city": "Salem", "state": "OR", "latitude": 44.94, "longitude": -123.04}},
            "rejected",
        ),
        (
            {
                "location": {
                    "city": "Boise",
                    "state": "ID",
                    "latitude": 43.615,
                    "longitude": -116.202,
                }
            },
            "rejected",
        ),
        ({"is_sold": True}, "rejected"),
        ({"is_pending": True}, "rejected"),
        ({"is_live": False}, "rejected"),
        ({"description": "440i, 72k miles, 4-cylinder engine."}, "match"),
        ({"title": "2021 BMW M4", "description": "440i 72k miles."}, "rejected"),
    ],
)
def test_original_vehicle_regressions(car, search, changes, status):
    assert extract({**car, **changes}, search.rules())["status"] == status


@pytest.mark.parametrize(
    "make,model",
    [
        ("Toyota", "Camry"),
        ("Honda", "Civic"),
        ("Honda", "Accord"),
        ("Ford", "F-150"),
        ("Tesla", "Model 3"),
        ("BMW", "M2"),
        ("BMW", "X3"),
        ("Acura", "TLX"),
        ("Subaru", "WRX"),
        ("Mercedes-Benz", "C 300"),
    ],
)
def test_make_model_customization(car, search, make, model):
    search.makes = [make]
    search.allowed_models = [model]
    result = extract(
        {
            **car,
            "title": f"2020 {make} {model}",
            "description": f"{model}. 72k miles. Automatic.",
            "make": make,
            "model": model,
        },
        search.rules(),
    )
    assert result["status"] == "match", result
    assert result["make"] == make


@pytest.mark.parametrize(
    "title,description",
    [
        ("2020 Toyota Corolla", "Camry. 55k miles."),
        ("2020 Honda Civic", "Civic 55k miles. Looking to trade for a Camry."),
        ("2020 Toyota Corolla", "55k miles. Looking for a Camry."),
    ],
)
def test_requested_keyword_cannot_rescue_wrong_vehicle(car, search, title, description):
    search.makes = ["Toyota"]
    search.allowed_models = ["Camry"]
    assert (
        extract({**car, "title": title, "description": description}, search.rules())["status"]
        == "rejected"
    )


def test_financing_boilerplate_and_approximate_mileage(car, search):
    result = extract(
        {
            **car,
            "title": "2018 BMW M240i",
            "description": "M240i with just over 68,000 miles. Documentary fee of up to $200. Price excludes Finance Charges. Financing available.",
            "vehicle_odometer_data": {"value": 68150, "unit": "MILES"},
            "listing_price": {"amount": 21900, "currency": "USD"},
        },
        search.rules(),
    )
    assert (result["status"], result["price"], result["miles"]) == ("match", 21900, 68150)


@pytest.mark.parametrize(
    "text",
    [
        "$2,500 down.",
        "$499 per month.",
        "Down payment: $2500.",
        "Price requires financing.",
        "Financing only.",
        "Must finance.",
    ],
)
def test_payment_prices_remain_uncertain(car, search, text):
    assert (
        extract({**car, "description": "440i 72k miles. " + text}, search.rules())["status"]
        == "needs_review"
    )


def test_cash_price_overrides_down_payment(car, search):
    result = extract(
        {
            **car,
            "description": "440i 72k miles. $2,500 down, cash price $19,995.",
            "listing_price": {"amount": 2500, "currency": "USD"},
        },
        search.rules(),
    )
    assert result["status"] == "match"
    assert result["price"] == 19995


def test_other_bounds_and_transmission(car, search):
    search.max_year = 2017
    assert extract(car, search.rules())["status"] == "rejected"
    search.max_year = 2025
    search.min_price = 24000
    assert extract(car, search.rules())["status"] == "rejected"
    search.min_price = 0
    search.transmission = "manual"
    assert extract(car, search.rules())["status"] == "rejected"
    search.transmission = "automatic"
    assert extract(car, search.rules())["status"] == "match"


def test_custom_radius_requires_actual_coordinates(car, search):
    search.region_mode = "radius"
    search.search_centers = search.search_centers[:1]
    search.search_centers[0].radius_km = 10
    assert extract(car, search.rules())["status"] == "match"
    car["location"] = {"city": "Tacoma", "state": "WA", "latitude": 47.25, "longitude": -122.44}
    assert extract(car, search.rules())["status"] == "rejected"
    car["location"] = {"city": "Seattle", "state": "WA"}
    assert extract(car, search.rules())["status"] == "needs_review"


def test_approximation_does_not_cross_mileage_ceiling(car, search):
    car.update(
        description="440i about 80k miles", vehicle_odometer_data={"value": 80001, "unit": "MILES"}
    )
    result = extract(car, search.rules())
    assert result["status"] == "rejected"
    assert result["miles"] == 80001


def test_any_model_and_punctuation_alias(car, search):
    search.makes = ["Ford"]
    search.allowed_models = ["F150"]
    car.update(title="2018 Ford F-150", description="F150. 72k miles.")
    assert extract(car, search.rules())["status"] == "match"
    search.allowed_models = []
    assert extract(car, search.rules())["status"] == "match"


@pytest.mark.parametrize(
    "price,status", [(19999, "rejected"), (20000, "match"), (25000, "match"), (25001, "rejected")]
)
def test_price_range_includes_both_bounds(car, search, price, status):
    search.min_price = 20000
    search.max_price = 25000
    car["listing_price"]["amount"] = price
    result = extract(car, search.rules())
    assert result["status"] == status


def test_cylinder_count_does_not_filter_a_requested_model(car, search):
    search.makes = ["Toyota"]
    search.allowed_models = ["Camry"]
    car.update(
        title="2018 Toyota Camry",
        description="Camry, 72k miles. Four-cylinder engine.",
        vehicle_cylinders=4,
    )
    assert extract(car, search.rules())["status"] == "match"
