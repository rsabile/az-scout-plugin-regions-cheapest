"""Tests for the Regions Cheapest plugin."""

from collections.abc import Generator
from unittest.mock import patch

import pytest

# Mock region prices returned by core get_retail_prices
MOCK_PRICES_EASTUS: dict[str, dict[str, object]] = {
    "Standard_B2s": {"paygo": 0.0416, "spot": 0.012, "currency": "USD"},
    "Standard_B4ms": {"paygo": 0.166, "spot": 0.05, "currency": "USD"},
    "Standard_D2s_v5": {"paygo": 0.096, "spot": 0.029, "currency": "USD"},
    "Standard_D4s_v5": {"paygo": 0.192, "spot": 0.058, "currency": "USD"},
    "Standard_D8s_v5": {"paygo": 0.384, "spot": 0.115, "currency": "USD"},
    "Standard_E2s_v5": {"paygo": 0.126, "spot": 0.038, "currency": "USD"},
    "Standard_E4s_v5": {"paygo": 0.252, "spot": 0.076, "currency": "USD"},
    "Standard_F2s_v2": {"paygo": 0.085, "spot": 0.025, "currency": "USD"},
    "Standard_F4s_v2": {"paygo": 0.169, "spot": 0.051, "currency": "USD"},
    "Standard_L8s_v3": {"paygo": 0.624, "spot": 0.187, "currency": "USD"},
}

MOCK_PRICES_WESTEUROPE: dict[str, dict[str, object]] = {
    "Standard_B2s": {"paygo": 0.0522, "spot": 0.016, "currency": "USD"},
    "Standard_B4ms": {"paygo": 0.208, "spot": 0.062, "currency": "USD"},
    "Standard_D2s_v5": {"paygo": 0.113, "spot": 0.034, "currency": "USD"},
    "Standard_D4s_v5": {"paygo": 0.226, "spot": 0.068, "currency": "USD"},
    "Standard_D8s_v5": {"paygo": 0.452, "spot": 0.136, "currency": "USD"},
    "Standard_E2s_v5": {"paygo": 0.148, "spot": 0.044, "currency": "USD"},
    "Standard_E4s_v5": {"paygo": 0.296, "spot": 0.089, "currency": "USD"},
    "Standard_F2s_v2": {"paygo": 0.1, "spot": 0.03, "currency": "USD"},
    "Standard_F4s_v2": {"paygo": 0.199, "spot": 0.06, "currency": "USD"},
    "Standard_L8s_v3": {"paygo": 0.736, "spot": 0.221, "currency": "USD"},
}

# Partial coverage for a third region (some SKUs missing)
MOCK_PRICES_JAPANEAST: dict[str, dict[str, object]] = {
    "Standard_B2s": {"paygo": 0.056, "spot": 0.017, "currency": "USD"},
    "Standard_D2s_v5": {"paygo": 0.128, "spot": 0.038, "currency": "USD"},
    "Standard_D4s_v5": {"paygo": 0.256, "spot": 0.077, "currency": "USD"},
    # Only 3 SKUs available → availability 30%
}

MOCK_REGIONS: list[dict[str, str]] = [
    {"name": "eastus", "displayName": "East US"},
    {"name": "westeurope", "displayName": "West Europe"},
    {"name": "japaneast", "displayName": "Japan East"},
]


def _mock_get_retail_prices(region: str, currency_code: str = "USD") -> dict[str, dict]:
    """Mock core pricing function."""
    mapping: dict[str, dict[str, dict[str, object]]] = {
        "eastus": MOCK_PRICES_EASTUS,
        "westeurope": MOCK_PRICES_WESTEUROPE,
        "japaneast": MOCK_PRICES_JAPANEAST,
    }
    return mapping.get(region, {})


def _mock_list_regions(
    subscription_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict[str, str]]:
    """Mock core discovery function."""
    return MOCK_REGIONS


@pytest.fixture(autouse=True)
def _mock_azure(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Patch core Azure API functions for all tests."""
    with (
        patch(
            "az_scout.azure_api.pricing.get_retail_prices",
            side_effect=_mock_get_retail_prices,
        ),
        patch(
            "az_scout.azure_api.discovery.list_regions",
            side_effect=_mock_list_regions,
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
        # Mean of all 10 sample SKU paygo prices
        expected = sum(
            v["paygo"]
            for v in MOCK_PRICES_EASTUS.values()
            if v.get("paygo") is not None  # type: ignore[union-attr]
        ) / len(MOCK_PRICES_EASTUS)
        assert eastus.avg_price is not None
        assert abs(eastus.avg_price - expected) < 0.001

    def test_availability_full_coverage(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        eastus = next(r for r in result.rows if r.region_id == "eastus")
        assert eastus.availability_pct == 100.0
        assert eastus.priced_count == 10
        assert eastus.sku_count == 10

    def test_availability_partial_coverage(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        japan = next(r for r in result.rows if r.region_id == "japaneast")
        # Japan has 3 SKUs returned, all with valid prices → 100% availability
        assert japan.availability_pct == 100.0
        assert japan.priced_count == 3
        assert japan.sku_count == 3

    def test_sorted_by_avg_price_ascending(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        prices = [r.avg_price for r in result.rows if r.avg_price is not None]
        assert prices == sorted(prices)

    def test_timestamp_is_set(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        assert result.timestamp_utc != ""
        assert "T" in result.timestamp_utc  # ISO format

    def test_currency_passed_through(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats(currency="EUR")
        assert result.currency == "EUR"

    def test_data_source_is_live_by_default(self) -> None:
        """Without DB plugin, data_source should be 'live'."""
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        result = compute_region_stats()
        assert result.data_source == "live"


class TestCaching:
    """Tests for caching behaviour."""

    def test_cache_returns_same_result(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        r1 = compute_region_stats()
        r2 = compute_region_stats()
        assert r1.timestamp_utc == r2.timestamp_utc
        assert len(r1.rows) == len(r2.rows)

    def test_different_params_not_cached(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        r1 = compute_region_stats(currency="USD")
        r2 = compute_region_stats(currency="EUR")
        # Different currency → different cache key → different timestamp
        assert r1.currency != r2.currency


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

    def test_returns_data_source(self) -> None:
        from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

        _result, ds = get_cheapest_regions()
        assert ds in ("db", "hybrid", "live")


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
            "availabilityPct",
            "skuCount",
            "pricedCount",
            "timestampUtc",
            "countryCode",
            "lat",
            "lon",
        }
        assert set(row.keys()) == expected_keys  # type: ignore[union-attr]

    def test_regions_price_summary_includes_data_source(self) -> None:
        from az_scout_plugin_regions_cheapest.mcp_tools import regions_price_summary

        result = regions_price_summary()
        assert result["dataSource"] in ("db", "hybrid", "live")

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
            "deltaVsCheapest",
            "availabilityPct",
            "skuCount",
            "pricedCount",
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
            availability_pct=100.0,
            sku_count=10,
            priced_count=10,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            country_code="US",
            lat=37.37,
            lon=-79.82,
        )
        d = row.to_dict()
        assert d["regionId"] == "eastus"
        assert d["avgPrice"] == 0.2134
        assert d["countryCode"] == "US"

    def test_cheapest_region_row_to_dict(self) -> None:
        from az_scout_plugin_regions_cheapest.models import CheapestRegionRow

        row = CheapestRegionRow(
            rank=1,
            geography="US",
            region_name="East US",
            region_id="eastus",
            avg_price=0.2134,
            delta_vs_cheapest=0.0,
            availability_pct=100.0,
            sku_count=10,
            priced_count=10,
            timestamp_utc="2026-01-01T00:00:00+00:00",
        )
        d = row.to_dict()
        assert d["rank"] == 1
        assert d["deltaVsCheapest"] == 0.0


# ---------------------------------------------------------------------------
# Provider integration tests
# ---------------------------------------------------------------------------


def _all_sku_names() -> list[str]:
    """Return sorted list of all SKU names from the mock data."""
    return sorted(MOCK_PRICES_EASTUS.keys())


def _build_db_rows(
    regions: list[str],
    skus: list[str],
    prices_map: dict[str, dict[str, dict[str, object]]],
) -> list[dict[str, object]]:
    """Build mock DB query response rows."""
    rows: list[dict[str, object]] = []
    for region in regions:
        region_data = prices_map.get(region, {})
        for sku in skus:
            if sku in region_data:
                entry = region_data[sku]
                rows.append(
                    {
                        "region": region,
                        "sku": sku,
                        "price_hourly": entry["paygo"],
                        "expires_at_utc": "2026-03-01T00:00:00Z",
                    }
                )
    return rows


class TestDbProviderAvailableFullCoverage:
    """Case 1: DB plugin has full coverage → dataSource='db', no core calls."""

    def test_uses_db_source(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        prices_map: dict[str, dict[str, dict[str, object]]] = {
            "eastus": MOCK_PRICES_EASTUS,
            "westeurope": MOCK_PRICES_WESTEUROPE,
            "japaneast": MOCK_PRICES_JAPANEAST,
        }

        def mock_status(*a: object, **kw: object) -> dict[str, object]:
            return {"db_connected": True, "retail_prices_count": 5000}

        def mock_query(*a: object, **kw: object) -> dict[str, object]:
            body = kw.get("json", {})
            req_regions: list[str] = body.get("regions", [])
            req_skus: list[str] = body.get("skus", [])
            return {
                "currency": "USD",
                "rows": _build_db_rows(req_regions, req_skus, prices_map),
            }

        with (
            patch(
                "az_scout_plugin_regions_cheapest.providers._internal_get",
                side_effect=mock_status,
            ),
            patch(
                "az_scout_plugin_regions_cheapest.providers._internal_post",
                side_effect=mock_query,
            ),
        ):
            result = compute_region_stats()

        assert result.data_source == "db"
        assert len(result.rows) == 3
        # Prices should be consistent with mock data
        eastus = next(r for r in result.rows if r.region_id == "eastus")
        assert eastus.avg_price is not None


class TestDbProviderInstalledButEmpty:
    """Case 2: DB plugin connected but has no data → dataSource='live'."""

    def test_falls_back_to_live(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        def mock_status(*a: object, **kw: object) -> dict[str, object]:
            return {"db_connected": True, "retail_prices_count": 0}

        with patch(
            "az_scout_plugin_regions_cheapest.providers._internal_get",
            side_effect=mock_status,
        ):
            result = compute_region_stats()

        assert result.data_source == "live"
        assert len(result.rows) == 3


class TestDbProviderPartialCoverage:
    """Case 3: DB has some data but not enough → dataSource='hybrid'."""

    def test_hybrid_fill(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        # DB only returns prices for eastus, not for westeurope/japaneast
        def mock_status(*a: object, **kw: object) -> dict[str, object]:
            return {"db_connected": True, "retail_prices_count": 5000}

        def mock_query(*a: object, **kw: object) -> dict[str, object]:
            body = kw.get("json", {})
            req_skus: list[str] = body.get("skus", [])
            # Only return eastus data → partial coverage
            return {
                "currency": "USD",
                "rows": _build_db_rows(
                    ["eastus"],
                    req_skus,
                    {
                        "eastus": MOCK_PRICES_EASTUS,
                    },
                ),
            }

        with (
            patch(
                "az_scout_plugin_regions_cheapest.providers._internal_get",
                side_effect=mock_status,
            ),
            patch(
                "az_scout_plugin_regions_cheapest.providers._internal_post",
                side_effect=mock_query,
            ),
        ):
            result = compute_region_stats()

        assert result.data_source == "hybrid"
        assert len(result.rows) == 3
        # All regions should still have data (filled by core)
        for row in result.rows:
            assert row.avg_price is not None


class TestDbProviderUnreachable:
    """Case 4: DB plugin unreachable/timeout → fallback to live."""

    def test_timeout_falls_back(self) -> None:
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        def mock_status(*a: object, **kw: object) -> dict[str, object]:
            raise ConnectionError("Connection refused")

        with patch(
            "az_scout_plugin_regions_cheapest.providers._internal_get",
            side_effect=mock_status,
        ):
            result = compute_region_stats()

        assert result.data_source == "live"
        assert len(result.rows) == 3

    def test_status_ok_but_query_fails(self) -> None:
        """Status is reachable but the query endpoint fails."""
        from az_scout_plugin_regions_cheapest.service import compute_region_stats

        def mock_status(*a: object, **kw: object) -> dict[str, object]:
            return {"db_connected": True, "retail_prices_count": 5000}

        def mock_query(*a: object, **kw: object) -> dict[str, object]:
            raise TimeoutError("read timed out")

        with (
            patch(
                "az_scout_plugin_regions_cheapest.providers._internal_get",
                side_effect=mock_status,
            ),
            patch(
                "az_scout_plugin_regions_cheapest.providers._internal_post",
                side_effect=mock_query,
            ),
        ):
            result = compute_region_stats()

        # DB query returned empty → treated as live fallback
        assert result.data_source == "live"
        assert len(result.rows) == 3
