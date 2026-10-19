import re
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

MAKES = [
    "Acura",
    "Alfa Romeo",
    "Audi",
    "BMW",
    "Buick",
    "Cadillac",
    "Chevrolet",
    "Chrysler",
    "Dodge",
    "Fiat",
    "Ford",
    "Genesis",
    "GMC",
    "Honda",
    "Hyundai",
    "Infiniti",
    "Jaguar",
    "Jeep",
    "Kia",
    "Land Rover",
    "Lexus",
    "Lincoln",
    "Mazda",
    "Mercedes-Benz",
    "Mini",
    "Mitsubishi",
    "Nissan",
    "Porsche",
    "Ram",
    "Rivian",
    "Subaru",
    "Tesla",
    "Toyota",
    "Volkswagen",
    "Volvo",
]


class Center(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    marketplace_location: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: int = Field(default=100, ge=1, le=500)


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="My vehicle search", min_length=1, max_length=80)
    makes: list[str] = Field(default_factory=list, max_length=6)
    allowed_models: list[str] = Field(default_factory=list, max_length=12)
    min_year: int | None = Field(default=None, ge=1900, le=2100)
    max_year: int | None = Field(default=None, ge=1900, le=2100)
    min_price: int | None = Field(default=None, ge=0, le=10000000)
    max_price: int | None = Field(
        default=None,
        ge=1,
        le=10000000,
        validation_alias=AliasChoices("max_price", "max_price_exclusive"),
    )
    max_miles_exclusive: int | None = Field(default=None, ge=1, le=1000000)
    transmission: Literal["any", "automatic", "manual"] = "any"
    region_mode: Literal["radius", "any"] = "any"
    stale_after_days: int = Field(default=7, ge=1, le=365)
    search_centers: list[Center] = Field(default_factory=list, max_length=20)
    search_terms: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode="before")
    @classmethod
    def migrate_saved_criteria(cls, value):
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "exclude_four_cylinder"}
            if "max_price" in value:
                value.pop("max_price_exclusive", None)
            if value.get("region_mode") == "pnw":
                value["region_mode"] = "radius" if value.get("search_centers") else "any"
        return value

    @field_validator("makes", "allowed_models", "search_terms")
    @classmethod
    def clean_terms(cls, values):
        cleaned = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if any(len(v) > 80 or not re.fullmatch(r"[\w .+&/-]+", v) for v in cleaned):
            raise ValueError(
                "Use names up to 80 characters; letters, numbers, spaces, hyphens and slashes are supported"
            )
        return cleaned

    @model_validator(mode="after")
    def check_bounds(self):
        if (
            self.max_year is not None
            and self.min_year is not None
            and self.max_year < self.min_year
        ):
            raise ValueError("Maximum year must be at least the minimum year")
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("Minimum price must be at most the maximum price")
        if self.region_mode == "radius" and not self.search_centers:
            raise ValueError("Add a search city to use radius filtering")
        return self

    def rules(self):
        return {
            **self.model_dump(),
            "allowed_models": [m.lower() for m in self.allowed_models],
        }

    def terms(self):
        return self.search_terms or [
            f"{make} {model}".strip()
            for make in (self.makes or [""])
            for model in (self.allowed_models or [""])
            if make or model
        ]


def default_search():
    return Search()


class Query(BaseModel):
    search: Search
    status: Literal["all", "match", "needs_review", "rejected", "saved"] = "all"
    text: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=80)
    reason: str = Field(default="", max_length=80)
    sort: Literal["newest", "price_asc", "price_desc", "miles", "year", "model", "reason"] = (
        "newest"
    )
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=24, ge=1, le=100)


class RunRequest(BaseModel):
    search: Search
    budget_usd: float = Field(default=2, gt=0, le=100)
    max_jobs: int = Field(default=8, ge=1, le=100)
    max_pages: int = Field(default=2, ge=1, le=5)
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    listing_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("listing_ids")
    @classmethod
    def valid_listing_ids(cls, values):
        if any(not re.fullmatch(r"\d{1,50}", v) for v in values):
            raise ValueError("Refresh accepts numeric Facebook listing IDs")
        return list(dict.fromkeys(values))
