"""MCP tools for the Regions Cheapest plugin.

These are plain functions with type annotations; the core MCP server
registers them automatically via ``get_mcp_tools()``.
"""


def regions_price_summary(
    tenant_id: str | None = None,
    currency: str = "USD",
    group_by: str = "region",
) -> dict[str, object]:
    """Return per-region average VM pricing summary.

    DB cache is used when available (via the bdd-sku plugin), otherwise
    live core API calls are used.

    **Definitions:**

    * *avgPrice* — mean hourly Linux retail (pay-as-you-go) price across
      all VM SKUs available in the region.
    * *availabilityPct* — percentage of VM SKUs that have a valid
      price for the region: ``(pricedCount / skuCount) × 100``.
    * *timestampUtc* — ISO 8601 UTC timestamp when data was computed
      (results are cached for ~10 minutes; underlying prices cached ~1 hour).
    * *dataSource* — ``"db"`` if cached data was used, ``"hybrid"``
      if a mix of cache and live calls, ``"live"`` for live API only.

    Parameters
    ----------
    tenant_id:
        Azure AD tenant ID.  When omitted the default tenant is used.
    currency:
        ISO 4217 currency code (default ``"USD"``).
    group_by:
        ``"region"`` (default) or ``"geography"`` for grouped view.

    Returns
    -------
    dict
        Contains ``rows`` (list of region dicts), ``dataSource``, and
        ``coveragePct``.
    """
    from az_scout_plugin_regions_cheapest.service import compute_region_stats

    result = compute_region_stats(
        tenant_id=tenant_id,
        currency=currency,
        group_by=group_by,
    )
    return {
        "rows": [r.to_dict() for r in result.rows],
        "dataSource": result.data_source,
        "coveragePct": result.coverage_pct,
    }


def cheapest_regions(
    tenant_id: str | None = None,
    currency: str = "USD",
    top_n: int = 10,
) -> dict[str, object]:
    """Return the top N cheapest Azure regions by average VM price.

    DB cache is used when available (via the bdd-sku plugin), otherwise
    live core API calls are used.

    Each result includes a ``deltaVsCheapest`` field showing the price
    difference compared to the cheapest region.

    **Assumptions:**

    * Pricing is based on all VM SKUs available in each region.
    * Only Linux pay-as-you-go hourly rates are considered.
    * Results are cached for ~10 minutes (underlying prices ~1 hour).
    * ``timestampUtc`` indicates when data was last computed.

    Parameters
    ----------
    tenant_id:
        Azure AD tenant ID.
    currency:
        ISO 4217 currency code (default ``"USD"``).
    top_n:
        Number of cheapest regions to return (default 10).

    Returns
    -------
    dict
        Contains ``rows`` (ranked list) and ``dataSource``.
    """
    from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

    rows, data_source = get_cheapest_regions(
        tenant_id=tenant_id,
        currency=currency,
        top_n=top_n,
    )
    return {"rows": rows, "dataSource": data_source}
