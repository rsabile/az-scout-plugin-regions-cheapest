"""Data models for the Regions Cheapest plugin."""

from dataclasses import dataclass, field


@dataclass
class RegionPriceRow:
    """A single row in the region pricing summary."""

    geography: str
    region_name: str
    region_id: str
    avg_price: float | None
    availability_pct: float
    sample_size: int
    priced_count: int
    timestamp_utc: str
    country_code: str = ""
    lat: float | None = None
    lon: float | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-compatible dict."""
        return {
            "geography": self.geography,
            "regionName": self.region_name,
            "regionId": self.region_id,
            "avgPrice": self.avg_price,
            "availabilityPct": round(self.availability_pct, 2),
            "sampleSize": self.sample_size,
            "pricedCount": self.priced_count,
            "timestampUtc": self.timestamp_utc,
            "countryCode": self.country_code,
            "lat": self.lat,
            "lon": self.lon,
        }


@dataclass
class CheapestRegionRow:
    """A row in the cheapest-regions ranking."""

    rank: int
    geography: str
    region_name: str
    region_id: str
    avg_price: float
    delta_vs_cheapest: float
    availability_pct: float
    sample_size: int
    priced_count: int
    timestamp_utc: str

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-compatible dict."""
        return {
            "rank": self.rank,
            "geography": self.geography,
            "regionName": self.region_name,
            "regionId": self.region_id,
            "avgPrice": round(self.avg_price, 6),
            "deltaVsCheapest": round(self.delta_vs_cheapest, 6),
            "availabilityPct": round(self.availability_pct, 2),
            "sampleSize": self.sample_size,
            "pricedCount": self.priced_count,
            "timestampUtc": self.timestamp_utc,
        }


@dataclass
class RegionPriceSummaryResult:
    """Full result of compute_region_stats."""

    rows: list[RegionPriceRow] = field(default_factory=list)
    timestamp_utc: str = ""
    sample_skus: list[str] = field(default_factory=list)
    currency: str = "USD"
