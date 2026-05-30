const REFRESH_MS = 30_000;

const COLORS = {
    accent: "#60a5fa",
    display: "#a78bfa",
    healthy: "#34d399",
    warn: "#fbbf24",
    danger: "#f87171",
    muted: "#9aa8bc",
    grid: "rgba(148, 163, 184, 0.15)",
};

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

const LOG_LEVEL_COLORS = {
    DEBUG: "#94a3b8",
    INFO: "#60a5fa",
    WARNING: "#fbbf24",
    ERROR: "#f87171",
    CRITICAL: "#ef4444",
};

const STATUS_LABELS = {
    fresh: "Fresh — display is serving live VBB data",
    stale: "Stale — display is serving a cached snapshot",
    degraded: "Degraded — display could not serve data",
    unknown: "Unknown — cannot determine current display status",
};

const chartInstances = {};
let allLogs = [];
let logHistogramMeta = null;
let activePatternTemplate = null;
let currentView = "logs"; // "logs" | "patterns"

function formatSecondsFromMs(value) {
    if (value == null) return "—";
    const seconds = value / 1000;
    return `${seconds.toFixed(2).replace(/\.?0+$/, "")} s`;
}

function toSecondsSeries(values) {
    if (!Array.isArray(values)) return [];
    return values.map((value) => (value == null ? null : value / 1000));
}

function formatCount(value) {
    if (value == null) return "—";
    return String(Math.round(value));
}

function formatPercent(value) {
    if (value == null) return "—";
    return `${value.toFixed(1)}%`;
}

function formatTimestamp(isoString) {
    if (!isoString) return "";
    return new Date(isoString).toLocaleString();
}

function formatWindowDescription(window) {
    if (!window) return "unknown window";
    if (window.description) return window.description;
    if (window.amount != null && window.unit) {
        const unit = window.amount === 1 ? window.unit.replace(/s$/, "") : window.unit;
        return `Last ${window.amount} ${unit}`;
    }
    return "unknown window";
}

function formatRollupDescription(summary) {
    const rollup = summary.window?.rollup_minutes ?? summary.charts?.bucket_minutes;
    if (rollup == null) return "?m buckets";
    return rollup === "auto" || rollup === 0 ? "auto rollup" : `${rollup}m buckets`;
}

function baseChartOptions(yLabel) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
            legend: {
                labels: { color: COLORS.muted, boxWidth: 12, font: { family: "Fira Mono, monospace", size: 11 } },
            },
        },
        scales: {
            x: {
                ticks: { color: COLORS.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
                grid: { color: COLORS.grid },
            },
            y: {
                beginAtZero: true,
                ticks: { color: COLORS.muted },
                grid: { color: COLORS.grid },
                title: yLabel ? { display: true, text: yLabel, color: COLORS.muted } : undefined,
            },
        },
    };
}

function destroyCharts() {
    for (const chart of Object.values(chartInstances)) {
        chart.destroy();
    }
    for (const key of Object.keys(chartInstances)) {
        delete chartInstances[key];
    }
}

function upsertChart(key, config) {
    const canvas = document.getElementById(key);
    if (!canvas || typeof Chart === "undefined") {
        return;
    }
    chartInstances[key]?.destroy();
    chartInstances[key] = new Chart(canvas, config);
}

function renderCharts(charts) {
    if (!charts || typeof Chart === "undefined") {
        return;
    }

    const labels = charts.labels;
    const latencyMs = charts.latency_p50_ms ?? {};
    const latencySeconds = {
        stations: toSecondsSeries(latencyMs.stations),
        display_data: toSecondsSeries(latencyMs.display_data),
        vbb_fetch: toSecondsSeries(latencyMs.vbb_fetch),
    };

    upsertChart("chartRequests", {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: "stations",
                    data: charts.requests_by_route.stations,
                    borderColor: COLORS.accent,
                    backgroundColor: "rgba(96, 165, 250, 0.15)",
                    fill: true,
                    tension: 0.25,
                },
                {
                    label: "display_data",
                    data: charts.requests_by_route.display_data,
                    borderColor: COLORS.display,
                    backgroundColor: "rgba(167, 139, 250, 0.15)",
                    fill: true,
                    tension: 0.25,
                },
            ],
        },
        options: baseChartOptions("requests"),
    });

    upsertChart("chartLatency", {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: "stations",
                    data: latencySeconds.stations,
                    borderColor: COLORS.accent,
                    tension: 0.25,
                    spanGaps: true,
                },
                {
                    label: "display_data",
                    data: latencySeconds.display_data,
                    borderColor: COLORS.display,
                    tension: 0.25,
                    spanGaps: true,
                },
                {
                    label: "vbb_fetch",
                    data: latencySeconds.vbb_fetch,
                    borderColor: COLORS.warn,
                    tension: 0.25,
                    spanGaps: true,
                },
            ],
        },
        options: baseChartOptions("s"),
    });

    upsertChart("chartVbbHealth", {
        type: "bar",
        data: {
            labels,
            datasets: [
                {
                    label: "VBB errors",
                    data: charts.vbb_errors,
                    backgroundColor: COLORS.danger,
                    yAxisID: "y",
                },
                {
                    label: "cache hit rate %",
                    data: charts.cache_hit_rate_pct,
                    type: "line",
                    borderColor: COLORS.healthy,
                    backgroundColor: "rgba(52, 211, 153, 0.12)",
                    yAxisID: "y1",
                    tension: 0.25,
                    spanGaps: true,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    labels: { color: COLORS.muted, boxWidth: 12, font: { family: "Fira Mono, monospace", size: 11 } },
                },
            },
            scales: {
                x: {
                    ticks: { color: COLORS.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
                    grid: { color: COLORS.grid },
                },
                y: {
                    type: "linear",
                    position: "left",
                    beginAtZero: true,
                    ticks: { color: COLORS.muted },
                    grid: { color: COLORS.grid },
                    title: { display: true, text: "errors", color: COLORS.muted },
                },
                y1: {
                    type: "linear",
                    position: "right",
                    min: 0,
                    max: 100,
                    ticks: { color: COLORS.muted },
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: "hit rate %", color: COLORS.muted },
                },
            },
        },
    });
}

function storeLogHistogramMeta(summary) {
    const labels = summary?.log_histogram?.labels;
    if (!labels?.length || !summary?.generated_at) {
        logHistogramMeta = null;
        return;
    }

    const generatedAt = new Date(summary.generated_at);
    const windowHours = summary.window?.hours ?? 1;
    const bucketMinutes = summary.window?.rollup_minutes ?? summary.charts?.bucket_minutes ?? 10;

    logHistogramMeta = {
        labels,
        windowStart: new Date(generatedAt.getTime() - windowHours * 3_600_000),
        bucketMinutes,
    };
}

function bucketIndex(timestamp, windowStart, bucketMinutes, bucketCount) {
    const elapsedMinutes = (timestamp - windowStart) / 60_000;
    if (elapsedMinutes < 0) {
        return null;
    }
    const index = Math.floor(elapsedMinutes / bucketMinutes);
    if (index >= bucketCount) {
        return null;
    }
    return index;
}

function buildLogHistogramCounts(logs) {
    if (!logHistogramMeta) {
        return null;
    }

    const { labels, windowStart, bucketMinutes } = logHistogramMeta;
    const bucketCount = labels.length;
    const visibleLevels = getSelectedLogLevels();
    const byLevel = Object.fromEntries(visibleLevels.map((level) => [level, Array(bucketCount).fill(0)]));

    for (const log of logs) {
        const level = log.level;
        if (!byLevel[level] || !log.timestamp) {
            continue;
        }
        const index = bucketIndex(new Date(log.timestamp), windowStart, bucketMinutes, bucketCount);
        if (index !== null) {
            byLevel[level][index] += 1;
        }
    }

    return { labels, byLevel, visibleLevels };
}

function renderLogHistogram(logs) {
    const histogram = buildLogHistogramCounts(logs);
    if (!histogram || typeof Chart === "undefined") {
        return;
    }

    upsertChart("chartLogLevels", {
        type: "bar",
        data: {
            labels: histogram.labels,
            datasets: histogram.visibleLevels.map((level) => ({
                label: level,
                data: histogram.byLevel[level],
                backgroundColor: LOG_LEVEL_COLORS[level],
                stack: "logs",
            })),
        },
        options: {
            ...baseChartOptions("logs"),
            scales: {
                ...baseChartOptions("logs").scales,
                x: { ...baseChartOptions("logs").scales.x, stacked: true },
                y: { ...baseChartOptions("logs").scales.y, stacked: true },
            },
        },
    });
}

function renderDisplayStatus(displayStatus) {
    const banner = document.getElementById("healthBanner");
    const label = document.getElementById("healthLabel");
    if (!banner || !label) {
        return;
    }

    const status = displayStatus?.status ?? "unknown";
    banner.dataset.health = status;
    label.textContent = displayStatus?.label ?? STATUS_LABELS[status] ?? STATUS_LABELS.unknown;
}

function updateLastUpdated(summary) {
    const updated = document.getElementById("lastUpdated");
    if (!summary?.generated_at) {
        updated.textContent = "";
        return;
    }
    updated.textContent = `Updated ${formatTimestamp(summary.generated_at)} · ${formatWindowDescription(summary.window)} · ${formatRollupDescription(summary)}`;
}

function renderDisplay(display) {
    const uptime = display.uptime ?? {};
    const fresh = `${formatPercent(uptime.fresh_pct)} (${formatCount(display.fresh)})`;
    const stale = `${formatPercent(uptime.stale_pct)} (${formatCount(display.fallback)})`;
    const downtime = `${formatPercent(uptime.degraded_pct)} (${formatCount(display.no_snapshot)})`;
    const unknown = `${formatPercent(uptime.unknown_pct)} (${formatCount(display.failed_responses)})`;

    document.getElementById("displayFresh").textContent = fresh;
    document.getElementById("displayFallback").textContent = stale;
    document.getElementById("displayNoSnapshot").textContent = downtime;
    document.getElementById("display502").textContent = unknown;
}

function renderVbb(vbb) {
    document.getElementById("vbbErrors").textContent = formatCount(vbb.errors);
    document.getElementById("vbbP50").textContent = formatSecondsFromMs(vbb.fetch_latency.p50_ms);
    document.getElementById("vbbP95").textContent = formatSecondsFromMs(vbb.fetch_latency.p95_ms);
    document.getElementById("vbbCacheHitRate").textContent = formatPercent(vbb.cache.hit_rate_pct);
}

function getSelectedLogLevels() {
    const level = document.getElementById("logLevelFilter").value;
    return level ? [level] : LOG_LEVELS;
}

function filterLogs(logs) {
    const levels = getSelectedLogLevels();
    const functionQuery = document.getElementById("logFunctionFilter").value.trim().toLowerCase();
    const contentQuery = document.getElementById("logContentFilter").value.trim().toLowerCase();

    return logs.filter((log) => {
        if (!levels.includes(log.level)) return false;

        if (functionQuery) {
            const fn = (log.function ?? "").toLowerCase();
            const logger = (log.logger_name ?? "").toLowerCase();
            if (!fn.includes(functionQuery) && !logger.includes(functionQuery)) return false;
        }

        if (contentQuery && !(log.message ?? "").toLowerCase().includes(contentQuery)) return false;

        if (activePatternTemplate && tokenizeMessage(log.message ?? "") !== activePatternTemplate) return false;

        return true;
    });
}

function levelBadgeClass(level) {
    return `level-badge level-${(level ?? "unknown").toLowerCase()}`;
}

// ── Python traceback formatter ────────────────────────────────────

const TRACEBACK_PATTERNS = {
    header:    /^Traceback \(most recent call last\):/,
    location:  /^\s+File "[^"]+", line \d+/,
    source:    /^    \S/,
    exception: /^\w[\w.]*(?:Error|Exception|Warning|KeyboardInterrupt|SystemExit|StopIteration)(\s*:|$)/,
};

function classifyTracebackLine(line) {
    if (TRACEBACK_PATTERNS.header.test(line))    return "tb-header";
    if (TRACEBACK_PATTERNS.location.test(line))  return "tb-location";
    if (TRACEBACK_PATTERNS.source.test(line))    return "tb-source";
    if (TRACEBACK_PATTERNS.exception.test(line)) return "tb-exception";
    return null;
}

function findExceptionLine(lines) {
    for (let i = lines.length - 1; i >= 0; i--) {
        if (lines[i].trim() && TRACEBACK_PATTERNS.exception.test(lines[i])) return lines[i].trim();
    }
    for (let i = lines.length - 1; i >= 0; i--) {
        if (lines[i].trim()) return lines[i].trim();
    }
    return "";
}

function buildTracebackPre(lines) {
    const pre = document.createElement("pre");
    pre.className = "log-traceback";
    const contentLines = lines[lines.length - 1] === "" ? lines.slice(0, -1) : lines;
    for (let i = 0; i < contentLines.length; i++) {
        const line = contentLines[i];
        const cls = classifyTracebackLine(line);
        if (cls) {
            const span = document.createElement("span");
            span.className = cls;
            span.textContent = line;
            pre.appendChild(span);
        } else {
            pre.appendChild(document.createTextNode(line));
        }
        if (i < contentLines.length - 1) pre.appendChild(document.createTextNode("\n"));
    }
    return pre;
}

function buildCollapsibleTraceback(lines) {
    const exceptionLine = findExceptionLine(lines);

    const wrapper = document.createElement("div");
    wrapper.className = "tb-wrapper tb-collapsed";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "tb-toggle";
    toggle.addEventListener("click", () => wrapper.classList.toggle("tb-collapsed"));

    const chevron = document.createElement("span");
    chevron.className = "tb-chevron";
    chevron.setAttribute("aria-hidden", "true");
    chevron.textContent = "›";

    const summary = document.createElement("span");
    summary.className = "tb-exception";
    summary.textContent = exceptionLine;

    toggle.append(chevron, summary);
    wrapper.append(toggle, buildTracebackPre(lines));
    return wrapper;
}

function renderMessage(message) {
    if (!message) return document.createTextNode("—");
    const lines = message.split("\n");
    if (lines.length === 1) return document.createTextNode(message);

    const isTraceback = lines.some(
        (l) => TRACEBACK_PATTERNS.header.test(l) || TRACEBACK_PATTERNS.location.test(l)
    );
    if (isTraceback) return buildCollapsibleTraceback(lines);

    const pre = document.createElement("pre");
    pre.className = "log-pre";
    pre.textContent = message;
    return pre;
}

// ── Log pattern grouping ──────────────────────────────────────────

function tokenizeMessage(message) {
    return message
        .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, "*")
        .replace(/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b/g, "*")
        .replace(/\b0x[0-9a-f]+\b/gi, "*")
        .replace(/\b[0-9a-f]{32,}\b/gi, "*")
        .replace(/"[^"\n]{0,200}"/g, '"*"')
        .replace(/'[^'\n]{0,200}'/g, "'*'")
        .replace(/\b\d+\.?\d*\b/g, "*")
        .replace(/\s+/g, " ")
        .trim();
}

function groupByPattern(logs) {
    const groups = new Map();
    for (const log of logs) {
        const template = tokenizeMessage(log.message ?? "");
        if (!groups.has(template)) groups.set(template, { template, count: 0 });
        groups.get(template).count++;
    }
    return [...groups.values()].sort((a, b) => b.count - a.count);
}

function refreshLogViews() {
    const filtered = filterLogs(allLogs);
    renderLogHistogram(filtered);
    if (currentView === "patterns") {
        renderPatterns(groupByPattern(filtered));
    } else {
        renderLogs(filtered);
    }
}

function renderLogs(filtered = filterLogs(allLogs)) {
    const tbody = document.getElementById("logsTableBody");
    const count = document.getElementById("logsCount");

    const patternSuffix = activePatternTemplate ? " · pattern filter active" : "";
    count.textContent = `Showing ${filtered.length} of ${allLogs.length} logs${patternSuffix}`;
    tbody.replaceChildren();

    if (!filtered.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 5;
        cell.className = "logs-empty";
        cell.textContent = allLogs.length ? "No logs match the current filters." : "No logs in this window.";
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }

    for (const log of filtered) {
        const row = document.createElement("tr");

        const timeCell = document.createElement("td");
        timeCell.className = "logs-time";
        timeCell.textContent = formatTimestamp(log.timestamp);

        const levelCell = document.createElement("td");
        const badge = document.createElement("span");
        badge.className = levelBadgeClass(log.level);
        badge.textContent = log.level ?? "—";
        levelCell.appendChild(badge);

        const functionCell = document.createElement("td");
        functionCell.className = "logs-function";
        functionCell.textContent = log.function ?? "—";

        const loggerCell = document.createElement("td");
        loggerCell.className = "logs-logger";
        loggerCell.textContent = log.logger_name ?? "—";

        const messageCell = document.createElement("td");
        messageCell.className = "logs-message";
        messageCell.appendChild(renderMessage(log.message ?? ""));

        row.append(timeCell, levelCell, functionCell, loggerCell, messageCell);
        tbody.appendChild(row);
    }
}

function renderPatterns(patternGroups) {
    const tbody = document.getElementById("patternsTableBody");
    tbody.replaceChildren();

    if (!patternGroups.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 2;
        cell.className = "logs-empty";
        cell.textContent = "No patterns found.";
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }

    for (const { template, count } of patternGroups) {
        const row = document.createElement("tr");
        row.className = "pattern-row";

        const countCell = document.createElement("td");
        const badge = document.createElement("span");
        badge.className = "pattern-count-badge";
        badge.textContent = count;
        countCell.appendChild(badge);

        const tmplCell = document.createElement("td");
        tmplCell.className = "pattern-template";
        tmplCell.textContent = template;

        row.append(countCell, tmplCell);
        row.addEventListener("click", () => {
            activePatternTemplate = template;
            switchView("logs");
        });
        tbody.appendChild(row);
    }
}

function switchView(view) {
    if (view === "patterns") activePatternTemplate = null;
    currentView = view;

    document.getElementById("logsSection").hidden = view !== "logs";
    document.getElementById("patternsSection").hidden = view !== "patterns";
    document.getElementById("logsViewBtn").classList.toggle("is-active", view === "logs");
    document.getElementById("patternsViewBtn").classList.toggle("is-active", view === "patterns");

    refreshLogViews();
}

function storeLogs(logs) {
    allLogs = Array.isArray(logs) ? logs : [];
    refreshLogViews();
}

function showFetchError(message) {
    let banner = document.querySelector(".error-banner");
    if (!banner) {
        banner = document.createElement("section");
        banner.className = "error-banner";
        document.querySelector(".dashboard").prepend(banner);
    }
    banner.textContent = message;
}

function clearFetchError() {
    document.querySelector(".error-banner")?.remove();
}

function buildSummaryUrl() {
    const amount = document.getElementById("windowAmount").value;
    const unit = document.getElementById("windowUnit").value;
    const rollup = document.getElementById("rollupWindow").value;
    const params = new URLSearchParams({ amount, unit, rollup });
    return `/api/observability/summary?${params.toString()}`;
}

async function loadSummary() {
    const response = await fetch(buildSummaryUrl());
    const summary = await response.json();

    if (!response.ok) {
        logHistogramMeta = null;
        destroyCharts();
        renderDisplayStatus({ status: "unknown", label: STATUS_LABELS.unknown });
        showFetchError(summary.error ?? "Failed to load observability summary.");
        updateLastUpdated(null);
        storeLogs([]);
        return;
    }

    clearFetchError();
    updateLastUpdated(summary);
    renderDisplayStatus(summary.display_status);
    renderCharts(summary.charts);
    storeLogHistogramMeta(summary);
    renderDisplay(summary.display);
    renderVbb(summary.vbb);
    storeLogs(summary.logs);
}

document.getElementById("refreshBtn").addEventListener("click", () => {
    loadSummary().catch((error) => showFetchError(error.message));
});

for (const id of ["windowAmount", "windowUnit", "rollupWindow"]) {
    document.getElementById(id).addEventListener("change", () => {
        loadSummary().catch((error) => showFetchError(error.message));
    });
}

document.getElementById("windowAmount").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        loadSummary().catch((error) => showFetchError(error.message));
    }
});

for (const id of ["logLevelFilter", "logFunctionFilter", "logContentFilter"]) {
    document.getElementById(id).addEventListener("input", refreshLogViews);
    document.getElementById(id).addEventListener("change", refreshLogViews);
}

document.getElementById("logsViewBtn").addEventListener("click", () => switchView("logs"));
document.getElementById("patternsViewBtn").addEventListener("click", () => switchView("patterns"));

window.addEventListener("beforeunload", destroyCharts);

function startDashboard() {
    loadSummary().catch((error) => showFetchError(error.message));
    setInterval(() => {
        loadSummary().catch((error) => showFetchError(error.message));
    }, REFRESH_MS);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startDashboard);
} else {
    startDashboard();
}
