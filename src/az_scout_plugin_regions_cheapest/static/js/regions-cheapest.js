// Regions Cheapest plugin – main JS
// Globals from app.js: apiFetch, tenantQS, subscriptions, regions
/* global apiFetch, tenantQS, d3 */
(function () {
    const PLUGIN = "regions-cheapest";
    const container = document.getElementById("plugin-tab-" + PLUGIN);
    if (!container) return;

    // State
    let data = null;          // last fetched summary rows
    let geoJson = null;       // world boundaries
    let regionLocs = null;    // region locations
    let sortCol = "avgPrice";
    let sortAsc = true;
    let mapMode = "choropleth"; // "choropleth" | "points"

    // -----------------------------------------------------------------------
    // 1. Load HTML fragment then initialise
    // -----------------------------------------------------------------------
    fetch(`/plugins/${PLUGIN}/static/html/regions-cheapest.html`)
        .then(r => r.text())
        .then(html => {
            container.innerHTML = html;
            init();
        })
        .catch(err => {
            container.innerHTML =
                `<div class="alert alert-danger">Failed to load plugin UI: ${err.message}</div>`;
        });

    // -----------------------------------------------------------------------
    // 2. Init
    // -----------------------------------------------------------------------
    function init() {
        // Preload static data
        Promise.all([
            fetch(`/plugins/${PLUGIN}/static/data/world.geojson`).then(r => r.json()),
            fetch(`/plugins/${PLUGIN}/static/data/region_locations.json`).then(r => r.json()),
        ]).then(([geo, locs]) => {
            geoJson = geo;
            regionLocs = locs;
        }).catch(() => { /* will degrade gracefully */ });

        // Bind controls
        document.getElementById("rc-refresh")?.addEventListener("click", () => loadData());
        document.getElementById("rc-currency")?.addEventListener("change", () => loadData());
        document.querySelectorAll('input[name="rc-group"]').forEach(el =>
            el.addEventListener("change", () => loadData())
        );
        document.querySelectorAll('input[name="rc-map-mode"]').forEach(el =>
            el.addEventListener("change", () => {
                mapMode = el.value;
                if (data) renderMap(data);
            })
        );

        // Table sorting
        document.querySelectorAll("#rc-table thead th[data-sort]")?.forEach(th =>
            th.addEventListener("click", () => {
                const col = th.dataset.sort;
                if (sortCol === col) { sortAsc = !sortAsc; }
                else { sortCol = col; sortAsc = col === "avgPrice"; }
                if (data) renderTable(data);
            })
        );

        // React to tenant changes
        const tenantEl = document.getElementById("tenant-select");
        if (tenantEl) tenantEl.addEventListener("change", () => loadData());

        // Initial load
        loadData();
    }

    // -----------------------------------------------------------------------
    // 3. Data loading
    // -----------------------------------------------------------------------
    function getParams() {
        const currency = document.getElementById("rc-currency")?.value || "USD";
        const groupBy = document.querySelector('input[name="rc-group"]:checked')?.value || "region";
        return { currency, groupBy };
    }

    async function loadData() {
        const loading = document.getElementById("rc-loading");
        const content = document.getElementById("rc-content");
        const errorEl = document.getElementById("rc-error");
        if (!loading || !content || !errorEl) return;

        loading.classList.remove("d-none");
        content.classList.add("d-none");
        errorEl.classList.add("d-none");

        const { currency, groupBy } = getParams();
        const tqs = tenantQS("&");
        const url = `/plugins/${PLUGIN}/summary?currency=${encodeURIComponent(currency)}&groupBy=${encodeURIComponent(groupBy)}${tqs}`;

        try {
            const resp = await apiFetch(url);
            data = resp;
            const skuEl = document.getElementById("rc-sku-count");
            if (skuEl && resp.rows && resp.rows.length) {
                const maxSkus = Math.max(...resp.rows.map(r => r.skuCount || 0));
                skuEl.textContent = maxSkus + " ";
            }

            loading.classList.add("d-none");
            content.classList.remove("d-none");

            renderMap(resp);
            renderChart(resp);
            renderTable(resp);
            renderDataSourceBadge(resp);

            const tsEl = document.getElementById("rc-timestamp");
            if (tsEl && resp.timestampUtc) {
                tsEl.textContent = "Data as of " + new Date(resp.timestampUtc).toLocaleString();
            }
        } catch (err) {
            loading.classList.add("d-none");
            errorEl.classList.remove("d-none");
            errorEl.textContent = "Failed to load data: " + err.message;
        }
    }

    // -----------------------------------------------------------------------
    // 4. Map rendering
    // -----------------------------------------------------------------------
    function renderMap(resp) {
        const mapEl = document.getElementById("rc-map");
        if (!mapEl) return;
        mapEl.innerHTML = "";

        if (!geoJson) {
            mapEl.innerHTML = '<p class="text-muted text-center py-4">Map data not available</p>';
            return;
        }

        const width = mapEl.clientWidth || 800;
        const height = mapEl.clientHeight || 420;

        const svg = d3.select(mapEl).append("svg")
            .attr("viewBox", `0 0 ${width} ${height}`)
            .attr("preserveAspectRatio", "xMidYMid meet");

        const projection = d3.geoNaturalEarth1()
            .fitSize([width - 20, height - 20], geoJson)
            .translate([width / 2, height / 2]);

        const path = d3.geoPath().projection(projection);

        // Tooltip
        let tooltip = d3.select(".rc-tooltip");
        if (tooltip.empty()) {
            tooltip = d3.select("body").append("div").attr("class", "rc-tooltip").style("display", "none");
        }

        const rows = resp.rows || [];
        const pricedRows = rows.filter(r => r.avgPrice != null);
        if (!pricedRows.length) {
            mapEl.innerHTML = '<p class="text-muted text-center py-4">No pricing data</p>';
            return;
        }

        const priceExtent = d3.extent(pricedRows, r => r.avgPrice);
        const colorScale = d3.scaleSequential(d3.interpolateRdYlGn)
            .domain([priceExtent[1], priceExtent[0]]); // reversed: green=cheap, red=expensive

        if (mapMode === "choropleth") {
            renderChoropleth(svg, path, rows, colorScale, tooltip, resp);
        } else {
            renderPoints(svg, path, rows, projection, colorScale, tooltip, resp);
        }
    }

    function renderChoropleth(svg, path, rows, colorScale, tooltip, resp) {
        // Aggregate by country code
        const countryData = {};
        rows.forEach(r => {
            const cc = r.countryCode || (regionLocs && regionLocs[r.regionId]?.countryCode) || "";
            if (!cc || r.avgPrice == null) return;
            if (!countryData[cc]) countryData[cc] = { prices: [], regions: [] };
            countryData[cc].prices.push(r.avgPrice);
            countryData[cc].regions.push(r);
        });
        // Mean price per country
        Object.keys(countryData).forEach(cc => {
            const prices = countryData[cc].prices;
            countryData[cc].avgPrice = prices.reduce((a, b) => a + b, 0) / prices.length;
        });

        // Map ISO-2 to ISO-3 (GeoJSON uses ISO A3)
        const iso2to3 = {};
        if (geoJson && geoJson.features) {
            geoJson.features.forEach(f => {
                const a2 = (f.properties.iso_a2 || "").toUpperCase();
                const a3 = f.properties.iso_a3 || f.properties.ISO_A3 || f.id || "";
                if (a2 && a2 !== "-99") iso2to3[a2] = a3;
            });
        }

        // Build A3 -> country data
        const a3Data = {};
        Object.entries(countryData).forEach(([cc, d]) => {
            const a3 = iso2to3[cc.toUpperCase()] || cc.toUpperCase();
            a3Data[a3] = d;
        });

        svg.selectAll("path.rc-country")
            .data(geoJson.features)
            .join("path")
            .attr("class", "rc-country")
            .attr("d", path)
            .attr("fill", d => {
                const a3 = d.properties.iso_a3 || d.properties.ISO_A3 || d.id || "";
                const cd = a3Data[a3];
                return cd ? colorScale(cd.avgPrice) : "var(--bs-tertiary-bg, #e9ecef)";
            })
            .on("mouseover", (event, d) => {
                const a3 = d.properties.iso_a3 || d.properties.ISO_A3 || d.id || "";
                const cd = a3Data[a3];
                const name = d.properties.name || d.properties.NAME || a3;
                if (cd) {
                    showTooltip(tooltip, event, name, cd.avgPrice,
                        cd.regions.length, resp.currency,
                        cd.regions.map(r => r.regionName).join(", "),
                        resp.timestampUtc);
                }
            })
            .on("mousemove", (event) => moveTooltip(tooltip, event))
            .on("mouseout", () => tooltip.style("display", "none"));
    }

    function renderPoints(svg, path, rows, projection, colorScale, tooltip, resp) {
        // Draw world boundaries as background
        svg.selectAll("path.rc-bg")
            .data(geoJson.features)
            .join("path")
            .attr("class", "rc-country")
            .attr("d", path)
            .attr("fill", "var(--bs-tertiary-bg, #e9ecef)");

        const locs = regionLocs || {};
        const pointData = rows.filter(r => {
            const loc = locs[r.regionId];
            return loc && loc.lat != null && loc.lon != null;
        });

        svg.selectAll("circle.rc-point")
            .data(pointData)
            .join("circle")
            .attr("class", "rc-point")
            .attr("cx", d => {
                const loc = locs[d.regionId];
                return projection([loc.lon, loc.lat])[0];
            })
            .attr("cy", d => {
                const loc = locs[d.regionId];
                return projection([loc.lon, loc.lat])[1];
            })
            .attr("r", 5)
            .attr("fill", d => d.avgPrice != null ? colorScale(d.avgPrice) : "#999")
            .attr("opacity", d => d.avgPrice != null ? 0.85 : 0.3)
            .on("mouseover", (event, d) => {
                showTooltip(tooltip, event, d.regionName, d.avgPrice,
                    1, resp.currency,
                    `${d.geography} | Avail: ${d.availabilityPct.toFixed(1)}% | SKUs: ${d.skuCount}`,
                    resp.timestampUtc);
            })
            .on("mousemove", (event) => moveTooltip(tooltip, event))
            .on("mouseout", () => tooltip.style("display", "none"));
    }

    function showTooltip(tooltip, event, title, price, count, currency, detail, timestamp) {
        const priceStr = price != null ? `${currency} ${price.toFixed(4)}` : "N/A";
        const ts = timestamp ? new Date(timestamp).toLocaleString() : "";
        tooltip.html(`
            <div class="rc-tt-title">${escapeHtml(title)}</div>
            <div class="rc-tt-row"><span class="rc-tt-label">Avg price/h:</span> <span>${priceStr}</span></div>
            <div class="rc-tt-row"><span class="rc-tt-label">Detail:</span> <span>${escapeHtml(detail)}</span></div>
            ${ts ? `<div class="rc-tt-row"><span class="rc-tt-label">Updated:</span> <span>${ts}</span></div>` : ""}
        `).style("display", "block");
        moveTooltip(tooltip, event);
    }

    function moveTooltip(tooltip, event) {
        tooltip
            .style("left", (event.pageX + 12) + "px")
            .style("top", (event.pageY - 10) + "px");
    }

    // -----------------------------------------------------------------------
    // 5. Bar chart
    // -----------------------------------------------------------------------
    function renderChart(resp) {
        const chartEl = document.getElementById("rc-chart");
        if (!chartEl) return;
        chartEl.innerHTML = "";

        const rows = (resp.rows || []).filter(r => r.avgPrice != null);
        rows.sort((a, b) => a.avgPrice - b.avgPrice);

        if (!rows.length) {
            chartEl.innerHTML = '<p class="text-muted text-center py-4">No pricing data</p>';
            return;
        }

        const margin = { top: 10, right: 30, bottom: 80, left: 60 };
        const width = (chartEl.clientWidth || 800) - margin.left - margin.right;
        const height = (chartEl.clientHeight || 300) - margin.top - margin.bottom;

        const svg = d3.select(chartEl).append("svg")
            .attr("viewBox", `0 0 ${width + margin.left + margin.right} ${height + margin.top + margin.bottom}`)
            .attr("preserveAspectRatio", "xMidYMid meet")
            .append("g")
            .attr("transform", `translate(${margin.left},${margin.top})`);

        const x = d3.scaleBand()
            .domain(rows.map(r => r.regionName))
            .range([0, width])
            .padding(0.15);

        const y = d3.scaleLinear()
            .domain([0, d3.max(rows, r => r.avgPrice) * 1.1])
            .range([height, 0]);

        const priceExtent = d3.extent(rows, r => r.avgPrice);
        const barColor = d3.scaleSequential(d3.interpolateRdYlGn)
            .domain([priceExtent[1], priceExtent[0]]);

        svg.append("g")
            .attr("class", "rc-axis")
            .attr("transform", `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll("text")
            .attr("transform", "rotate(-45)")
            .style("text-anchor", "end")
            .attr("dx", "-0.5em")
            .attr("dy", "0.15em");

        svg.append("g")
            .attr("class", "rc-axis")
            .call(d3.axisLeft(y).ticks(6).tickFormat(d => d.toFixed(3)));

        // Tooltip
        let tooltip = d3.select(".rc-tooltip");
        if (tooltip.empty()) {
            tooltip = d3.select("body").append("div").attr("class", "rc-tooltip").style("display", "none");
        }

        svg.selectAll("rect.rc-bar")
            .data(rows)
            .join("rect")
            .attr("class", "rc-bar")
            .attr("x", d => x(d.regionName))
            .attr("y", d => y(d.avgPrice))
            .attr("width", x.bandwidth())
            .attr("height", d => height - y(d.avgPrice))
            .attr("fill", d => barColor(d.avgPrice))
            .attr("rx", 2)
            .on("mouseover", (event, d) => {
                showTooltip(tooltip, event, d.regionName, d.avgPrice,
                    1, resp.currency,
                    `${d.geography} | Avail: ${d.availabilityPct.toFixed(1)}%`,
                    resp.timestampUtc);
            })
            .on("mousemove", (event) => moveTooltip(tooltip, event))
            .on("mouseout", () => tooltip.style("display", "none"));
    }

    // -----------------------------------------------------------------------
    // 6. Table
    // -----------------------------------------------------------------------
    function renderTable(resp) {
        const tbody = document.getElementById("rc-table-body");
        if (!tbody) return;

        const rows = [...(resp.rows || [])];
        // Sort
        rows.sort((a, b) => {
            let va = a[sortCol];
            let vb = b[sortCol];
            // Nulls last
            if (va == null && vb == null) return 0;
            if (va == null) return 1;
            if (vb == null) return -1;
            if (typeof va === "string") va = va.toLowerCase();
            if (typeof vb === "string") vb = vb.toLowerCase();
            if (va < vb) return sortAsc ? -1 : 1;
            if (va > vb) return sortAsc ? 1 : -1;
            return 0;
        });

        // Update sort indicators
        document.querySelectorAll("#rc-table thead th[data-sort]").forEach(th => {
            th.classList.toggle("rc-sort-active", th.dataset.sort === sortCol);
            const icon = th.querySelector(".bi");
            if (icon) {
                icon.className = th.dataset.sort === sortCol
                    ? (sortAsc ? "bi bi-chevron-up" : "bi bi-chevron-down")
                    : "bi bi-chevron-expand";
            }
        });

        const currency = resp.currency || "USD";
        tbody.innerHTML = rows.map(r => `<tr>
            <td>${escapeHtml(r.geography)}</td>
            <td>${escapeHtml(r.regionName)}</td>
            <td><code>${escapeHtml(r.regionId)}</code></td>
            <td class="text-end">${r.availabilityPct != null ? r.availabilityPct.toFixed(1) + "%" : '<span class="rc-na">N/A</span>'}</td>
            <td class="text-end">${r.avgPrice != null ? currency + " " + r.avgPrice.toFixed(4) : '<span class="rc-na">N/A</span>'}</td>
        </tr>`).join("");
    }

    // -----------------------------------------------------------------------
    // Data source badge
    // -----------------------------------------------------------------------
    function renderDataSourceBadge(resp) {
        const badge = document.getElementById("rc-data-source-badge");
        if (!badge) return;
        const src = resp.dataSource || "live";
        const coverage = resp.coveragePct != null ? resp.coveragePct : null;
        const labels = {
            db: "Source: DB cache",
            hybrid: "Source: Hybrid (DB + Live)",
            live: "Source: Live API",
        };
        const colors = {
            db: "bg-success",
            hybrid: "bg-warning text-dark",
            live: "bg-info text-dark",
        };
        let text = labels[src] || ("Source: " + src);
        if (coverage != null && src !== "live") {
            text += " (" + coverage.toFixed(0) + "% coverage)";
        }
        badge.textContent = text;
        badge.className = "badge ms-2 " + (colors[src] || "bg-secondary");
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------
    function escapeHtml(str) {
        if (str == null) return "";
        const div = document.createElement("div");
        div.appendChild(document.createTextNode(String(str)));
        return div.innerHTML;
    }
})();
