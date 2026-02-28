# az-scout-plugin-regions-cheapest

An [az-scout](https://github.com/lrivallain/az-scout) plugin that ranks Azure regions by average VM hourly price. It provides an interactive world map (choropleth or region-point view), a bar chart, and a sortable table — all in a single UI tab.

## Features

- **World map** — D3.js choropleth or region-point overlay showing price distribution across Azure regions
- **Bar chart** — regions sorted by ascending average price (cheapest first)
- **Sortable table** — per-region details: average price, SKU count, availability %, geography
- **Group by geography** — toggle between per-region and per-geography views
- **Multi-currency** — supports USD, EUR, GBP, JPY, AUD, CAD, CHF, INR, BRL
- **Smart data sourcing** — uses the [bdd-sku](https://github.com/rsabile/az-scout-plugin-bdd-sku) DB cache when available, falls back to live Azure Retail Prices API, or hybridises both
- **MCP tools** — `regions_price_summary` and `cheapest_regions` exposed on the MCP server for AI/chat integration
- **URL hash routing** — `#regions-cheapest` deep-links to the plugin tab

## Pricing methodology

- **Scope:** all VM SKUs available in each Azure region (not a subset)
- **Rate type:** Linux pay-as-you-go hourly retail prices
- **Average price:** mean of all SKUs with a valid price in the region
- **Availability %:** `(priced SKU count / total SKU count) × 100`
- **Caching:** results are cached for ~10 minutes; underlying prices for ~1 hour

## Installation

```bash
# From the az-scout project directory
uv pip install -e /path/to/az-scout-plugin-regions-cheapest

# Or install from git
uv pip install git+https://github.com/rsabile/az-scout-plugin-regions-cheapest.git

# Start az-scout — the plugin is auto-discovered
az-scout web
```

The plugin registers itself via the `az_scout.plugins` entry point. No manual configuration is needed.

## API endpoints

Mounted at `/plugins/regions-cheapest/`:

| Endpoint | Method | Parameters | Description |
|---|---|---|---|
| `/summary` | GET | `tenantId?`, `currency` (default `USD`), `groupBy` (`region` \| `geography`) | Per-region average VM pricing summary |
| `/cheapest` | GET | `tenantId?`, `currency` (default `USD`), `topN` (default `10`) | Top N cheapest regions with delta vs. cheapest |

## MCP tools

| Tool | Parameters | Description |
|---|---|---|
| `regions_price_summary` | `tenant_id?`, `currency`, `group_by` | Full pricing summary across all regions |
| `cheapest_regions` | `tenant_id?`, `currency`, `top_n` | Ranked list of the N cheapest regions |

## Project structure

```
az-scout-plugin-regions-cheapest/
├── pyproject.toml
├── README.md
├── LICENSE.txt
└── src/
    └── az_scout_plugin_regions_cheapest/
        ├── __init__.py       # Plugin class + entry point instance
        ├── routes.py         # FastAPI routes (/summary, /cheapest)
        ├── mcp_tools.py      # MCP tool functions
        ├── models.py         # Dataclasses (RegionPriceRow, CheapestRegionRow, …)
        ├── providers.py      # DbPricingProvider / CoreApiPricingProvider
        ├── service.py        # Core business logic and caching
        └── static/
            ├── css/regions-cheapest.css
            ├── html/regions-cheapest.html
            ├── js/regions-cheapest.js
            └── data/
                ├── region_geography.json
                ├── region_locations.json
                └── world.geojson
```

## Development

```bash
# Install in editable mode with dev dependencies
uv sync

# Run quality checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

## License

[MIT](LICENSE.txt)

## Disclaimer

> **This tool is not affiliated with Microsoft.** All pricing information is indicative and not a guarantee. Prices are dynamic and may change at any time. Data is based on the [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).
