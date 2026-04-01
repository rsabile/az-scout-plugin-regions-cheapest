"""Tests for the Regions Cheapest plugin.

All BDD API calls are mocked via ``az_scout_bdd_sku.api_client``.
"""

from collections.abc import Generator
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Mock BDD API response data
# ---------------------------------------------------------------------------

MOCK_SUMMARY_ROWS: list[dict[str, object]] = [
    {
        "id": 1,
        "runId": 100,
        "snapshotUtc": "2026-01-15T02:00:00+00:00",
        "region": "eastus",
        "category": None,
        "priceType": "retail",
        "currencyCode": "USD",
        "avgPrice": 0.2134,
        "medianPrice": 0.1690,
        "minPrice": 0.0416,
        "maxPrice": 0.6240,
        "p10Price": 0.0500,
        "p25Price": 0.0960,
        "p75Price": 0.2520,
        "p90Price": 0.3840,
        "skuCount": 10,
    },
    {
        "id": 2,
        "runId": 100,
        "snapshotUtc": "2026-01-15T02:00:00+00:00",
        "region": "westeurope",
        "category": None,
        "priceType": "retail",
        "currencyCode": "USD",
        "avgPrice": 0.2530,
        "medianPrice": 0.1990,
        "minPrice": 0.0522,
        "maxPrice": 0.7360,
        "p10Price": 0.0620,
        "p25Price": 0.1130,
        "p75Price": 0.2960,
        "p90Price": 0.4520,
        "skuCount": 10,
    },
    {
        "id": 3,
        "runId": 100,
        "snapshotUtc": "2026-01-15T02:00:00+00:00",
        "region": "japaneast",
        "category": None,
        "priceType": "retail",
        "currencyCode": "USD",
        "avgPrice": 0.1467,
        "medianPrice": 0.1280,
        "minPrice": 0.0560,
        "maxPrice": 0.2560,
        "p10Price": 0.0600,
        "p25Price": 0.0700,
        "p75Price": 0.2000,
        "p90Price": 0.2400,
        "skuCount": 3,
    },
]

MOCK_CHEAPEST_ROWS: list[dict[str, object]] = [
    {
        "region": "japaneast",
        "avgPrice": 0.1467,
        "medianPrice": 0.1280,
        "skuCount": 3,
        "snapshotUtc": "2026-01-15T02:00:00+00:00",
    },
    {
        "region": "eastus",
        "avgPrice": 0.2134,
        "medianPrice": 0.1690,
        "skuCount": 10,
        "snapshotUtc": "2026-01-15T02:00:00+00:00",
    },
    {
        "region": "westeurope",
        "avgPrice": 0.2530,
        "medianPrice": 0.1990,
        "skuCount": 10,
        "snapshotUtc": "2026-01-15T02:00:00+00:00",
    },
]


def _mock_v1_pricing_summary_latest(**kwargs: object) -> dict[str, object]:
    """Mock BDD API: v1_pricing_summary_latest."""
    return {"rows": MOCK_SUMMARY_ROWS, "nextCursor": ""}


def _mock_v1_pricing_cheapest(**kwargs: object) -> dict[str, object]:
    """Mock BDD API: v1_pricing_cheapest."""
    limit = kwargs.get("limit", 10)
    rows = MOCK_CHEAPEST_ROWS[: int(limit)]  # type: ignore[arg-type]
    return {"rows": rows}


@pytest.fixture(autouse=True)
def _mock_bdd_api() -> Generator[None, None, None]:
    """Patch BDD API client functions for all tests."""
    with (
        patch(
            "az_scout_bdd_sku.api_client.v1_pricing_summary_latest",
            side_effect=_mock_v1_pricing_summary_latest,
        ),
        patch(
            "az_scout_bdd_sku.api_client.v1_pricing_cheapest",
            side_effect=_mock_v1_pricing_cheapest,
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None, None, None]:
    """Clear the result cache before each test."""
    from az_scout_plugin_regions_cheapest.service import _result_cache

    _result_cache.clear()
    yield
    _result_cache.clear()


class TestComputeRegionStats:
    """Tests for compute_region_stats service function."""

    def test_returns_all_regions(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        assert len(result.rows) == 3

    def test_avg_price_is_correct(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        eastus = next(r for r in result.rows if r.region_id == "eastus")
        assert eastus.avg_price is not None
        assert abs(eastus.avg_price - 0.2134) < 0.001

    def test_median_price_populated(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        eastus = next(r for r in result.rows if r.region_id == "eastus")
        assert eastus.median_price == 0.1690

    def test_min_max_price_populated(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        eastus = next(r for r in result.rows if r.region_id == "eastus")
        assert eastus.min_price == 0.0416
        assert eastus.max_price == 0.6240

    def test_sku_count(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        eastus = next(r for r in result.rows if r.region_id == "eastus")
        assert eastus.sku_count == 10

    def test_sorted_by_avg_price_ascending(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        prices = [r.avg_price for r in result.rows if r.avg_price is not None]
        assert prices == sorted(prices)

    def test_timestamp_is_set(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        assert result.timestamp_utc != ""
        assert "T" in result.timestamp_utc

    def test_data_source_is_bdd(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        assert result.data_source == "bdd"

    def test_api_not_configured_raises(self) -> None:
        from az_scout_bdd_sku.api_client import ApiNotConfiguredError

        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        with (
            patch(
                "az_scout_bdd_sku.api_client.v1_pricing_summary_latest",
                side_effect=ApiNotConfiguredError("not configured"),
            ),
            pytest.raises(ApiNotConfiguredError),
        ):
            compute_region_stats()

    def test_group_by_geography_aggregates(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats(group_by="geography")
        # All 3 mock rows use geography "Unknown" (no region_geography.json in tests)
        assert len(result.rows) >= 1
        # row ids are geography names, not region ids
        assert all(r.region_id == r.geography for r in result.rows)

    def test_group_by_geography_sku_count_summed(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        region_result = compute_region_stats(group_by="region")
        geo_result = compute_region_stats(group_by="geography")
        total_region_skus = sum(r.sku_count for r in region_result.rows)
        total_geo_skus = sum(r.sku_count for r in geo_result.rows)
        assert total_geo_skus == total_region_skus

    def test_group_by_geography_sorted_ascending(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats(group_by="geography")
        prices = [r.avg_price for r in result.rows if r.avg_price is not None]
        assert prices == sorted(prices)


class TestCaching:
    """Tests for caching behaviour."""

    def test_cache_returns_same_result(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        r1 = compute_region_stats()
        r2 = compute_region_stats()
        assert r1.timestamp_utc == r2.timestamp_utc
        assert len(r1.rows) == len(r2.rows)


class TestGetCheapestRegions:
    """Tests for get_cheapest_regions."""

    def test_returns_top_n(self) -> None:
        from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

        result, _ds = get_cheapest_regions(top_n=2)
        assert len(result) == 2

    def test_ordered_by_price(self) -> None:
        from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

        result, _ds = get_cheapest_regions()
        prices = [r["avgPrice"] for r in result]
        assert prices == sorted(prices)

    def test_delta_vs_cheapest(self) -> None:
        from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

        result, _ds = get_cheapest_regions()
        assert result[0]["deltaVsCheapest"] == 0.0
        for row in result[1:]:
            assert row["deltaVsCheapest"] > 0  # type: ignore[operator]

    def test_rank_starts_at_1(self) -> None:
        from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

        result, _ds = get_cheapest_regions()
        assert result[0]["rank"] == 1

    def test_returns_data_source_bdd(self) -> None:
        from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

        _result, ds = get_cheapest_regions()
        assert ds == "bdd"


class TestMCPTools:
    """Tests for MCP tool functions."""

    def test_regions_price_summary_returns_dict(self) -> None:
        from az_scout_plugin_regions_cheapest.mcp_tools import regions_price_summary

        result = regions_price_summary()
        assert isinstance(result, dict)
        assert "rows" in result
        assert "dataSource" in result
        assert len(result["rows"]) == 3  # type: ignore[arg-type]

    def test_regions_price_summary_row_keys(self) -> None:
        from az_scout_plugin_regions_cheapest.mcp_tools import regions_price_summary

        result = regions_price_summary()
        row = result["rows"][0]  # type: ignore[index]
        expected_keys = {
            "geography",
            "regionName",
            "regionId",
            "avgPrice",
            "medianPrice",
            "minPrice",
            "maxPrice",
            "skuCount",
            "timestampUtc",
            "countryCode",
            "lat",
            "lon",
        }
        assert set(row.keys()) == expected_keys  # type: ignore[union-attr]

    def test_regions_price_summary_data_source_bdd(self) -> None:
        from az_scout_plugin_regions_cheapest.mcp_tools import regions_price_summary

        result = regions_price_summary()
        assert result["dataSource"] == "bdd"

    def test_cheapest_regions_returns_dict(self) -> None:
        from az_scout_plugin_regions_cheapest.mcp_tools import cheapest_regions

        result = cheapest_regions(top_n=2)
        assert isinstance(result, dict)
        assert "rows" in result
        assert "dataSource" in result
        assert len(result["rows"]) == 2  # type: ignore[arg-type]

    def test_cheapest_regions_row_keys(self) -> None:
        from az_scout_plugin_regions_cheapest.mcp_tools import cheapest_regions

        result = cheapest_regions()
        row = result["rows"][0]  # type: ignore[index]
        expected_keys = {
            "rank",
            "geography",
            "regionName",
            "regionId",
            "avgPrice",
            "medianPrice",
            "deltaVsCheapest",
            "skuCount",
            "timestampUtc",
        }
        assert set(row.keys()) == expected_keys  # type: ignore[union-attr]


class TestPluginRegistration:
    """Tests for plugin object protocol compliance."""

    def test_plugin_name(self) -> None:
        from az_scout_plugin_regions_cheapest import plugin

        assert plugin.name == "regions-cheapest"

    def test_plugin_version(self) -> None:
        from az_scout_plugin_regions_cheapest import plugin

        assert plugin.version == "0.1.0"

    def test_get_router(self) -> None:
        from az_scout_plugin_regions_cheapest import plugin

        router = plugin.get_router()
        assert router is not None

    def test_get_mcp_tools(self) -> None:
        from az_scout_plugin_regions_cheapest import plugin

        tools = plugin.get_mcp_tools()
        assert tools is not None
        assert len(tools) == 2

    def test_get_static_dir(self) -> None:
        from az_scout_plugin_regions_cheapest import plugin

        static_dir = plugin.get_static_dir()
        assert static_dir is not None
        assert static_dir.exists()

    def test_get_tabs(self) -> None:
        from az_scout_plugin_regions_cheapest import plugin

        tabs = plugin.get_tabs()
        assert tabs is not None
        assert len(tabs) == 1
        assert tabs[0].id == "regions-cheapest"
        assert tabs[0].label == "Regions Cheapest"

    def test_get_chat_modes(self) -> None:
        from az_scout_plugin_regions_cheapest import plugin

        assert plugin.get_chat_modes() is None


class TestModels:
    """Tests for data models."""

    def test_region_price_row_to_dict(self) -> None:
        from az_scout_plugin_regions_cheapest.models import RegionPriceRow

        row = RegionPriceRow(
            geography="US",
            region_name="East US",
            region_id="eastus",
            avg_price=0.2134,
            median_price=0.1690,
            min_price=0.0416,
            max_price=0.6240,
            sku_count=10,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            country_code="US",
            lat=37.37,
            lon=-79.82,
        )
        d = row.to_dict()
        assert d["regionId"] == "eastus"
        assert d["avgPrice"] == 0.2134
        assert d["medianPrice"] == 0.1690
        assert d["minPrice"] == 0.0416
        assert d["maxPrice"] == 0.6240
        assert d["countryCode"] == "US"

    def test_cheapest_region_row_to_dict(self) -> None:
        from az_scout_plugin_regions_cheapest.models import CheapestRegionRow

        row = CheapestRegionRow(
            rank=1,
            geography="US",
            region_name="East US",
            region_id="eastus",
            avg_price=0.2134,
            median_price=0.1690,
            delta_vs_cheapest=0.0,
            sku_count=10,
            timestamp_utc="2026-01-01T00:00:00+00:00",
        )
        d = row.to_dict()
        assert d["rank"] == 1
        assert d["deltaVsCheapest"] == 0.0
        assert d["medianPrice"] == 0.169
