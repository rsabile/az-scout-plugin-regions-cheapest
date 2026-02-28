"""MCP tools for the Regions Cheapest plugin.

These are plain functions with type annotations; the core MCP server
registers them automatically via ``get_mcp_tools()``.
"""


def regions_price_summary(
    tenant_id: str | None = None,
    currency: str = "USD",
    group_by: str = "region",
    sku_sample: list[str] | None = None,
) -> list[dict[str, object]]:
    """Return per-region average VM pricing summary.

    **Definitions:**

    * *avgPrice* — mean hourly Linux retail (pay-as-you-go) price across
      a curated sample of VM SKUs in the region.
    * *availabilityPct* — percentage of sample SKUs that have a valid
      price for the region: ``(pricedCount / sampleSize) × 100``.
    * *timestampUtc* — ISO 8601 UTC timestamp when data was computed
      (results are cached for ~10 minutes).

    Parameters
    ----------
    tenant_id:
        Azure AD tenant ID.  When omitted the default tenant is used.
    currency:
        ISO 4217 currency code (default ``"USD"``).
    group_by:
        ``"region"`` (default) or ``"geography"`` for grouped view.
    sku_sample:
        Override the built-in SKU sample list.  Pass a list of ARM SKU
        names (e.g. ``["Standard_D2s_v5", "Standard_B2s"]``).

    Returns
    -------
    list[dict]
        One dict per region with keys: geography, regionName, regionId,
        avgPrice, availabilityPct, sampleSize, pricedCount, timestampUtc.
    """
    from az_scout_plugin_regions_cheapest.service import compute_region_stats

    result = compute_region_stats(
        tenant_id=tenant_id,
        currency=currency,
        group_by=group_by,
        sku_sample=sku_sample,
    )
    return [r.to_dict() for r in result.rows]


def cheapest_regions(
    tenant_id: str | None = None,
    currency: str = "USD",
    top_n: int = 10,
    sku_sample: list[str] | None = None,
) -> list[dict[str, object]]:
    """Return the top N cheapest Azure regions by average VM price.

    Each result includes a ``deltaVsCheapest`` field showing the price
    difference compared to the cheapest region.

    **Assumptions:**

    * Pricing is based on a curated sample of ~10 popular VM SKUs.
    * Only Linux pay-as-you-go hourly rates are considered.
    * Results are cached for ~10 minutes.
    * ``timestampUtc`` indicates when data was last computed.

    Parameters
    ----------
    tenant_id:
        Azure AD tenant ID.
    currency:
        ISO 4217 currency code (default ``"USD"``).
    top_n:
        Number of cheapest regions to return (default 10).
    sku_sample:
        Override the built-in SKU sample list.

    Returns
    -------
    list[dict]
        Ranked list with keys: rank, geography, regionName, regionId,
        avgPrice, deltaVsCheapest, availabilityPct, sampleSize,
        pricedCount, timestampUtc.
    """
    from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

    return get_cheapest_regions(
        tenant_id=tenant_id,
        currency=currency,
        top_n=top_n,
        sku_sample=sku_sample,
    )
