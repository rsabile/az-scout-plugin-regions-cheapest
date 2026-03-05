"""MCP tools for the Regions Cheapest plugin.

These are plain functions with type annotations; the core MCP server
registers them automatically via ``get_mcp_tools()``.
"""


def regions_price_summary(
    group_by: str = "region",
) -> dict[str, object]:
    """Return per-region average VM pricing summary.

    Uses pre-aggregated pricing data from the BDD SKU API.

    **Definitions:**

    * *avgPrice* — mean hourly Linux retail (pay-as-you-go) price across
      all VM SKUs available in the region.
    * *medianPrice* — median hourly price across all VM SKUs.
    * *minPrice* / *maxPrice* — cheapest / most expensive SKU in the region.
    * *timestampUtc* — ISO 8601 UTC timestamp of the pricing snapshot.
    * *dataSource* — always ``"bdd"`` (data from the BDD SKU database).

    Parameters
    ----------
    group_by:
        ``"region"`` (default) or ``"geography"`` for grouped view.

    Returns
    -------
    dict
        Contains ``rows`` (list of region dicts) and ``dataSource``.
    """
    from az_scout_plugin_regions_cheapest.service import compute_region_stats

    result = compute_region_stats(group_by=group_by)
    return {
        "rows": [r.to_dict() for r in result.rows],
        "dataSource": result.data_source,
    }


def cheapest_regions(
    top_n: int = 10,
) -> dict[str, object]:
    """Return the top N cheapest Azure regions by average VM price.

    Uses pre-aggregated pricing data from the BDD SKU API.

    Each result includes a ``deltaVsCheapest`` field showing the price
    difference compared to the cheapest region.

    **Assumptions:**

    * Pricing is based on all VM SKUs available in each region.
    * Only Linux pay-as-you-go hourly rates are considered (USD).
    * Results are cached for ~10 minutes.
    * ``timestampUtc`` indicates the pricing snapshot timestamp.

    Parameters
    ----------
    top_n:
        Number of cheapest regions to return (default 10).

    Returns
    -------
    dict
        Contains ``rows`` (ranked list) and ``dataSource``.
    """
    from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

    rows, data_source = get_cheapest_regions(top_n=top_n)
    return {"rows": rows, "dataSource": data_source}
