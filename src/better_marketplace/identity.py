"""Vehicle identity from explicit title, vehicle fields, and description evidence."""

import re

from .models import MAKES

CATALOG = {
    "BMW": [
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M8",
        "X1",
        "X2",
        "X3",
        "X4",
        "X5",
        "X6",
        "X7",
        "Z3",
        "Z4",
        "i3",
        "i4",
        "i8",
        "iX",
    ],
    "Toyota": [
        "Camry",
        "Corolla",
        "Prius",
        "RAV4",
        "Highlander",
        "4Runner",
        "Tacoma",
        "Tundra",
        "Supra",
        "GR86",
        "Sienna",
        "Sequoia",
        "Land Cruiser",
    ],
    "Honda": [
        "Civic",
        "Accord",
        "CR-V",
        "HR-V",
        "Pilot",
        "Fit",
        "Ridgeline",
        "Odyssey",
        "Passport",
    ],
    "Ford": [
        "F-150",
        "F-250",
        "F-350",
        "Mustang",
        "Ranger",
        "Bronco",
        "Escape",
        "Explorer",
        "Edge",
        "Focus",
        "Fusion",
        "Maverick",
        "Transit",
    ],
    "Tesla": ["Model 3", "Model S", "Model X", "Model Y", "Cybertruck"],
    "Subaru": [
        "WRX",
        "STI",
        "BRZ",
        "Impreza",
        "Outback",
        "Forester",
        "Crosstrek",
        "Legacy",
        "Ascent",
    ],
    "Audi": [
        "A3",
        "A4",
        "A5",
        "A6",
        "A7",
        "A8",
        "S3",
        "S4",
        "S5",
        "S6",
        "S7",
        "S8",
        "RS3",
        "RS5",
        "RS6",
        "Q3",
        "Q5",
        "Q7",
        "Q8",
        "TT",
        "R8",
    ],
    "Chevrolet": [
        "Silverado",
        "Colorado",
        "Corvette",
        "Camaro",
        "Malibu",
        "Cruze",
        "Tahoe",
        "Suburban",
        "Equinox",
        "Bolt",
        "Volt",
    ],
    "Mazda": ["Mazda3", "Mazda6", "CX-3", "CX-5", "CX-9", "CX-30", "CX-50", "MX-5", "Miata"],
    "Nissan": [
        "Altima",
        "Maxima",
        "Sentra",
        "Rogue",
        "Pathfinder",
        "Frontier",
        "Titan",
        "350Z",
        "370Z",
        "GT-R",
        "Leaf",
    ],
    "Volkswagen": ["Golf", "GTI", "Jetta", "Passat", "Tiguan", "Atlas", "ID.4"],
    "Porsche": ["911", "Boxster", "Cayman", "Macan", "Cayenne", "Panamera", "Taycan"],
    "Jeep": ["Wrangler", "Cherokee", "Grand Cherokee", "Compass", "Gladiator", "Renegade"],
    "Lexus": [
        "IS 250",
        "IS 350",
        "IS 300",
        "ES 350",
        "GS 350",
        "RX 350",
        "GX 460",
        "LX 570",
        "RC 350",
        "LC 500",
    ],
    "Hyundai": [
        "Elantra",
        "Sonata",
        "Tucson",
        "Santa Fe",
        "Palisade",
        "Veloster",
        "Ioniq 5",
        "Ioniq 6",
    ],
    "Kia": ["Forte", "Optima", "K5", "Stinger", "Soul", "Sportage", "Sorento", "Telluride", "EV6"],
    "Dodge": ["Charger", "Challenger", "Durango", "Dart"],
    "Ram": ["1500", "2500", "3500"],
    "GMC": ["Sierra", "Canyon", "Yukon", "Acadia", "Terrain"],
    "Mercedes-Benz": [
        "C300",
        "C 300",
        "E350",
        "E 350",
        "C63",
        "C 63",
        "GLC",
        "GLE",
        "GLA",
        "GLS",
        "S550",
        "S 550",
    ],
}
ALIASES = {
    "vw": "Volkswagen",
    "chevy": "Chevrolet",
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
}


def canonical_make(value):
    key = value.strip().lower()
    return ALIASES.get(key, next((m for m in MAKES if m.lower() == key), value.strip()))


def pattern(name):
    escaped = re.escape(name).replace(r"\ ", r"\s+").replace(r"\-", r"[-\s]?")
    return re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.I)


def makes_in(text):
    return {canonical_make(name) for name in [*MAKES, *ALIASES] if pattern(name).search(text)}


def model_mentions(text, makes, requested):
    candidates = set(requested)
    for make in makes:
        candidates.update(CATALOG.get(canonical_make(make), []))
    # Prefer a longer model name over its substring (Grand Cherokee vs Cherokee).
    matches = []
    for name in sorted(candidates, key=len, reverse=True):
        for match in pattern(name).finditer(text):
            if not any(
                start <= match.start() and match.end() <= end for _, _, start, end in matches
            ):
                matches.append((name.lower(), match[0], match.start(), match.end()))
    return matches
