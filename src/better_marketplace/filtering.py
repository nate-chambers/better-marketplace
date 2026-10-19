"""Conservative extraction. Unknown or conflicting facts never become matches."""

import math
import re
from datetime import datetime

from .identity import canonical_make, makes_in, model_mentions

MODEL = re.compile(r"\b(m?\s?[1-8]\d{2}x?[ide])(?:\b|(?=xdrive\b))", re.I)
NUMBER = r"\d[\d,]*(?:\.\d+)?\s*[kK]?"
MILEAGE = re.compile(rf"\b({NUMBER})\s*(miles?|mi\b|kms?\b|kilomet(?:er|re)s?)", re.I)
CHASSIS = re.compile(rf"\b({NUMBER})\s*(?:on\s+(?:the\s+)?)?(?:chassis|body|odometer)\b", re.I)
ODOMETER = re.compile(
    rf"\b(?:odometer|mileage)\s*[:=]?\s*({NUMBER})\s*(miles?|mi\b|kms?\b|kilomet(?:er|re)s?)?", re.I
)
MONEY = re.compile(rf"(?:\$|USD\s*)({NUMBER})", re.I)
PRICE_LABEL = re.compile(
    r"(?:cash(?:\s+price)?|asking(?:\s+price)?|full\s+price|price)\s*(?:is\s*)?[:=]?\s*$", re.I
)
NON_CASH = re.compile(
    rf"\b(down\s*payment|deposit|monthly|per\s+month|payments?|lease)\b|/\s*mo\b|{NUMBER}\s+down\b"
    r"|\b(?:financ(?:ing|ed)\s+only|must\s+finance|price\s+(?:with|requires)\s+financing)\b",
    re.I,
)
PARTS = re.compile(
    r"\b(parting\s+out|parts|coilovers?|wheels?|rims?|bumpers?|exhaust|head\s*lights?|tail\s*lights?|turbo(?:charger)?|engine|motor|transmission|rotors?|injectors?|inlet pipe|owner'?s? manual|lsd|mirror caps?|module|springs?|spoiler|muffler|suspension|car cover|floor mats?|trunk lip|differential|seats?|dme unlock|ev chargers?|ac system|coding package|downpipe|air filter|trim set)\b",
    re.I,
)


def number(value):
    text = str(value).replace(",", "").strip().lower()
    try:
        n = float(text.rstrip("k").strip()) * (1000 if text.endswith("k") else 1)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError):
        return None


def as_text(value):
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def field(raw, *keys):
    for key in keys:
        if raw.get(key) is not None:
            return raw[key]
    return None


def consistent_approximation(clause, match, exact, normalized, unit):
    """An explicitly rounded description can agree with a precise odometer."""
    modifier = re.search(
        r"\b(just over|just under|about|around|approximately|approx\.?)\s*$",
        clause[: match.start()],
        re.I,
    )
    if modifier is None or exact is None:
        return False
    value_text = match[1].replace(",", "").strip().lower()
    if value_text.endswith("k"):
        decimals = len(value_text[:-1].split(".")[1]) if "." in value_text else 0
        scale = 1000 / (10**decimals)
    else:
        zeros = len(value_text) - len(value_text.rstrip("0"))
        scale = min(1000, 10**zeros)
    if unit.startswith(("km", "kilo")):
        scale /= 1.609344
    qualifier = modifier[1].lower()
    if qualifier == "just over":
        return normalized < exact < normalized + scale
    if qualifier == "just under":
        return normalized - scale < exact < normalized
    return abs(exact - normalized) <= scale / 2


def extract(raw, config):
    evidence, conflicts, unknowns, rejected = [], [], [], []

    def add(name, value, source, quote):
        evidence.append(
            {
                "field": name,
                "value": value,
                "source": source,
                "quote": str(quote),
                "kind": "observation",
            }
        )
        return value

    def resolve(name, values, tolerance=0):
        values = [v for v in values if v is not None]
        if not values:
            unknowns.append(f"Unknown {name}")
            return None
        if isinstance(values[0], (int, float)):
            disagree = max(values) - min(values) > tolerance
        else:
            disagree = len(set(values)) > 1
        if disagree:
            conflicts.append(f"Conflicting {name}: {', '.join(map(str, sorted(set(values))))}")
            return None
        return max(values)

    title = as_text(field(raw, "marketplace_listing_title", "title", "custom_title"))
    description = as_text(field(raw, "redacted_description", "description"))
    if not description.strip():
        unknowns.append("Full description missing; detail collection must be verified")
    texts = [("title", title), ("description", description)]
    model_field = as_text(field(raw, "vehicle_model_display_name", "model"))
    trim = as_text(raw.get("vehicle_trim_display_name"))
    requested_makes = {canonical_make(m) for m in config.get("makes", [])}
    make_field = canonical_make(as_text(field(raw, "vehicle_make_display_name", "make")))
    title_makes = makes_in(title)
    observed_makes = title_makes | ({make_field} if make_field else set())
    if not observed_makes:
        observed_makes = makes_in(description)
    make = resolve(
        "make",
        [add("make", m, "vehicle_make / title / description", m) for m in sorted(observed_makes)],
    )
    if requested_makes and observed_makes - requested_makes:
        rejected.append("Non-target make: " + ", ".join(sorted(observed_makes - requested_makes)))
    identity_makes = observed_makes or requested_makes
    models = []
    for source, text in texts + [("vehicle_model", model_field + " " + trim)]:
        mentions = model_mentions(text, identity_makes, config["allowed_models"])
        if "BMW" in identity_makes:
            mentions += [
                (re.sub(r"\s", "", m[1]).lower(), m[0], m.start(), m.end())
                for m in MODEL.finditer(text)
            ]
        # Accept a structured model for custom makes even when the catalog has no entry.
        if (
            source == "vehicle_model"
            and model_field
            and not mentions
            and "BMW" not in identity_makes
        ):
            mentions = [(model_field.lower(), model_field, 0, len(model_field))]
        for name, quote, start, _end in mentions:
            # Mentions in trade requests, compatibility lists and comparisons aren't identity.
            prefix = re.split(r"[.;\n]", text[:start])[-1]
            if source == "description" and re.search(
                r"\b(trade|trading|looking for|looking to|want|fits?|compatible|compared|unlike|not a)\b",
                prefix,
                re.I,
            ):
                continue

            # Punctuation variants such as F150 / F-150 do not imply different models.
            def model_key(value):
                return re.sub(r"[^a-z0-9]", "", value.lower())

            name = next(
                (m for m in config["allowed_models"] if model_key(m) == model_key(name)), name
            )
            name = next((m for m in models if model_key(m) == model_key(name)), name)
            models.append(add("model", name, source, quote))
    model = resolve("model", models) if models or config["allowed_models"] else None
    excluded_identity = (
        set(models) - set(config["allowed_models"]) if config["allowed_models"] else set()
    )
    if excluded_identity:
        rejected.append("Non-target vehicle identity: " + ", ".join(sorted(excluded_identity)))
    if model and config["allowed_models"] and model not in config["allowed_models"]:
        rejected.append(f"Model {model} is not a target")
    category = raw.get("marketplace_listing_category_id")
    if category:
        add("category_id", str(category), "marketplace_listing_category_id", category)
        if str(category) != "807311116002614":
            rejected.append("Listing is outside Cars & Trucks")
    if (
        config["allowed_models"]
        and "BMW" in requested_makes
        and re.search(r"\bBMW\s+(?:M[2-8]|[XZ][1-8]|[15678R]\s*Series)\b", title, re.I)
        and not model
    ):
        rejected.append("Vehicle is a different BMW model/series")
    if re.search(r"\b(parting out|parts only)\b", title + " " + description, re.I):
        rejected.append("Parts / part-out listing")
    elif PARTS.search(title) and (
        not re.search(r"\b20\d{2}\b", title)
        or re.search(r"\b(fits?|only|for|parts|stock)\b|20\d{2}\s*[-–]\s*20\d{2}", title, re.I)
    ):
        rejected.append("Component listing, not a complete car")
    elif PARTS.search(title):
        # Modified cars can mention wheels/engine in titles; don't automatically reject them.
        unknowns.append("Title mentions a component; verify this is a complete car")
    if re.search(r"\b(wanted|wtb|looking (?:for|to buy))\b", title, re.I):
        rejected.append("Wanted listing, not a vehicle for sale")
    if re.search(
        r"\b(not (?:an? )?(?:bmw\s+)?m?\s?[234]\d{2}i|rebadged|replica|clone)\b",
        title + " " + description,
        re.I,
    ):
        conflicts.append("Vehicle identity is negated or describes a rebadge/replica")

    years = []
    for source, text in [
        ("title", title),
        ("vehicle_year", as_text(field(raw, "vehicle_year", "year"))),
    ]:
        for match in re.finditer(r"\b(19\d{2}|20\d{2})\b", text):
            years.append(add("year", int(match[0]), source, match[0]))
    # Description years only count when attached to a vehicle identity.
    for match in re.finditer(
        r"\b((?:19|20)\d{2})\s+(?:" + "|".join(re.escape(m) for m in identity_makes) + r")\b",
        description,
        re.I,
    ):
        prefix = re.split(r"[.;\n]", description[: match.start()])[-1]
        if not re.search(r"\b(trade|fits?|looking|want)\b", prefix, re.I):
            years.append(add("year", int(match[1]), "description", match[0]))
    year = resolve("year", years)
    if year is not None and config.get("min_year") is not None and year < config["min_year"]:
        rejected.append(f"Year {year} is before {config['min_year']}")
    if year is not None and config.get("max_year") and year > config["max_year"]:
        rejected.append(f"Year {year} is after {config['max_year']}")
    if year is not None and year > datetime.now().year + 1:
        conflicts.append("Implausible future model year")

    miles = []
    exact_odometer = None
    odo = raw.get("vehicle_odometer_data") or {}
    if isinstance(odo, dict) and odo.get("value") is not None:
        value = number(odo["value"])
        unit = str(odo.get("unit", "")).lower()
        if value is not None and unit in {"miles", "mile", "mi", "km", "kilometers", "kilometres"}:
            miles.append(
                add(
                    "miles",
                    value / 1.609344 if unit in {"km", "kilometers", "kilometres"} else value,
                    "vehicle_odometer_data",
                    odo,
                )
            )
            exact_odometer = miles[-1]
        else:
            unknowns.append("Invalid odometer value or missing unit")
    if raw.get("mileage") is not None:
        texts.append(("mileage", as_text(raw["mileage"])))
    for subtitle in raw.get("custom_sub_titles_with_rendering_flags") or []:
        if isinstance(subtitle, dict):
            texts.append(("subtitle", as_text(subtitle.get("subtitle"))))
    for source, text in texts:
        # Commas separating thousands and decimal points must not break clauses.
        clauses = re.split(r"[;\n]|(?<!\d)[.!?]|,(?!\d)|\bbut\b", text, flags=re.I)
        for clause in clauses:
            candidates = (
                list(MILEAGE.finditer(clause))
                + list(CHASSIS.finditer(clause))
                + list(ODOMETER.finditer(clause))
            )
            for match in candidates:
                if re.search(
                    r"\b(engine|motor|transmission|turbo|oil|tires?|brakes?|warranty|range)\b",
                    clause,
                    re.I,
                ):
                    if not CHASSIS.fullmatch(match[0]) and not ODOMETER.fullmatch(match[0]):
                        # A bare '60k miles' near engine work cannot establish chassis mileage.
                        unknowns.append(
                            "Component/service mileage mentioned; verify chassis odometer"
                        )
                        continue
                value = number(match[1])
                unit = match[2].lower() if match.re != CHASSIS and match[2] else "miles"
                if value is not None:
                    normalized = value / 1.609344 if unit.startswith(("km", "kilo")) else value
                    if consistent_approximation(clause, match, exact_odometer, normalized, unit):
                        add("rounded_mileage_summary", normalized, source, clause.strip())
                        continue
                    if (
                        source == "subtitle"
                        and "k" in match[1].lower()
                        and exact_odometer is not None
                    ):
                        scale = (100 if "." in match[1] else 1000) / (
                            1.609344 if unit.startswith(("km", "kilo")) else 1
                        )
                        if math.floor(exact_odometer / scale) == math.floor(normalized / scale):
                            add("rounded_mileage_summary", normalized, source, match[0])
                            continue
                    miles.append(
                        add(
                            "miles",
                            normalized,
                            source,
                            match[0],
                        )
                    )
    mileage = resolve("mileage", miles, tolerance=1)
    if (
        mileage is None
        and miles
        and config.get("max_miles_exclusive") is not None
        and min(miles) >= config["max_miles_exclusive"]
    ):
        rejected.append("All reported odometer values exceed the mileage limit")
    if re.search(
        r"\b(tmu|true mileage unknown|not actual miles|odometer (?:broken|rollback|replaced)|rolled back)\b",
        title + " " + description,
        re.I,
    ):
        unknowns.append("Odometer accuracy is explicitly uncertain")
    if mileage is not None:
        if mileage <= 0:
            unknowns.append("Zero mileage needs verification")
        elif (
            config.get("max_miles_exclusive") is not None
            and mileage >= config["max_miles_exclusive"]
        ):
            rejected.append(
                f"Mileage {mileage:,.0f} is not below {config['max_miles_exclusive']:,}"
            )

    price_data = raw.get("listing_price") or {}
    currency = price_data.get("currency") if isinstance(price_data, dict) else None
    currency = currency or raw.get("currency")
    if currency != "USD":
        unknowns.append("Currency must be explicitly USD")
    displayed = price_data.get("amount") if isinstance(price_data, dict) else None
    if displayed is None:
        displayed = raw.get("price")
    displayed_num = number(str(displayed).replace("$", "")) if displayed is not None else None
    if displayed_num is not None:
        add("displayed_price", displayed_num, "listing_price / price", displayed)
    cash, other_cash = [], []
    for source, text in [("title", title), ("description", description)]:
        for match in MONEY.finditer(text):
            prefix = text[max(0, match.start() - 35) : match.start()]
            suffix = text[match.end() : match.end() + 22]
            value = number(match[1])
            if re.match(r"\s*(?:down|deposit|/\s*mo|per month|a month|monthly)\b", suffix, re.I):
                continue
            elif re.match(
                r"\s*(?:(?:doc(?:umentation)?|dealer|document|administrative|registration)\s+)?fee\b",
                suffix,
                re.I,
            ):
                continue
            elif PRICE_LABEL.search(prefix):
                cash.append(add("price", value, source, match[0]))
            elif re.search(
                r"\b(spent|invested|repairs?|worth|paid|cost|new|wheels?|mods?|fees?)\b",
                prefix,
                re.I,
            ):
                continue
            else:
                other_cash.append(add("price", value, source, match[0]))
    # Financing availability and ordinary finance-charge disclaimers do not make
    # the Marketplace asking-price field a payment amount.
    financing = bool(NON_CASH.search(title + " " + description)) or bool(
        raw.get("payment_time_period")
    )
    if financing:
        if not cash:
            unknowns.append(
                "Down payment / financing mentioned without an explicit full cash price"
            )
            if (
                displayed_num is not None
                and config.get("max_price") is not None
                and displayed_num > config["max_price"]
            ):
                rejected.append(f"Displayed price ${displayed_num:,.0f} exceeds the maximum price")
        price = resolve("price", cash + other_cash)
    else:
        price = resolve(
            "price", cash + other_cash + ([displayed_num] if displayed_num is not None else [])
        )
    if price is not None:
        if config.get("min_price") is not None and price < config["min_price"]:
            rejected.append(f"Price ${price:,.0f} is below the minimum ${config['min_price']:,}")
        if price < 1000:
            unknowns.append("Placeholder or unusually low asking price needs verification")
        elif config.get("max_price") is not None and price > config["max_price"]:
            rejected.append(f"Price ${price:,.0f} exceeds the maximum ${config['max_price']:,}")
    if re.search(r"\b(?:CAD|C\$|Canadian dollars)\b", title + " " + description, re.I):
        conflicts.append("Text mentions a non-USD price")

    location = raw.get("location") or {}
    location = location if isinstance(location, dict) else {}
    reverse = location.get("reverse_geocode") or location
    city = as_text(reverse.get("city"))
    state = as_text(reverse.get("state"))
    location_text = as_text(raw.get("location_text"))
    if not state and location_text:
        match = re.fullmatch(r"\s*(.+),\s*([^,]+?)\s*", location_text)
        if match:
            city, state = match.groups()
    lat, lon = location.get("latitude"), location.get("longitude")
    coords = None
    if lat is not None and lon is not None:
        try:
            coords = (float(lat), float(lon))
            if not all(math.isfinite(x) for x in coords) or not (
                -90 <= coords[0] <= 90 and -180 <= coords[1] <= 180
            ):
                coords = None
                conflicts.append("Invalid listing coordinates")
        except (ValueError, TypeError):
            conflicts.append("Invalid listing coordinates")
    add(
        "location",
        {"city": city, "state": state, "coordinates": coords},
        "location / location_text",
        location or location_text,
    )
    country = (location.get("reverse_geocode_detailed") or {}).get("country_alpha_two")
    if country and country != "US":
        rejected.append("Outside the United States")
    region = config.get("region_mode", "any")
    if region == "radius":
        if not coords:
            unknowns.append("Location needs coordinates to verify radius")
        else:

            def distance(center):
                lat1, lat2 = math.radians(coords[0]), math.radians(center["latitude"])
                dlat, dlon = lat2 - lat1, math.radians(center["longitude"] - coords[1])
                a = (
                    math.sin(dlat / 2) ** 2
                    + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
                )
                return 6371 * 2 * math.asin(min(1, math.sqrt(a)))

            if not any(distance(c) <= c["radius_km"] for c in config["search_centers"]):
                rejected.append("Outside selected search radii")
    transmission_text = as_text(
        field(raw, "vehicle_transmission", "vehicle_transmission_display_name", "transmission")
    )
    transmissions = {
        m.lower()
        for m in re.findall(
            r"\b(automatic|manual)\b", transmission_text or (title + " " + description), re.I
        )
    }
    transmission = next(iter(transmissions)) if len(transmissions) == 1 else None
    if config.get("transmission", "any") != "any":
        if transmission is None:
            unknowns.append("Transmission missing or conflicting")
        elif transmission != config["transmission"]:
            rejected.append(f"Transmission {transmission} is not {config['transmission']}")
    if transmission:
        add(
            "transmission",
            transmission,
            "vehicle_transmission / text",
            transmission_text or transmission,
        )
    if raw.get("is_sold") is True or raw.get("is_pending") is True or raw.get("is_live") is False:
        rejected.append("Sold, pending, or unavailable")
    unknowns = list(dict.fromkeys(unknowns))
    rejected = list(dict.fromkeys(rejected))
    status = "rejected" if rejected else "needs_review" if conflicts or unknowns else "match"
    return {
        "title": title,
        "description": description,
        "model": model,
        "make": make,
        "transmission": transmission,
        "year": year,
        "miles": mileage,
        "price": price,
        "currency": currency,
        "location": ", ".join(filter(None, [city, state])) or "Unknown",
        "location_precision": "approximate listing coordinates" if coords else "city/state only",
        "status": status,
        "reasons": rejected + conflicts + unknowns,
        "evidence": evidence,
        "conflicts": conflicts,
        "unknowns": unknowns,
    }
