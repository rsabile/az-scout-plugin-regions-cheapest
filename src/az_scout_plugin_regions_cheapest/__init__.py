"""az-scout plugin: Regions Cheapest.

Provides a world-map choropleth/region-points view, bar chart, and table
ranking Azure regions by average VM hourly price.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from az_scout.plugin_api import TabDefinition
from fastapi import APIRouter

_STATIC_DIR = Path(__file__).parent / "static"

__version__ = "0.1.0"


class RegionsCheapestPlugin:
    """Regions Cheapest az-scout plugin."""

    name = "regions-cheapest"
    version = __version__

    def get_router(self) -> APIRouter | None:
        """Return API routes mounted at /plugins/regions-cheapest/."""
        from az_scout_plugin_regions_cheapest.routes import router

        return router

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        """Return MCP tool functions."""
        from az_scout_plugin_regions_cheapest.mcp_tools import (
            cheapest_regions,
            regions_price_summary,
        )

        return [regions_price_summary, cheapest_regions]

    def get_static_dir(self) -> Path | None:
        """Return path to static assets directory."""
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
        """Return UI tab definitions."""
        return [
            TabDefinition(
                id="regions-cheapest",
                label="Regions Cheapest",
                icon="bi bi-graph-down-arrow",
                js_entry="js/regions-cheapest.js",
                css_entry="css/regions-cheapest.css",
            )
        ]

    def get_chat_modes(self) -> list[Any] | None:
        """No chat modes for this plugin."""
        return None


# Module-level instance — referenced by the entry point
plugin = RegionsCheapestPlugin()
