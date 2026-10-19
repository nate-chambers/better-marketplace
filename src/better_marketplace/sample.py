"""Synthetic listings for an offline product tour. Photos are illustrative."""


def records():
    vehicles = [
        (
            "Honda",
            "Accord",
            2021,
            22500,
            42000,
            "Austin",
            "honda",
            "Automatic sedan. Service records available.",
        ),
        (
            "Toyota",
            "Camry",
            2020,
            20900,
            55000,
            "Dallas",
            "toyota",
            "Automatic sedan. Full asking price shown.",
        ),
        (
            "Ford",
            "Mustang",
            2019,
            28400,
            63000,
            "Houston",
            "ford",
            "Manual coupe. Full asking price shown.",
        ),
        (
            "Tesla",
            "Model 3",
            2022,
            27900,
            41000,
            "Austin",
            "tesla",
            "Electric sedan. Automatic. Full asking price shown.",
        ),
        (
            "Honda",
            "Civic",
            2016,
            13900,
            112000,
            "Dallas",
            "honda",
            "Automatic sedan. Full asking price shown.",
        ),
        (
            "Toyota",
            "Corolla",
            2021,
            18900,
            39000,
            "Austin",
            "toyota",
            "Automatic sedan. Full asking price shown.",
        ),
        (
            "Ford",
            "Mustang",
            2020,
            2500,
            58000,
            "Houston",
            "ford",
            "$2,500 down. Contact us for the full price.",
        ),
        (
            "Tesla",
            "Model Y",
            2021,
            29900,
            None,
            "Dallas",
            "tesla",
            "Contact seller for the odometer reading.",
        ),
    ]
    output = []
    for index, (make, model, year, price, miles, city, photo, description) in enumerate(vehicles):
        raw = {
            "id": f"sample-{index + 1:03d}",
            "title": f"{year} {make} {model}",
            "make": make,
            "model": model,
            "description": f"{year} {make} {model}. {description}\n\nSAMPLE LISTING. Illustrative photo; not a real offer.",
            "listing_price": {"amount": price, "currency": "USD"},
            "location": {"city": city, "state": "TX"},
            "image_url": f"/static/demo/{photo}.jpg",
            "marketplace_listing_category_id": "807311116002614",
        }
        if miles is not None:
            raw["vehicle_odometer_data"] = {"value": miles, "unit": "MILES"}
        output.append(raw)
    return output


def seed(store):
    if not store.revision():
        store.import_records(records(), "sample")
