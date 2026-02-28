"""FastAPI routes for the Regions Cheapest plugin.

Mounted at ``/plugins/regions-cheapest/`` by the core plugin manager.
"""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/summary")
async def summary(
    tenantId: str | None = Query(None, description="Azure tenant ID."),  # noqa: N803
    currency: str = Query("USD", description="ISO 4217 currency code."),
    groupBy: str = Query("region", description="Group by 'region' or 'geography'."),  # noqa: N803
) -> JSONResponse:
    """Return per-region average VM pricing summary."""
    from az_scout_plugin_regions_cheapest.service import compute_region_stats

    result = compute_region_stats(
        tenant_id=tenantId,
        currency=currency,
        group_by=groupBy,
    )
    return JSONResponse(
        {
            "rows": [r.to_dict() for r in result.rows],
            "timestampUtc": result.timestamp_utc,
            "currency": result.currency,
            "dataSource": result.data_source,
            "coveragePct": result.coverage_pct,
        }
    )


@router.get("/cheapest")
async def cheapest(
    tenantId: str | None = Query(None, description="Azure tenant ID."),  # noqa: N803
    currency: str = Query("USD", description="ISO 4217 currency code."),
    topN: int = Query(10, description="Number of cheapest regions to return."),  # noqa: N803
) -> JSONResponse:
    """Return the top N cheapest Azure regions by average VM price."""
    from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

    rows, data_source = get_cheapest_regions(
        tenant_id=tenantId,
        currency=currency,
        top_n=topN,
    )
    return JSONResponse({"rows": rows, "dataSource": data_source})
