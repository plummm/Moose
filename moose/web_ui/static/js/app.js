/**
 * Moose Dashboard - Main Application JavaScript
 *
 * Features:
 * - Chart.js integration for better visualizations
 * - SSE pause on tab visibility change
 * - Log level filtering
 * - Virtual scrolling for large lists
 * - Skeleton loaders
 * - Auto-scroll toggle
 */

// ============================================================================
// State Management
// ============================================================================

const state = {
    // Project state
    currentProject: null,

    // SSE connections
    logEventSource: null,
    chatEventSource: null,
    ssesPaused: false,

    // URL-driven view state
    initialProjectFromUrl: null,
    initialTabFromUrl: null,
    initialCostAgentFromUrl: null,
    initialTraceRequestIdFromUrl: null,
    initialTraceIngressOnlyFromUrl: null,
    initialUrlApplied: false,

    // Costs agent-detail view
    costAgent: null,
    agentPageLimit: 20,
    agentPageOffset: 0,
    agentHasMore: false,
    agentExpanded: {},
    agentSortKey: 'first_ts',
    agentSortDir: 'desc',
    agentCostData: null,

    // Buffers (limited size for memory efficiency)
    streamedLogs: [],
    streamedMessages: [],
    maxChatBufferSize: 200,  // Maximum messages to keep in memory
    maxLogBufferSize: 2000,  // Maximum log entries to keep in memory

    // Chat pagination state
    chatHasMore: false,
    chatOldestTimestamp: null,
    chatNewestTimestamp: null,
    chatTotalAvailable: 0,
    chatLoadingOlder: false,

    // View state
    logViewMode: 'live',
    chatViewMode: 'stopped',  // Start as 'stopped' - user must click "Start Chat"
    rightTab: 'overview',
    hideToolMessages: false,
    pendingSystemMessage: null,

    // Log level filters
    logLevelFilters: {
        DEBUG: false,
        INFO: true,
        WARNING: true,
        ERROR: true,
        CRITICAL: false
    },

    // Auto-scroll
    logAutoScroll: true,
    chatAutoScroll: true,

    // Log pagination/windowing (render only recent N unless user loads older)
    logDisplayStep: 100,
    logDisplayCount: 100,
    logLoadingOlder: false,

    // Trace state
    selectedTraceId: null,
    selectedLlmSpanId: null,
    traceAdvanced: false,
    traceChatLastCount: 0,
    traceChatPollInterval: null,

    // Chart state
    chartTimeRange: '7d',
    chartInstances: {},
    costData: null
};

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    readUrlParams();
    initChatCollapse();
    loadProjects();
    initResizeHandle();
    initVerticalSplitters();
    initLogWindowing();
    initVisibilityHandler();
    initLogLevelFilters();
    initAgentCostSorting();
});

function _lsGet(key, fallbackValue) {
    try {
        const v = window.localStorage ? window.localStorage.getItem(key) : null;
        return (v === null || v === undefined) ? fallbackValue : v;
    } catch (e) {
        return fallbackValue;
    }
}

function _lsSet(key, value) {
    try {
        if (!window.localStorage) return;
        window.localStorage.setItem(key, String(value));
    } catch (e) {}
}

function initChatCollapse() {
    // Default: collapsed (saves space). User can expand via chevron.
    const raw = String(_lsGet('moose_chat_collapsed', '1')).toLowerCase().trim();
    state.chatCollapsed = !(raw === '0' || raw === 'false' || raw === 'no');
    applyChatCollapsed();
}

function applyChatCollapsed() {
    const pageLayout = document.querySelector('.page-layout');
    if (!pageLayout) return;
    const btn = document.getElementById('chat-collapse-btn');
    const label = document.getElementById('chat-collapsed-label');
    if (state.chatCollapsed) {
        pageLayout.classList.add('chat-collapsed');
        if (btn) {
            btn.textContent = '▶';
            btn.title = 'Expand chat';
        }
        if (label) label.style.display = '';
    } else {
        pageLayout.classList.remove('chat-collapsed');
        if (btn) {
            btn.textContent = '◀';
            btn.title = 'Collapse chat';
        }
        if (label) label.style.display = 'none';
    }
}

function toggleChatCollapse() {
    state.chatCollapsed = !state.chatCollapsed;
    _lsSet('moose_chat_collapsed', state.chatCollapsed ? '1' : '0');
    applyChatCollapsed();
}

function readUrlParams() {
    try {
        const params = new URLSearchParams(window.location.search || '');
        const p = (params.get('project') || '').trim();
        const tab = (params.get('tab') || '').trim().toLowerCase();
        const agent = (params.get('agent') || '').trim();
        const rid = (params.get('request_id') || '').trim();
        const ingressOnlyRaw = (params.get('ingress_only') || '').trim().toLowerCase();

        if (p) state.initialProjectFromUrl = p;
        if (tab === 'costs' || tab === 'traces' || tab === 'overview') state.initialTabFromUrl = tab;
        if (agent) state.initialCostAgentFromUrl = agent;
        if (rid) state.initialTraceRequestIdFromUrl = rid;
        if (ingressOnlyRaw) {
            state.initialTraceIngressOnlyFromUrl = !(ingressOnlyRaw === '0' || ingressOnlyRaw === 'false' || ingressOnlyRaw === 'no');
        }
        if (state.initialCostAgentFromUrl) state.initialTabFromUrl = 'costs';
        if (state.initialTraceRequestIdFromUrl) state.initialTabFromUrl = 'traces';
    } catch (e) {
        console.error('Error reading URL params:', e);
    }
}

// ============================================================================
// Visibility API - Pause SSE when tab hidden
// ============================================================================

function initVisibilityHandler() {
    document.addEventListener('visibilitychange', handleVisibilityChange);
}

function handleVisibilityChange() {
    if (document.hidden) {
        pauseSSEConnections();
    } else {
        resumeSSEConnections();
    }
    updateConnectionStatus();
}

function pauseSSEConnections() {
    state.ssesPaused = true;

    if (state.logEventSource) {
        state.logEventSource.close();
        state.logEventSource = null;
    }

    if (state.chatEventSource) {
        state.chatEventSource.close();
        state.chatEventSource = null;
    }

    console.log('SSE connections paused (tab hidden)');
}

function resumeSSEConnections() {
    state.ssesPaused = false;

    if (state.currentProject) {
        if (state.logViewMode === 'live') {
            connectLogStream();
        }
        if (state.chatViewMode === 'live') {
            // Reconnect with 'since' parameter to avoid duplicate messages
            connectChatStream(state.chatNewestTimestamp);
        }
    }

    console.log('SSE connections resumed (tab visible)');
}

function updateConnectionStatus() {
    const statusEl = document.getElementById('connection-status');
    if (!statusEl) return;

    if (state.ssesPaused) {
        statusEl.className = 'connection-status paused';
        statusEl.innerHTML = '<span class="dot"></span>Paused';
    } else if (state.logEventSource || state.chatEventSource) {
        statusEl.className = 'connection-status connected';
        statusEl.innerHTML = '<span class="dot"></span>Connected';
    } else {
        statusEl.className = 'connection-status disconnected';
        statusEl.innerHTML = '<span class="dot"></span>Disconnected';
    }
}

// ============================================================================
// Log Level Filtering
// ============================================================================

function initLogLevelFilters() {
    const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
    levels.forEach(level => {
        const checkbox = document.getElementById(`log-filter-${level.toLowerCase()}`);
        if (checkbox) {
            checkbox.checked = state.logLevelFilters[level];
            checkbox.addEventListener('change', () => {
                state.logLevelFilters[level] = checkbox.checked;
                // When filters change, reset to recent window.
                state.logDisplayCount = state.logDisplayStep;
                reRenderLogs();
            });
        }
    });
}

function initLogWindowing() {
    const container = document.getElementById('log-container');
    if (!container) return;
    container.addEventListener('scroll', () => {
        if (container.scrollTop <= 0) {
            loadOlderLogs();
        }
    });
}

function shouldShowLogEntry(entry) {
    const level = (entry.level || 'INFO').toUpperCase();
    return state.logLevelFilters[level] !== false;
}

function reRenderLogs() {
    if (state.logViewMode === 'live') {
        const container = document.getElementById('log-entries');
        if (!container) return;
        renderLogWindow(container, state.streamedLogs);
    }
}

function loadOlderLogs() {
    if (state.logLoadingOlder) return;
    const logContainer = document.getElementById('log-container');
    const entriesEl = document.getElementById('log-entries');
    if (!logContainer || !entriesEl) return;

    const filtered = state.streamedLogs.filter(shouldShowLogEntry);
    if (filtered.length <= state.logDisplayCount) return;

    state.logLoadingOlder = true;

    const prevScrollHeight = logContainer.scrollHeight;
    const prevScrollTop = logContainer.scrollTop;

    state.logDisplayCount = Math.min(filtered.length, state.logDisplayCount + state.logDisplayStep);
    renderLogWindow(entriesEl, state.streamedLogs);

    // Maintain viewport position (avoid jump)
    const newScrollHeight = logContainer.scrollHeight;
    logContainer.scrollTop = prevScrollTop + (newScrollHeight - prevScrollHeight);

    state.logLoadingOlder = false;
}

function renderLogWindow(entriesEl, allLogs) {
    const logContainer = document.getElementById('log-container');
    if (!entriesEl) return;

    const wasAtBottom = !!(logContainer && (logContainer.scrollTop + logContainer.clientHeight >= logContainer.scrollHeight - 4));

    const filtered = (allLogs || []).filter(shouldShowLogEntry);
    const total = filtered.length;
    const count = Math.max(state.logDisplayStep, state.logDisplayCount || state.logDisplayStep);
    const hasMore = total > count;
    const slice = hasMore ? filtered.slice(total - count) : filtered;

    entriesEl.innerHTML = '';

    if (hasMore) {
        const btn = document.createElement('button');
        btn.id = 'load-older-logs-btn';
        btn.className = 'load-older-btn';
        btn.textContent = 'Load older logs...';
        btn.onclick = () => loadOlderLogs();
        entriesEl.appendChild(btn);
    }

    for (const entry of slice) {
        const div = createLogEntryElement(entry);
        entriesEl.appendChild(div);
    }

    if (logContainer && (state.logAutoScroll || wasAtBottom)) {
        scrollToBottom('log-container');
    }
}

// ============================================================================
// Chart Time Range Controls
// ============================================================================

function onChartTimeRangeChange() {
    const select = document.getElementById('chart-time-range');
    if (select) {
        state.chartTimeRange = select.value;
        renderCostCharts();
    }
}

function filterDataByTimeRange(perDay, timeRange) {
    if (!perDay || !perDay.length) return [];

    const now = new Date();
    let cutoffDate;

    switch (timeRange) {
        case '24h':
            cutoffDate = new Date(now.getTime() - 24 * 60 * 60 * 1000);
            break;
        case '7d':
            cutoffDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
            break;
        case '30d':
            cutoffDate = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
            break;
        case 'all':
        default:
            return perDay;
    }

    const cutoffStr = cutoffDate.toISOString().split('T')[0];
    return perDay.filter(d => d.date >= cutoffStr);
}

// ============================================================================
// Chart.js Integration
// ============================================================================

function renderCostCharts() {
    if (!state.costData) return;

    let filteredData = filterDataByTimeRange(state.costData.per_day, state.chartTimeRange);
    // Ensure charts keep a stable x-axis window (avoids single-bar centering).
    filteredData = fillMissingDays(filteredData, state.chartTimeRange);

    renderChart('chart-cost-canvas', filteredData, 'cost', 'Daily Cost ($)');
    renderChart('chart-tokens-canvas', filteredData, 'tokens', 'Daily Tokens');
}

function renderChart(canvasId, perDay, kind, title) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
        // Fallback to DOM-based chart
        const containerId = kind === 'cost' ? 'chart-cost' : 'chart-tokens';
        const legendId = kind === 'cost' ? 'chart-legend-cost' : 'chart-legend-tokens';
        renderStackedChart(containerId, legendId, perDay, kind);
        return;
    }

    // Destroy existing chart
    if (state.chartInstances[canvasId]) {
        state.chartInstances[canvasId].destroy();
    }

    // Collect agents
    const agentSet = new Set();
    for (const d of perDay) {
        const by = (d && d.by_agent) ? d.by_agent : {};
        for (const a of Object.keys(by)) agentSet.add(a);
    }
    const agents = Array.from(agentSet).sort();

    // If cost data includes provider breakdown, render one bar per agent stacked by provider.
    const providers = collectProviders(perDay);
    const providerMode = (kind === 'cost') && (providers.length > 0);

    // Prepare datasets
    const datasets = providerMode
        ? buildProviderStackDatasets(perDay, agents, providers)
        : agents.map(agent => {
            const color = agentColor(agent);
            return {
                label: agent,
                data: perDay.map(d => {
                    const by = (d && d.by_agent) ? d.by_agent : {};
                    const rec = by[agent] || {};
                    if (kind === 'cost') {
                        return Number(rec.cost || 0);
                    } else {
                        return Number((rec.tokens || {}).total || 0);
                    }
                }),
                backgroundColor: color,
                borderColor: color,
                borderWidth: 1
            };
        });

    const labels = perDay.map(d => d.date || 'unknown');

    // Create chart
    const ctx = canvas.getContext('2d');
    state.chartInstances[canvasId] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 0 },
            interaction: { mode: 'nearest', intersect: true },
            datasets: {
                bar: {
                    maxBarThickness: 80,
                    categoryPercentage: 0.8,
                    barPercentage: 0.9
                }
            },
            plugins: {
                title: {
                    display: true,
                    text: title,
                    color: '#ffffff',
                    font: { size: 14, weight: 'bold' }
                },
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: '#ffffff',
                        font: { size: 11 },
                        boxWidth: 12,
                        padding: 10
                    },
                    // In provider mode, show only one legend item per provider (toggle all stacks).
                    ...(providerMode ? {
                        labels: {
                            generateLabels: function(chart) {
                                const out = [];
                                for (const p of providers) {
                                    out.push({
                                        text: p,
                                        fillStyle: providerColor(p),
                                        strokeStyle: providerColor(p),
                                        lineWidth: 1,
                                        hidden: false,
                                        datasetIndex: -1
                                    });
                                }
                                return out;
                            }
                        },
                        onClick: function(e, legendItem, legend) {
                            const p = legendItem && legendItem.text ? String(legendItem.text) : '';
                            if (!p) return;
                            const chart = legend.chart;
                            const metas = chart.getSortedVisibleDatasetMetas();
                            // Toggle visibility for datasets that match this provider.
                            let anyVisible = false;
                            for (const meta of metas) {
                                const ds = chart.data.datasets[meta.index];
                                if (ds && ds.__provider === p && meta.visible) {
                                    anyVisible = true;
                                    break;
                                }
                            }
                            for (const meta of metas) {
                                const ds = chart.data.datasets[meta.index];
                                if (ds && ds.__provider === p) {
                                    chart.setDatasetVisibility(meta.index, !anyVisible);
                                }
                            }
                            chart.update();
                        }
                    } : {})
                },
                tooltip: {
                    backgroundColor: '#16213e',
                    titleColor: '#ffffff',
                    bodyColor: '#ffffff',
                    borderColor: '#2a2a4a',
                    borderWidth: 1,
                    callbacks: {
                        label: function(context) {
                            const value = context.parsed.y;
                            if (kind === 'cost') {
                                return `${context.dataset.label}: $${fmtCost(value, 4)}`;
                            } else {
                                return `${context.dataset.label}: ${Math.round(value).toLocaleString()} tokens`;
                            }
                        }
                    }
                }
            },
            scales: {
                x: {
                    stacked: true,
                    grid: {
                        color: '#2a2a4a'
                    },
                    ticks: {
                        color: '#ffffff',
                        font: { size: 10 }
                    }
                },
                y: {
                    stacked: true,
                    grid: {
                        color: '#2a2a4a'
                    },
                    ticks: {
                        color: '#ffffff',
                        font: { size: 10 },
                        callback: function(value) {
                            if (kind === 'cost') {
                                return '$' + fmtCost(value, 2);
                            } else {
                                return value >= 1000 ? (value / 1000).toFixed(1) + 'k' : value;
                            }
                        }
                    }
                }
            }
        }
    });
}

function agentColor(agent) {
    let h = 0;
    for (let i = 0; i < (agent || '').length; i++) {
        h = (h * 31 + agent.charCodeAt(i)) >>> 0;
    }
    const hue = h % 360;
    return `hsl(${hue}, 70%, 55%)`;
}

function collectProviders(perDay) {
    const set = new Set();
    for (const d of (perDay || [])) {
        const by = (d && d.by_agent) ? d.by_agent : {};
        for (const a of Object.keys(by || {})) {
            const rec = by[a] || {};
            const bp = rec && rec.by_provider ? rec.by_provider : null;
            if (bp && typeof bp === 'object') {
                for (const p of Object.keys(bp)) set.add(String(p));
            }
        }
    }
    return Array.from(set).filter(Boolean).sort();
}

function providerColor(provider) {
    const p = String(provider || '').toLowerCase();
    if (p === 'openai') return 'rgba(6, 182, 212, 0.85)';     // cyan
    if (p === 'gemini') return 'rgba(74, 144, 217, 0.85)';    // blue
    if (p === 'anthropic') return 'rgba(156, 39, 176, 0.85)'; // purple
    if (p === 'xai') return 'rgba(255, 152, 0, 0.85)';        // orange
    // Stable hash fallback
    let h = 0;
    for (let i = 0; i < p.length; i++) h = (h * 31 + p.charCodeAt(i)) >>> 0;
    const hue = h % 360;
    return `hsla(${hue}, 70%, 55%, 0.85)`;
}

function buildProviderStackDatasets(perDay, agents, providers) {
    const out = [];
    for (const agent of (agents || [])) {
        for (const provider of (providers || [])) {
            const color = providerColor(provider);
            const data = (perDay || []).map(d => {
                const by = (d && d.by_agent) ? d.by_agent : {};
                const rec = by[agent] || {};
                const bp = (rec && rec.by_provider && typeof rec.by_provider === 'object') ? rec.by_provider : {};
                const prow = bp[provider] || {};
                return Number(prow.cost || 0);
            });
            // Skip datasets that are all zeros to keep hover/tooltip fast.
            const hasAny = data.some(v => Number(v || 0) !== 0);
            if (!hasAny) continue;

            out.push({
                label: `${agent} • ${provider}`,
                stack: agent, // separate bar per agent per day
                __provider: provider,
                data,
                backgroundColor: color,
                borderColor: color,
                borderWidth: 1
            });
        }
    }
    return out;
}

function fillMissingDays(perDay, timeRange) {
    const arr = Array.isArray(perDay) ? perDay : [];
    if (!arr.length) return arr;

    // Only pad fixed windows to avoid huge "all time" expansions.
    const now = new Date();
    let days = 0;
    if (timeRange === '7d') days = 7;
    else if (timeRange === '30d') days = 30;
    else if (timeRange === '24h') days = 2; // show today + yesterday for better context
    else return arr;

    const map = new Map();
    for (const d of arr) {
        if (d && d.date) map.set(String(d.date), d);
    }

    const out = [];
    for (let i = days - 1; i >= 0; i--) {
        const dt = new Date(now.getTime() - i * 24 * 60 * 60 * 1000);
        const ds = dt.toISOString().split('T')[0];
        out.push(map.get(ds) || { date: ds, by_agent: {} });
    }
    return out;
}

// Fallback DOM-based stacked chart
function renderStackedChart(containerId, legendId, perDay, kind) {
    const container = document.getElementById(containerId);
    const legend = document.getElementById(legendId);
    if (!container || !legend) return;

    const agentSet = new Set();
    for (const d of perDay) {
        const by = (d && d.by_agent) ? d.by_agent : {};
        for (const a of Object.keys(by)) agentSet.add(a);
    }
    const agents = Array.from(agentSet).sort();

    legend.innerHTML = '';
    for (const a of agents) {
        const item = document.createElement('div');
        item.className = 'legend-item';
        const sw = document.createElement('div');
        sw.className = 'legend-swatch';
        sw.style.background = agentColor(a);
        item.appendChild(sw);
        const label = document.createElement('span');
        label.textContent = a;
        item.appendChild(label);
        legend.appendChild(item);
    }

    let maxTotal = 0;
    const dayTotals = [];
    for (const d of perDay) {
        const by = (d && d.by_agent) ? d.by_agent : {};
        let total = 0;
        for (const a of Object.keys(by)) {
            const rec = by[a] || {};
            if (kind === 'cost') total += Number(rec.cost || 0);
            else total += Number((rec.tokens || {}).total || 0);
        }
        dayTotals.push(total);
        if (total > maxTotal) maxTotal = total;
    }
    if (maxTotal <= 0) maxTotal = 1;

    container.innerHTML = '';
    perDay.forEach((d, idx) => {
        const date = d.date || 'unknown';
        const by = (d && d.by_agent) ? d.by_agent : {};
        const total = dayTotals[idx] || 0;

        const row = document.createElement('div');
        row.className = 'chart-row';

        const dateEl = document.createElement('div');
        dateEl.className = 'chart-date';
        dateEl.textContent = date;

        const bar = document.createElement('div');
        bar.className = 'chart-bar';

        for (const a of agents) {
            const rec = by[a] || null;
            const val = rec ? (kind === 'cost' ? Number(rec.cost || 0) : Number((rec.tokens || {}).total || 0)) : 0;
            if (val <= 0) continue;
            const seg = document.createElement('div');
            seg.className = 'chart-seg';
            seg.style.background = agentColor(a);
            seg.style.width = `${(val / maxTotal) * 100}%`;
            bar.appendChild(seg);
        }

        const valueEl = document.createElement('div');
        valueEl.className = 'chart-value';
        valueEl.textContent = kind === 'cost' ? `$${fmtCost(total, 3)}` : `${fmtInt(Math.round(total))}`;

        row.appendChild(dateEl);
        row.appendChild(bar);
        row.appendChild(valueEl);
        container.appendChild(row);
    });
}

// ============================================================================
// Skeleton Loaders
// ============================================================================

function showSkeleton(containerId, type = 'text', count = 3) {
    const container = document.getElementById(containerId);
    if (!container) return;

    let html = '';
    for (let i = 0; i < count; i++) {
        switch (type) {
            case 'text':
                const widthClass = ['short', 'medium', 'full'][i % 3];
                html += `<div class="skeleton skeleton-text ${widthClass}"></div>`;
                break;
            case 'card':
                html += `<div class="skeleton skeleton-card"></div>`;
                break;
            case 'chart':
                html += `<div class="skeleton skeleton-chart"></div>`;
                break;
        }
    }
    container.innerHTML = html;
}

function hideSkeleton(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        const skeletons = container.querySelectorAll('.skeleton');
        skeletons.forEach(s => s.remove());
    }
}

// ============================================================================
// Auto-scroll Toggle
// ============================================================================

function toggleLogAutoScroll() {
    state.logAutoScroll = !state.logAutoScroll;
    const btn = document.getElementById('log-auto-scroll-btn');
    if (btn) {
        btn.classList.toggle('active', state.logAutoScroll);
        btn.textContent = state.logAutoScroll ? 'Auto-scroll: ON' : 'Auto-scroll: OFF';
    }
}

function toggleChatAutoScroll() {
    state.chatAutoScroll = !state.chatAutoScroll;
    const btn = document.getElementById('chat-auto-scroll-btn');
    if (btn) {
        btn.classList.toggle('active', state.chatAutoScroll);
        btn.textContent = state.chatAutoScroll ? 'Auto-scroll: ON' : 'Auto-scroll: OFF';
    }
}

// ============================================================================
// Projects
// ============================================================================

async function loadProjects(preserveSelection = true) {
    try {
        const response = await fetch('/api/projects');
        const projects = await response.json();

        const dropdown = document.getElementById('project-dropdown');
        const prev = dropdown ? dropdown.value : '';
        dropdown.innerHTML = '';

        if (projects.length === 0) {
            dropdown.innerHTML = '<option value="">No projects</option>';
            return;
        }

        projects.forEach((project) => {
            const option = document.createElement('option');
            option.value = project;
            option.textContent = project;
            dropdown.appendChild(option);
        });

        if (state.initialProjectFromUrl && projects.includes(state.initialProjectFromUrl)) {
            state.currentProject = state.initialProjectFromUrl;
            state.initialProjectFromUrl = null;
        } else {
            let desired = (preserveSelection ? (prev || state.currentProject || '') : '') || '';
            if (desired && projects.includes(desired)) {
                state.currentProject = desired;
            } else {
                state.currentProject = projects[0];
            }
        }
        dropdown.value = state.currentProject;
        loadProjectData();
    } catch (error) {
        console.error('Failed to load projects:', error);
        document.getElementById('project-dropdown').innerHTML = '<option value="">Error loading projects</option>';
    }
}

async function refreshProjects(showToast) {
    await loadProjects(true);
}

function onProjectChange() {
    const dropdown = document.getElementById('project-dropdown');
    state.currentProject = dropdown.value;

    state.streamedLogs = [];
    state.streamedMessages = [];

    if (state.traceChatPollInterval) {
        clearInterval(state.traceChatPollInterval);
        state.traceChatPollInterval = null;
    }
    state.selectedTraceId = null;
    state.traceChatLastCount = 0;

    state.logViewMode = 'live';
    // Don't set chatViewMode to 'live' here - let resetChatOverlay() handle it
    // This prevents auto-loading chat when switching projects
    document.getElementById('log-file-dropdown').value = 'live';
    document.getElementById('chat-file-dropdown').value = 'live';

    loadProjectData();
}

function loadProjectData() {
    if (!state.currentProject) return;

    loadAgents();
    loadLogFiles();
    loadChatFiles();
    connectLogStream();
    // Don't auto-start chat - let user click "Start Chat" button
    // This prevents browser crashes from loading large chat histories
    resetChatOverlay();
    loadCostSummary();

    if (state.costAgent) {
        loadAgentCostBreakdown();
    }
    if (state.rightTab === 'traces') {
        loadTracesDropdown();
    }

    if (!state.initialUrlApplied) {
        state.initialUrlApplied = true;
        if (state.initialTabFromUrl) {
            switchRightTab(state.initialTabFromUrl);
        }
        if (state.initialCostAgentFromUrl) {
            enterAgentCostView(state.initialCostAgentFromUrl);
        } else {
            exitAgentCostView();
        }
        if (state.initialTraceRequestIdFromUrl) {
            window.__desiredTraceId = state.initialTraceRequestIdFromUrl;
            if (state.initialTraceIngressOnlyFromUrl !== null) {
                window.__desiredIngressOnly = state.initialTraceIngressOnlyFromUrl;
            }
        }
    }

    updateConnectionStatus();
}

// ============================================================================
// Tabs
// ============================================================================

function switchRightTab(tabName) {
    state.rightTab = tabName;
    const tabs = ['overview', 'costs', 'traces'];
    const pageLayout = document.querySelector('.page-layout');

    tabs.forEach(tab => {
        const btn = document.getElementById(`tab-btn-${tab}`);
        const content = document.getElementById(`tab-content-${tab}`);
        if (tab === tabName) {
            btn?.classList.add('active');
            content?.classList.add('active');
        } else {
            btn?.classList.remove('active');
            content?.classList.remove('active');
        }
    });

    if (tabName === 'traces') {
        pageLayout?.classList.add('traces-full');
        const sinceEl = document.getElementById('trace-since');
        const untilEl = document.getElementById('trace-until');
        const t = todayIso();
        if (sinceEl && !sinceEl.value) sinceEl.value = t;
        if (untilEl && !untilEl.value) untilEl.value = t;
        // Ensure trace layout matches current Advanced toggle on first load.
        onTraceAdvancedToggle();
        loadTracesDropdown();
    } else {
        pageLayout?.classList.remove('traces-full');
        if (state.traceChatPollInterval) {
            clearInterval(state.traceChatPollInterval);
            state.traceChatPollInterval = null;
        }
    }

    if (tabName === 'costs') {
        loadCostSummary();
    }
}

// ============================================================================
// Resize Handle
// ============================================================================

function initResizeHandle() {
    const resizeHandle = document.getElementById('resize-handle');
    const chatPanel = document.getElementById('chat-panel');
    if (!resizeHandle || !chatPanel) return;

    let isResizing = false;
    let startX = 0;
    let startWidth = 0;

    resizeHandle.addEventListener('mousedown', function(e) {
        isResizing = true;
        startX = e.clientX;
        startWidth = chatPanel.offsetWidth;
        resizeHandle.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
        if (!isResizing) return;
        const diff = e.clientX - startX;
        const newWidth = Math.max(300, Math.min(800, startWidth + diff));
        chatPanel.style.width = newWidth + 'px';
        document.documentElement.style.setProperty('--chat-panel-width', newWidth + 'px');
    });

    document.addEventListener('mouseup', function() {
        if (isResizing) {
            isResizing = false;
            resizeHandle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}

// ============================================================================
// Vertical Splitters (Overview / Costs / Traces)
// ============================================================================

function initVerticalSplitters() {
    initVerticalSplitter({
        containerId: 'overview-vsplit',
        topId: 'overview-top',
        bottomId: 'overview-bottom',
        handleId: 'overview-splitter',
        storageKey: 'moose.vsplit.overview.topPx',
        minTop: 140,
        minBottom: 180
    });

    initVerticalSplitter({
        containerId: 'costs-vsplit',
        topId: 'costs-top',
        bottomId: 'costs-bottom',
        handleId: 'costs-splitter',
        storageKey: 'moose.vsplit.costs.topPx',
        minTop: 180,
        minBottom: 240
    });

    // Traces: only allow resizing when Advanced panel is visible
    initVerticalSplitter({
        containerId: 'trace-main',
        topId: 'trace-chat',
        bottomId: 'trace-advanced',
        handleId: 'traces-splitter',
        storageKey: 'moose.vsplit.traces.topPx',
        minTop: 180,
        minBottom: 180,
        isEnabled: () => {
            const adv = document.getElementById('trace-advanced');
            return !!adv && adv.style.display !== 'none';
        }
    });
}

function initVerticalSplitter({ containerId, topId, bottomId, handleId, storageKey, minTop, minBottom, isEnabled }) {
    const container = document.getElementById(containerId);
    const top = document.getElementById(topId);
    const bottom = document.getElementById(bottomId);
    const handle = document.getElementById(handleId);
    if (!container || !top || !bottom || !handle) return;

    function applyTopPx(px, persist) {
        top.style.flex = `0 0 ${px}px`;
        top.style.height = `${px}px`;
        bottom.style.flex = '1 1 auto';
        if (persist) {
            _lsSet(storageKey, String(Math.round(px)));
        }
    }

    function autoSizeTopPane() {
        const containerH = container.getBoundingClientRect().height;
        const handleH = handle.getBoundingClientRect().height || 0;
        const maxTop = Math.max(minTop, containerH - handleH - minBottom);
        const desired = Math.min(maxTop, Math.max(minTop, top.scrollHeight || minTop));
        applyTopPx(desired, false);
    }

    const hadSaved = (() => {
        const raw = _lsGet(storageKey, '');
        return !!(raw && String(raw).trim());
    })();

    // Apply saved top height (if any)
    try {
        const saved = _lsGet(storageKey, '');
        if (saved) {
            const px = parseInt(saved, 10);
            if (Number.isFinite(px) && px > 0) {
                applyTopPx(px, false);
            }
        }
    } catch (e) {}

    // If the user hasn't resized yet, auto-size top so it shows "completely" by default.
    if (!hadSaved) {
        requestAnimationFrame(() => requestAnimationFrame(autoSizeTopPane));
        // One more pass for async content (agents/cost summaries render after fetch)
        setTimeout(() => {
            if (!_lsGet(storageKey, '')) autoSizeTopPane();
        }, 700);
    }

    let dragging = false;
    let startY = 0;
    let startTopHeight = 0;

    function canResizeNow() {
        try {
            return typeof isEnabled === 'function' ? !!isEnabled() : true;
        } catch (e) {
            return true;
        }
    }

    function onDown(ev) {
        if (!canResizeNow()) return;
        dragging = true;
        startY = ev.clientY;
        startTopHeight = top.getBoundingClientRect().height;
        handle.classList.add('dragging');
        document.body.style.cursor = 'row-resize';
        document.body.style.userSelect = 'none';
        try { handle.setPointerCapture(ev.pointerId); } catch (e) {}
        ev.preventDefault();
    }

    function onMove(ev) {
        if (!dragging) return;
        if (!canResizeNow()) return;

        const diff = ev.clientY - startY;
        const containerH = container.getBoundingClientRect().height;
        const handleH = handle.getBoundingClientRect().height || 0;

        const maxTop = Math.max(minTop, containerH - handleH - minBottom);
        const nextTop = Math.max(minTop, Math.min(maxTop, startTopHeight + diff));

        applyTopPx(nextTop, true);
    }

    function onUp() {
        if (!dragging) return;
        dragging = false;
        handle.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    }

    handle.addEventListener('pointerdown', onDown);
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
}

// ============================================================================
// Agents
// ============================================================================

async function loadAgents() {
    const tbody = document.getElementById('agents-tbody');
    if (!tbody) return;

    showSkeleton('agents-tbody', 'text', 3);

    try {
        const response = await fetch(`/api/projects/${state.currentProject}/agents`);
        const agents = await response.json();

        if (agents.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="loading">No agents found</td></tr>';
            return;
        }

        tbody.innerHTML = '';
        agents.forEach(agent => {
            const row = document.createElement('tr');
            const statusClass = `status-${agent.status}`;
            const statusIcon = agent.status === 'running' ? '●' :
                              agent.status === 'error' ? '●' : '○';

            row.innerHTML = `
                <td>${escapeHtml(agent.name)}</td>
                <td class="${statusClass}">${statusIcon} ${agent.status}</td>
                <td class="container-name">${escapeHtml(agent.container || '')}</td>
                <td class="interactive-mode">${escapeHtml(agent.interactive_mode || '-')}</td>
                <td>${agent.url ? `<a href="${escapeHtml(agent.url)}" target="_blank" class="agent-link">Open</a>` : '-'}</td>
            `;
            tbody.appendChild(row);
        });

        // If the overview splitter hasn't been customized, auto-size the top pane to fit agents.
        try {
            const saved = _lsGet('moose.vsplit.overview.topPx', '');
            if (!saved) {
                const container = document.getElementById('overview-vsplit');
                const top = document.getElementById('overview-top');
                const handle = document.getElementById('overview-splitter');
                const bottomMin = 180;
                const topMin = 140;
                if (container && top && handle) {
                    const containerH = container.getBoundingClientRect().height;
                    const handleH = handle.getBoundingClientRect().height || 0;
                    const maxTop = Math.max(topMin, containerH - handleH - bottomMin);
                    const desired = Math.min(maxTop, Math.max(topMin, top.scrollHeight || topMin));
                    top.style.flex = `0 0 ${desired}px`;
                    top.style.height = `${desired}px`;
                }
            }
        } catch (e) {}
    } catch (error) {
        console.error('Failed to load agents:', error);
        tbody.innerHTML = '<tr><td colspan="5" class="loading">Error loading agents</td></tr>';
    }
}

// ============================================================================
// Log Files
// ============================================================================

async function loadLogFiles() {
    try {
        const response = await fetch(`/api/projects/${state.currentProject}/logs/files`);
        const files = await response.json();

        const dropdown = document.getElementById('log-file-dropdown');
        dropdown.innerHTML = '<option value="live">Live Stream</option>';

        files.forEach(file => {
            const option = document.createElement('option');
            option.value = file;
            option.textContent = file;
            dropdown.appendChild(option);
        });
    } catch (error) {
        console.error('Failed to load log files:', error);
    }
}

async function loadChatFiles() {
    try {
        const response = await fetch(`/api/projects/${state.currentProject}/chat/files`);
        const files = await response.json();

        const dropdown = document.getElementById('chat-file-dropdown');
        dropdown.innerHTML = '<option value="live">Live Stream</option>';

        files.forEach(file => {
            const option = document.createElement('option');
            option.value = file;
            option.textContent = file;
            dropdown.appendChild(option);
        });
    } catch (error) {
        console.error('Failed to load chat files:', error);
    }
}

function onLogFileChange() {
    const dropdown = document.getElementById('log-file-dropdown');
    const file = dropdown.value;

    if (file === 'live') {
        switchToLiveLogs();
    } else {
        loadHistoricalLogs(file);
    }
}

function onChatFileChange() {
    const dropdown = document.getElementById('chat-file-dropdown');
    const file = dropdown.value;

    if (file === 'live') {
        switchToLiveChat();
    } else {
        loadHistoricalChat(file);
    }
}

function switchToLiveLogs() {
    state.logViewMode = 'live';
    document.getElementById('log-file-dropdown').value = 'live';
    state.logDisplayCount = state.logDisplayStep;

    const container = document.getElementById('log-entries');
    renderLogWindow(container, state.streamedLogs);

    if (!state.ssesPaused && (!state.logEventSource || state.logEventSource.readyState === EventSource.CLOSED)) {
        connectLogStream();
    }
}

function resetChatOverlay() {
    // Show the start overlay when project changes
    const overlay = document.getElementById('chat-start-overlay');
    if (overlay) {
        overlay.style.display = 'flex';
    }
    
    // Set chatViewMode to 'stopped' so resumeSSEConnections() won't auto-connect
    // This is the key fix - prevents visibility change from triggering chat load
    state.chatViewMode = 'stopped';
    
    // Clear any existing chat state
    state.streamedMessages = [];
    state.chatHasMore = false;
    state.chatOldestTimestamp = null;
    state.chatNewestTimestamp = null;
    state.chatTotalAvailable = 0;
    const container = document.getElementById('chat-messages');
    if (container) {
        container.innerHTML = '';
    }
    updateChatInfoBar(0, 0, false);
    
    // Close any existing SSE connection
    if (state.chatEventSource) {
        state.chatEventSource.close();
        state.chatEventSource = null;
    }
}

function startChat() {
    // Hide the start overlay
    const overlay = document.getElementById('chat-start-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
    // Load chat with pagination
    switchToLiveChat();
}

async function switchToLiveChat() {
    state.chatViewMode = 'live';
    document.getElementById('chat-file-dropdown').value = 'live';
    state.pendingSystemMessage = null;

    // Hide start overlay
    const overlay = document.getElementById('chat-start-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }

    // Reset pagination state
    state.chatHasMore = false;
    state.chatOldestTimestamp = null;
    state.chatNewestTimestamp = null;
    state.chatTotalAvailable = 0;
    state.streamedMessages = [];

    const container = document.getElementById('chat-messages');
    container.innerHTML = '';

    // Show loading state
    updateChatInfoBar(0, 0, true);

    // Load initial messages via paginated API
    try {
        const response = await fetch(
            `/api/projects/${state.currentProject}/chat?mode=paginated&limit=50`
        );
        const data = await response.json();

        if (data.messages && data.messages.length > 0) {
            state.streamedMessages = data.messages;
            state.chatHasMore = data.has_more;
            state.chatOldestTimestamp = data.oldest_timestamp;
            state.chatNewestTimestamp = data.newest_timestamp;
            state.chatTotalAvailable = data.total_available;

            // Render messages
            renderChatMessages();

            // Connect SSE with 'since' parameter to only get new messages
            if (!state.ssesPaused) {
                connectChatStream(state.chatNewestTimestamp);
            }
        } else {
            // No messages yet, just connect SSE without 'since'
            if (!state.ssesPaused) {
                connectChatStream();
            }
        }

        updateChatInfoBar(state.streamedMessages.length, state.chatTotalAvailable, false);
    } catch (error) {
        console.error('Failed to load initial chat messages:', error);
        // Fallback to SSE without pagination
        if (!state.ssesPaused) {
            connectChatStream();
        }
    }

    if (state.chatAutoScroll) {
        scrollToBottom('chat-container');
    }
}

async function loadOlderMessages() {
    if (!state.currentProject || !state.chatOldestTimestamp || state.chatLoadingOlder) {
        return;
    }

    state.chatLoadingOlder = true;
    updateLoadOlderButton(true);

    try {
        const response = await fetch(
            `/api/projects/${state.currentProject}/chat?mode=paginated&limit=50&before=${encodeURIComponent(state.chatOldestTimestamp)}`
        );
        const data = await response.json();

        if (data.messages && data.messages.length > 0) {
            // Prepend older messages to the buffer
            const olderMessages = data.messages;
            state.streamedMessages = [...olderMessages, ...state.streamedMessages];

            // Update pagination state
            state.chatHasMore = data.has_more;
            state.chatOldestTimestamp = data.oldest_timestamp;

            // Limit buffer size by removing oldest messages if too large
            if (state.streamedMessages.length > state.maxChatBufferSize) {
                const excess = state.streamedMessages.length - state.maxChatBufferSize;
                state.streamedMessages = state.streamedMessages.slice(excess);
                state.chatOldestTimestamp = state.streamedMessages[0]?.timestamp || null;
                state.chatHasMore = true; // There are definitely more if we had to trim
            }

            // Re-render messages
            renderChatMessages();

            updateChatInfoBar(state.streamedMessages.length, state.chatTotalAvailable, false);
        } else {
            state.chatHasMore = false;
        }
    } catch (error) {
        console.error('Failed to load older messages:', error);
    } finally {
        state.chatLoadingOlder = false;
        updateLoadOlderButton(false);
    }
}

function renderChatMessages() {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    container.innerHTML = '';

    // Add "Load older" button if there are more messages
    if (state.chatHasMore && state.chatViewMode === 'live') {
        const loadOlderBtn = document.createElement('button');
        loadOlderBtn.id = 'load-older-btn';
        loadOlderBtn.className = 'load-older-btn';
        loadOlderBtn.textContent = 'Load older messages...';
        loadOlderBtn.onclick = loadOlderMessages;
        container.appendChild(loadOlderBtn);
    }

    // Render messages
    const processed = processMessagesForDisplay(state.streamedMessages);
    processed.forEach(msg => {
        const div = createChatMessageElement(msg);
        container.appendChild(div);
    });
}

function updateLoadOlderButton(loading) {
    const btn = document.getElementById('load-older-btn');
    if (btn) {
        btn.disabled = loading;
        btn.classList.toggle('loading', loading);
        btn.textContent = loading ? '' : 'Load older messages...';
    }
}

function updateChatInfoBar(displayed, total, loading) {
    const infoBar = document.getElementById('chat-info-bar');
    if (!infoBar) return;

    if (loading) {
        infoBar.innerHTML = '<span>Loading messages...</span>';
    } else if (total > 0) {
        const d = fmtInt(displayed);
        const t = fmtInt(total);
        infoBar.innerHTML = `<span class="message-count">${d} messages</span><span class="muted">${total > displayed ? `(${t} total available)` : ''}</span>`;
    } else {
        infoBar.innerHTML = '<span class="muted">No messages yet</span>';
    }
}

async function loadHistoricalLogs(filename) {
    state.logViewMode = 'historical';
    state.logDisplayCount = state.logDisplayStep;

    try {
        const response = await fetch(`/api/projects/${state.currentProject}/logs?file=${filename}`);
        const logs = await response.json();

        const container = document.getElementById('log-entries');
        // Store for windowing/filtering (and enable "load older" within the fetched set).
        state.streamedLogs = Array.isArray(logs) ? logs : [];
        renderLogWindow(container, state.streamedLogs);
    } catch (error) {
        console.error('Failed to load historical logs:', error);
    }
}

async function loadHistoricalChat(filename) {
    state.chatViewMode = 'historical';

    // Hide start overlay when loading historical file
    const overlay = document.getElementById('chat-start-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }

    try {
        const response = await fetch(`/api/projects/${state.currentProject}/chat?file=${filename}`);
        const messages = await response.json();

        const processed = processMessagesForDisplay(messages);

        const container = document.getElementById('chat-messages');
        container.innerHTML = '';

        processed.forEach(msg => {
            const div = createChatMessageElement(msg);
            container.appendChild(div);
        });

        scrollToBottom('chat-container');
    } catch (error) {
        console.error('Failed to load historical chat:', error);
    }
}

// ============================================================================
// SSE Streams
// ============================================================================

function connectLogStream() {
    if (state.logEventSource) {
        state.logEventSource.close();
    }

    if (!state.currentProject || state.ssesPaused) return;

    state.logEventSource = new EventSource(`/api/projects/${state.currentProject}/logs/stream`);

    state.logEventSource.onmessage = function(event) {
        const entry = JSON.parse(event.data);
        state.streamedLogs.push(entry);

        // Limit buffer size
        if (state.streamedLogs.length > state.maxLogBufferSize) {
            state.streamedLogs = state.streamedLogs.slice(-state.maxLogBufferSize);
        }

        if (state.logViewMode === 'live') {
            const entriesEl = document.getElementById('log-entries');
            if (entriesEl) renderLogWindow(entriesEl, state.streamedLogs);
        }
    };

    state.logEventSource.onerror = function(error) {
        console.error('Log stream error:', error);
        setTimeout(() => {
            if (state.currentProject && state.logViewMode === 'live' && !state.ssesPaused) {
                connectLogStream();
            }
        }, 5000);
    };

    updateConnectionStatus();
}

function connectChatStream(since = null) {
    if (state.chatEventSource) {
        state.chatEventSource.close();
    }

    if (!state.currentProject || state.ssesPaused) return;

    // Build URL with optional 'since' parameter for efficient live-tail mode
    let url = `/api/projects/${state.currentProject}/chat/stream`;
    if (since) {
        url += `?since=${encodeURIComponent(since)}`;
    }

    state.chatEventSource = new EventSource(url);

    state.chatEventSource.onmessage = function(event) {
        const message = JSON.parse(event.data);

        // Check if message already exists (deduplication)
        const isDuplicate = state.streamedMessages.some(
            m => m.timestamp === message.timestamp &&
                 m.type === message.type &&
                 m.content === message.content
        );

        if (!isDuplicate) {
            state.streamedMessages.push(message);

            // Update newest timestamp for future reconnections
            if (message.timestamp) {
                state.chatNewestTimestamp = message.timestamp;
            }

            // Limit buffer size by removing oldest messages
            if (state.streamedMessages.length > state.maxChatBufferSize) {
                const excess = state.streamedMessages.length - state.maxChatBufferSize;
                state.streamedMessages = state.streamedMessages.slice(excess);
                state.chatOldestTimestamp = state.streamedMessages[0]?.timestamp || null;
                state.chatHasMore = true;
            }

            if (state.chatViewMode === 'live') {
                appendChatMessage(message);
                updateChatInfoBar(state.streamedMessages.length, state.chatTotalAvailable, false);
            }
        }
    };

    state.chatEventSource.onerror = function(error) {
        console.error('Chat stream error:', error);
        setTimeout(() => {
            if (state.currentProject && state.chatViewMode === 'live' && !state.ssesPaused) {
                // Reconnect with 'since' parameter to avoid duplicate messages
                connectChatStream(state.chatNewestTimestamp);
            }
        }, 5000);
    };

    updateConnectionStatus();
}

// ============================================================================
// Log Entries
// ============================================================================

function appendLogEntry(entry, autoScroll = true) {
    const container = document.getElementById('log-entries');
    const div = createLogEntryElement(entry);
    container.appendChild(div);

    if (autoScroll && state.logAutoScroll) {
        scrollToBottom('log-container');
    }
}

function createLogEntryElement(entry) {
    const div = document.createElement('div');
    div.className = `log-entry ${entry.level || 'INFO'}`;

    const time = entry.time ? entry.time.split('T').pop().split('.')[0] : '';
    const level = entry.level || 'INFO';
    const message = entry.message || JSON.stringify(entry);

    div.innerHTML = `<span class="log-time">${time}</span><span class="log-level">[${level}]</span>${escapeHtml(message)}`;

    return div;
}

// ============================================================================
// Chat Messages
// ============================================================================

function onHideToolMessagesToggle() {
    const cb = document.getElementById('hide-tool-messages');
    state.hideToolMessages = !!(cb && cb.checked);

    const dropdown = document.getElementById('chat-file-dropdown');
    const file = dropdown ? dropdown.value : 'live';
    if (file === 'live') {
        // Just re-render existing messages instead of reloading
        renderChatMessages();
        if (state.chatAutoScroll) {
            scrollToBottom('chat-container');
        }
    } else {
        loadHistoricalChat(file);
    }
}

function appendChatMessage(message) {
    const type = message.type || 'unknown';

    if (type === 'system') {
        state.pendingSystemMessage = message;
        return;
    }

    if (state.hideToolMessages && type === 'tool') {
        return;
    }

    if (state.pendingSystemMessage) {
        message.systemMessage = state.pendingSystemMessage;
        state.pendingSystemMessage = null;
    }

    const container = document.getElementById('chat-messages');
    const div = createChatMessageElement(message);
    container.appendChild(div);

    if (state.chatAutoScroll) {
        scrollToBottom('chat-container');
    }
}

function processMessagesForDisplay(messages) {
    const processed = [];
    let pendingSystem = null;

    for (const msg of messages) {
        const type = msg.type || 'unknown';

        if (type === 'system') {
            pendingSystem = msg;
        } else if (state.hideToolMessages && type === 'tool') {
            continue;
        } else {
            if (pendingSystem) {
                msg.systemMessage = pendingSystem;
                pendingSystem = null;
            }
            processed.push(msg);
        }
    }

    return processed;
}

function createChatMessageElement(message) {
    const type = message.type || 'unknown';
    const rawContent = message.content;
    const timestamp = message.timestamp || '';

    const div = document.createElement('div');
    div.className = `message ${type}`;

    const avatarIcons = { 'human': '👤', 'ai': '🤖', 'tool': '🔧' };

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = avatarIcons[type] || '❓';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    const headerDiv = document.createElement('div');
    headerDiv.className = 'message-header';

    const typeLabel = document.createElement('span');
    typeLabel.className = 'message-type-label';

    if (type === 'ai') {
        if (message.agent_name && message.model_name) {
            typeLabel.textContent = `${message.agent_name} (${message.model_name})`;
        } else if (message.agent_name) {
            typeLabel.textContent = message.agent_name;
        } else if (message.model_name) {
            typeLabel.textContent = message.model_name;
        } else {
            typeLabel.textContent = 'AI';
        }
    } else if (type === 'tool') {
        typeLabel.textContent = message.tool_name || 'TOOL';
    } else {
        typeLabel.textContent = type.toUpperCase();
    }
    headerDiv.appendChild(typeLabel);

    if (timestamp) {
        const timeLabel = document.createElement('span');
        timeLabel.className = 'message-timestamp';
        const timeStr = timestamp.includes('T') ? timestamp.split('T')[1].split('.')[0] : timestamp;
        timeLabel.textContent = timeStr;
        headerDiv.appendChild(timeLabel);
    }

    contentDiv.appendChild(headerDiv);

    if (message.systemMessage) {
        const sysContent = message.systemMessage.content || '';
        const sysToggle = document.createElement('div');
        sysToggle.className = 'embedded-system-toggle';
        sysToggle.innerHTML = '📋 <span>System Message</span>';
        sysToggle.onclick = function() {
            const contentEl = this.nextElementSibling;
            contentEl.classList.toggle('expanded');
            this.classList.toggle('expanded');
        };
        contentDiv.appendChild(sysToggle);

        const systemContent = document.createElement('div');
        systemContent.className = 'embedded-system-content';
        systemContent.textContent = sysContent;
        contentDiv.appendChild(systemContent);
    }

    const { textContent, toolUses } = parseMessageContent(rawContent);

    if (textContent) {
        const textDiv = document.createElement('div');
        textDiv.className = 'message-text';
        contentDiv.appendChild(textDiv);

        const MAX_WORDS = 150;

        function countWords(text) {
            return (text || '').trim().split(/\s+/).filter(Boolean).length;
        }

        function truncateToWords(text, maxWords) {
            const words = (text || '').trim().split(/\s+/).filter(Boolean);
            if (words.length <= maxWords) {
                return { truncated: text, isTruncated: false };
            }
            return { truncated: words.slice(0, maxWords).join(' ') + ' …', isTruncated: true };
        }

        function renderRichText(targetDiv, text) {
            targetDiv.innerHTML = '';
            const contentParts = parseMarkdownContent(text);

            for (const part of contentParts) {
                if (part.type === 'code') {
                    const codeBlock = document.createElement('pre');
                    codeBlock.className = 'json-block';

                    if (part.lang === 'json' || isJsonString(part.content.trim())) {
                        try {
                            const jsonObj = JSON.parse(part.content.trim());
                            codeBlock.textContent = JSON.stringify(jsonObj, null, 2);
                        } catch {
                            codeBlock.textContent = part.content;
                        }
                    } else {
                        codeBlock.textContent = part.content;
                    }
                    targetDiv.appendChild(codeBlock);
                } else {
                    if (isJsonString(part.content)) {
                        try {
                            const jsonObj = JSON.parse(part.content);
                            const jsonBlock = document.createElement('pre');
                            jsonBlock.className = 'json-block';
                            jsonBlock.textContent = JSON.stringify(jsonObj, null, 2);
                            targetDiv.appendChild(jsonBlock);
                        } catch {
                            const textSpan = document.createElement('span');
                            textSpan.textContent = part.content;
                            targetDiv.appendChild(textSpan);
                        }
                    } else {
                        const textSpan = document.createElement('span');
                        textSpan.textContent = part.content;
                        targetDiv.appendChild(textSpan);
                    }
                }
            }
        }

        const totalWords = countWords(textContent);
        const { truncated, isTruncated } = truncateToWords(textContent, MAX_WORDS);
        let expanded = false;

        renderRichText(textDiv, isTruncated ? truncated : textContent);

        if (isTruncated) {
            const toggle = document.createElement('div');
            toggle.className = 'expand-toggle';
            toggle.textContent = `Show more (${totalWords} words)`;
            toggle.onclick = function() {
                expanded = !expanded;
                renderRichText(textDiv, expanded ? textContent : truncated);
                toggle.textContent = expanded ? 'Show less' : `Show more (${totalWords} words)`;
            };
            contentDiv.appendChild(toggle);
        }
    }

    const toolsToDisplay = (toolUses && toolUses.length > 0)
        ? toolUses.map(tu => ({ name: tu.name, args: tu.input }))
        : (message.tool_calls || []).map(tc => ({ name: tc.name, args: tc.args }));

    if (toolsToDisplay.length > 0) {
        toolsToDisplay.forEach(tool => {
            const toolBlock = document.createElement('div');
            toolBlock.className = 'tool-use-block';

            const toolHeader = document.createElement('div');
            toolHeader.className = 'tool-use-header';
            toolHeader.innerHTML = `🔧 ${escapeHtml(tool.name || 'tool')}`;
            toolBlock.appendChild(toolHeader);

            if (tool.args) {
                const argsDiv = document.createElement('pre');
                argsDiv.className = 'tool-use-args';
                argsDiv.textContent = JSON.stringify(tool.args, null, 2);
                toolBlock.appendChild(argsDiv);
            }

            contentDiv.appendChild(toolBlock);
        });
    }

    if (message.tool_call_id) {
        const tcIdDiv = document.createElement('div');
        tcIdDiv.className = 'tool-call-id';
        tcIdDiv.textContent = `Tool call id: ${message.tool_call_id}`;
        contentDiv.appendChild(tcIdDiv);
    }

    const usage = message.usage || message.usage_metadata || null;
    const cost = (typeof message.cost === 'number') ? message.cost : null;
    if (cost !== null || usage) {
        const it = usage && (usage.input_tokens ?? usage.input ?? 0);
        const ot = usage && (usage.output_tokens ?? usage.output ?? 0);
        const tt = usage && (usage.total_tokens ?? usage.total ?? ((it || 0) + (ot || 0)));
        const footer = document.createElement('div');
        footer.className = 'message-footer';
        const parts = [];
        if (cost !== null) parts.push(`Cost: $${fmtCost(cost, 6)}`);
        if (usage) parts.push(`Tokens: in ${fmtInt(it || 0)}, out ${fmtInt(ot || 0)} (${fmtInt(tt || 0)})`);
        footer.textContent = parts.join(' | ');
        contentDiv.appendChild(footer);
    }

    div.appendChild(avatar);
    div.appendChild(contentDiv);

    return div;
}

// ============================================================================
// Costs
// ============================================================================

async function loadCostSummary() {
    if (!state.currentProject) return;

    showSkeleton('cost-totals', 'text', 2);
    showSkeleton('cost-by-agent-tbody', 'text', 3);

    try {
        const resp = await fetch(`/api/projects/${state.currentProject}/llm/usage_summary`);
        const data = await resp.json();
        state.costData = data;
        renderCostSummary(data);
    } catch (e) {
        const totalsEl = document.getElementById('cost-totals');
        if (totalsEl) totalsEl.textContent = 'Failed to load cost summary.';
    }
}

function renderCostSummary(data) {
    const totalsEl = document.getElementById('cost-totals');
    const tbody = document.getElementById('cost-by-agent-tbody');
    if (!totalsEl || !tbody) return;

    const totals = (data && data.totals) ? data.totals : { cost: 0, tokens: { input: 0, output: 0, total: 0 } };
    const tokens = totals.tokens || { input: 0, output: 0, total: 0 };
    totalsEl.textContent = `Total cost: $${fmtCost(Number(totals.cost || 0), 6)}\n` +
                           `Tokens: in ${fmtInt(tokens.input || 0)}, out ${fmtInt(tokens.output || 0)} (total ${fmtInt(tokens.total || 0)})`;

    const byAgent = (data && data.by_agent) ? data.by_agent : {};
    const agents = Object.keys(byAgent).sort();

    tbody.innerHTML = '';
    if (agents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading">No cost data</td></tr>';
    } else {
        for (const agent of agents) {
            const row = document.createElement('tr');
            const a = byAgent[agent] || {};
            const t = a.tokens || {};
            const href = `/?project=${encodeURIComponent(state.currentProject || '')}&tab=costs&agent=${encodeURIComponent(agent)}`;
            row.innerHTML = `
                <td><a class="cost-link" href="${href}">${escapeHtml(agent)}</a></td>
                <td>$${fmtCost(Number(a.cost || 0), 6)}</td>
                <td>${fmtInt(t.input || 0)}</td>
                <td>${fmtInt(t.output || 0)}</td>
                <td>${fmtInt(t.total || 0)}</td>
            `;
            tbody.appendChild(row);
        }
    }

    renderCostCharts();

    // If the costs splitter hasn't been customized, auto-size the top pane to fit totals + table.
    try {
        const saved = _lsGet('moose.vsplit.costs.topPx', '');
        if (!saved) {
            const container = document.getElementById('costs-vsplit');
            const top = document.getElementById('costs-top');
            const handle = document.getElementById('costs-splitter');
            const bottomMin = 240;
            const topMin = 180;
            if (container && top && handle) {
                const containerH = container.getBoundingClientRect().height;
                const handleH = handle.getBoundingClientRect().height || 0;
                const maxTop = Math.max(topMin, containerH - handleH - bottomMin);
                const desired = Math.min(maxTop, Math.max(topMin, top.scrollHeight || topMin));
                top.style.flex = `0 0 ${desired}px`;
                top.style.height = `${desired}px`;
            }
        }
    } catch (e) {}
}

// ============================================================================
// Cost Agent View
// ============================================================================

function exitAgentCostView() {
    state.costAgent = null;
    state.agentPageOffset = 0;
    state.agentHasMore = false;
    state.agentExpanded = {};
    const main = document.getElementById('costs-main-view');
    const view = document.getElementById('costs-agent-view');
    if (main) main.style.display = '';
    if (view) view.style.display = 'none';
    const pageLayout = document.querySelector('.page-layout');
    if (pageLayout) pageLayout.classList.remove('costs-only');
}

function enterAgentCostView(agent) {
    state.costAgent = (agent || '').trim();
    if (!state.costAgent) {
        exitAgentCostView();
        return;
    }
    state.agentPageOffset = 0;
    state.agentHasMore = false;
    state.agentExpanded = {};

    const main = document.getElementById('costs-main-view');
    const view = document.getElementById('costs-agent-view');
    if (main) main.style.display = 'none';
    if (view) view.style.display = '';

    const nameEl = document.getElementById('cost-agent-name');
    if (nameEl) nameEl.textContent = state.costAgent;

    const limitSel = document.getElementById('cost-agent-limit');
    if (limitSel) limitSel.value = String(state.agentPageLimit);

    const pageLayout = document.querySelector('.page-layout');
    if (pageLayout) pageLayout.classList.add('costs-only');

    loadAgentCostBreakdown();
}

function backToCostSummary() {
    try {
        const params = new URLSearchParams(window.location.search || '');
        params.set('project', state.currentProject || '');
        params.set('tab', 'costs');
        params.delete('agent');
        params.delete('request_id');
        params.delete('ingress_only');
        window.location.search = params.toString();
    } catch (e) {
        window.location.href = '/';
    }
}

function onAgentLimitChange() {
    const sel = document.getElementById('cost-agent-limit');
    const v = sel ? parseInt(sel.value || '20', 10) : 20;
    state.agentPageLimit = (v === 50 || v === 100) ? v : 20;
    state.agentPageOffset = 0;
    loadAgentCostBreakdown();
}

function initAgentCostSorting() {
    const table = document.querySelector('#costs-agent-view .costs-table');
    if (!table) return;
    const headers = table.querySelectorAll('th[data-sort]');
    headers.forEach(th => {
        if (!th.title) th.title = 'Click to sort';
        th.addEventListener('click', () => {
            const key = th.getAttribute('data-sort') || '';
            if (!key) return;
            if (state.agentSortKey === key) {
                state.agentSortDir = (state.agentSortDir === 'asc') ? 'desc' : 'asc';
            } else {
                state.agentSortKey = key;
                state.agentSortDir = 'desc';
            }
            updateAgentSortHeaders();
            if (state.agentCostData) renderAgentCostBreakdown(state.agentCostData);
        });
    });
    updateAgentSortHeaders();
}

function updateAgentSortHeaders() {
    const headers = document.querySelectorAll('#costs-agent-view .costs-table th[data-sort]');
    headers.forEach(th => {
        const key = th.getAttribute('data-sort') || '';
        const indicator = th.querySelector('.sort-indicator');
        if (!indicator) return;
        if (key && key === state.agentSortKey) {
            indicator.textContent = state.agentSortDir === 'asc' ? '▲' : '▼';
        } else {
            indicator.textContent = '↕';
        }
    });
}

function sortAgentRequests(reqs) {
    const out = Array.isArray(reqs) ? reqs.slice() : [];
    const key = state.agentSortKey || '';
    if (!key) return out;
    const dir = state.agentSortDir === 'asc' ? 1 : -1;
    const getVal = (r) => {
        if (!r) return 0;
        const tokens = (r.tokens && typeof r.tokens === 'object') ? r.tokens : {};
        if (key === 'first_ts') return Number(r.first_ts || 0);
        if (key === 'cost') return Number(r.cost || 0);
        if (key === 'input_tokens') return Number(tokens.input || 0);
        if (key === 'output_tokens') return Number(tokens.output || 0);
        if (key === 'total_tokens') return Number(tokens.total || 0);
        return 0;
    };
    out.sort((a, b) => {
        const av = getVal(a);
        const bv = getVal(b);
        if (av === bv) {
            const ar = (a && a.request_id) ? String(a.request_id) : '';
            const br = (b && b.request_id) ? String(b.request_id) : '';
            return ar.localeCompare(br);
        }
        return (av < bv ? -1 : 1) * dir;
    });
    return out;
}

function agentPrevPage() {
    state.agentPageOffset = Math.max(0, state.agentPageOffset - state.agentPageLimit);
    loadAgentCostBreakdown();
}

function agentNextPage() {
    if (!state.agentHasMore) return;
    state.agentPageOffset = state.agentPageOffset + state.agentPageLimit;
    loadAgentCostBreakdown();
}

async function loadAgentCostBreakdown() {
    if (!state.currentProject || !state.costAgent) return;
    const tbody = document.getElementById('cost-agent-requests-tbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="loading">Loading…</td></tr>';
    try {
        const url =
            `/api/projects/${encodeURIComponent(state.currentProject)}` +
            `/llm/usage_by_agent/${encodeURIComponent(state.costAgent)}` +
            `?limit=${encodeURIComponent(String(state.agentPageLimit))}&offset=${encodeURIComponent(String(state.agentPageOffset))}`;
        const resp = await fetch(url);
        const data = await resp.json();
        renderAgentCostBreakdown(data);
    } catch (e) {
        if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="loading">Failed to load agent breakdown.</td></tr>';
    }
}

function openTraceDeepLink(requestId) {
    try {
        const params = new URLSearchParams(window.location.search || '');
        params.set('project', state.currentProject || '');
        params.set('tab', 'traces');
        params.set('request_id', requestId || '');
        params.set('ingress_only', '0');
        params.delete('agent');
        window.location.search = params.toString();
    } catch (e) {
        window.location.href = '/';
    }
}

function renderAgentCostBreakdown(data) {
    const tbody = document.getElementById('cost-agent-requests-tbody');
    if (!tbody) return;

    state.agentCostData = data || null;

    const agentName = (data && data.agent_name) ? String(data.agent_name) : (state.costAgent || '');
    const nameEl = document.getElementById('cost-agent-name');
    if (nameEl) nameEl.textContent = agentName;

    const reqs = Array.isArray(data && data.requests) ? data.requests : [];
    state.agentHasMore = !!(data && data.has_more);

    const prevBtn = document.getElementById('cost-agent-prev');
    const nextBtn = document.getElementById('cost-agent-next');
    if (prevBtn) prevBtn.disabled = state.agentPageOffset <= 0;
    if (nextBtn) nextBtn.disabled = !state.agentHasMore;

    tbody.innerHTML = '';

    const chartEl = document.getElementById('cost-agent-model-chart');
    if (chartEl) {
        const modelTotals = {};
        for (const r of reqs) {
            const byModel = Array.isArray(r && r.by_model) ? r.by_model : [];
            for (const m of byModel) {
                const modelName = (m && m.model) ? String(m.model) : 'unknown';
                const mc = Number((m && m.cost) ? m.cost : 0);
                modelTotals[modelName] = (modelTotals[modelName] || 0) + (Number.isFinite(mc) ? mc : 0);
            }
        }
        const models = Object.keys(modelTotals).map(name => ({ name, cost: modelTotals[name] }));
        models.sort((a, b) => b.cost - a.cost);
        if (models.length === 0) {
            chartEl.innerHTML = '';
        } else {
            const maxCost = Math.max(...models.map(m => m.cost), 0.000001);
            chartEl.innerHTML = models.map(m => {
                const pct = Math.max(2, Math.round((m.cost / maxCost) * 100));
                return `
                    <div class="costs-model-row">
                        <div class="costs-model-name" title="${escapeHtml(m.name)}">${escapeHtml(m.name)}</div>
                        <div class="costs-model-bar">
                            <div class="costs-model-bar-fill" style="width:${pct}%"></div>
                        </div>
                        <div class="costs-model-cost">$${fmtCost(m.cost, 6)}</div>
                    </div>
                `;
            }).join('');
        }
    }

    if (!reqs || reqs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="loading">No requests found for this agent.</td></tr>';
        return;
    }

    const sortedReqs = sortAgentRequests(reqs);

    for (const r of sortedReqs) {
        const rid = (r && r.request_id) ? String(r.request_id) : '';
        if (!rid) continue;
        const expanded = !!state.agentExpanded[rid];
        const firstTs = (r && (r.first_ts !== undefined) && (r.first_ts !== null)) ? r.first_ts : '';
        const tokens = (r && r.tokens && typeof r.tokens === 'object') ? r.tokens : {};
        const it = Number(tokens.input || 0);
        const ot = Number(tokens.output || 0);
        const tt = Number(tokens.total || 0);
        const cost = Number(r.cost || 0);

        const mainRow = document.createElement('tr');

        const toggleTd = document.createElement('td');
        const toggleBtn = document.createElement('button');
        toggleBtn.className = 'tri-btn';
        toggleBtn.type = 'button';
        toggleBtn.textContent = expanded ? '▼' : '▶';
        toggleTd.appendChild(toggleBtn);

        const ridTd = document.createElement('td');
        const ridLink = document.createElement('a');
        ridLink.className = 'cost-link mono';
        ridLink.href = '#';
        ridLink.textContent = rid;
        ridLink.addEventListener('click', (ev) => {
            ev.preventDefault();
            openTraceDeepLink(rid);
        });
        ridTd.appendChild(ridLink);

        const tsTd = document.createElement('td');
        tsTd.className = 'mono muted';
        tsTd.textContent = fmtTs(firstTs);

        const costTd = document.createElement('td');
        costTd.textContent = `$${fmtCost(cost, 6)}`;
        const itTd = document.createElement('td');
        itTd.textContent = fmtInt(Math.round(it));
        const otTd = document.createElement('td');
        otTd.textContent = fmtInt(Math.round(ot));
        const ttTd = document.createElement('td');
        ttTd.textContent = fmtInt(Math.round(tt));

        mainRow.appendChild(toggleTd);
        mainRow.appendChild(ridTd);
        mainRow.appendChild(tsTd);
        mainRow.appendChild(costTd);
        mainRow.appendChild(itTd);
        mainRow.appendChild(otTd);
        mainRow.appendChild(ttTd);

        const detailRow = document.createElement('tr');
        detailRow.className = 'cost-agent-detail-row';
        detailRow.style.display = expanded ? '' : 'none';
        const detailTd = document.createElement('td');
        detailTd.colSpan = 7;

        const byModel = Array.isArray(r && r.by_model) ? r.by_model : [];
        if (byModel.length === 0) {
            const msg = document.createElement('div');
            msg.className = 'muted';
            msg.textContent = 'No model breakdown available.';
            detailTd.appendChild(msg);
        } else {
            const sub = document.createElement('table');
            sub.className = 'costs-subtable';
            sub.innerHTML = `
                <thead>
                    <tr>
                        <th>Model</th>
                        <th>Cost (USD)</th>
                        <th>Input tokens</th>
                        <th>Output tokens</th>
                        <th>Total tokens</th>
                    </tr>
                </thead>
                <tbody></tbody>
            `;
            const subBody = sub.querySelector('tbody');
            for (const m of byModel) {
                const modelName = (m && m.model) ? String(m.model) : 'unknown';
                const mc = Number((m && m.cost) ? m.cost : 0);
                const mtoks = (m && m.tokens && typeof m.tokens === 'object') ? m.tokens : {};
                const mit = Number(mtoks.input || 0);
                const mot = Number(mtoks.output || 0);
                const mtt = Number(mtoks.total || 0);
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="mono">${escapeHtml(modelName)}</td>
                    <td>$${fmtCost(mc, 6)}</td>
                    <td>${fmtInt(Math.round(mit))}</td>
                    <td>${fmtInt(Math.round(mot))}</td>
                    <td>${fmtInt(Math.round(mtt))}</td>
                `;
                if (subBody) subBody.appendChild(tr);
            }
            detailTd.appendChild(sub);
        }

        detailRow.appendChild(detailTd);

        toggleBtn.addEventListener('click', (ev) => {
            ev.preventDefault();
            state.agentExpanded[rid] = !state.agentExpanded[rid];
            const now = !!state.agentExpanded[rid];
            toggleBtn.textContent = now ? '▼' : '▶';
            detailRow.style.display = now ? '' : 'none';
        });

        tbody.appendChild(mainRow);
        tbody.appendChild(detailRow);
    }
}

// ============================================================================
// Traces
// ============================================================================

function todayIso() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
}

function onTraceAdvancedToggle() {
    const cb = document.getElementById('trace-advanced-toggle');
    state.traceAdvanced = !!(cb && cb.checked);
    const adv = document.getElementById('trace-advanced');
    if (adv) adv.style.display = state.traceAdvanced ? '' : 'none';
    const splitter = document.getElementById('traces-splitter');
    if (splitter) splitter.style.display = state.traceAdvanced ? '' : 'none';

    // When advanced is hidden, ensure chat expands to full height (clear any fixed split sizing)
    const chat = document.getElementById('trace-chat');
    if (!state.traceAdvanced && chat) {
        chat.style.flex = '1 1 auto';
        chat.style.height = '';
    }

    // When advanced becomes visible, re-apply saved split height (if any), otherwise keep a sensible default.
    if (state.traceAdvanced && chat && adv) {
        try {
            const saved = localStorage.getItem('moose.vsplit.traces.topPx');
            const px = saved ? parseInt(saved, 10) : NaN;
            if (Number.isFinite(px) && px > 0) {
                chat.style.flex = `0 0 ${px}px`;
                chat.style.height = `${px}px`;
                adv.style.flex = '1 1 auto';
            } else {
                // Default: give chat most of the space
                chat.style.flex = '1 1 auto';
                adv.style.flex = '1 1 auto';
            }
        } catch (e) {}
    }
    if (state.selectedTraceId) {
        if (!state.traceChatPollInterval) {
            state.traceChatLastCount = 0;
            loadTraceChat(state.selectedTraceId, true);
            startTraceChatPolling(state.selectedTraceId);
        }
        if (state.traceAdvanced) {
            loadTraceAdvanced(state.selectedTraceId);
        }
    }
}

function onTraceDropdownChange() {
    const dd = document.getElementById('trace-dropdown');
    const rid = dd ? (dd.value || '') : '';
    if (!rid) {
        state.selectedTraceId = null;
        state.traceChatLastCount = 0;
        if (state.traceChatPollInterval) {
            clearInterval(state.traceChatPollInterval);
            state.traceChatPollInterval = null;
        }
        return;
    }
    state.selectedTraceId = rid;
    state.traceChatLastCount = 0;
    loadTraceChat(rid, true);
    if (state.traceAdvanced) {
        loadTraceAdvanced(rid);
    }
    startTraceChatPolling(rid);
}

async function loadTracesDropdown() {
    if (!state.currentProject) return;
    const chatEl = document.getElementById('trace-chat');
    const hadSelection = !!state.selectedTraceId;
    if (chatEl && !hadSelection) chatEl.innerHTML = '<span class="muted">Loading trace list…</span>';

    const qEl = document.getElementById('trace-q');
    const q = qEl ? (qEl.value || '').trim() : '';
    const sinceEl = document.getElementById('trace-since');
    const untilEl = document.getElementById('trace-until');
    const since = sinceEl ? (sinceEl.value || '') : '';
    const until = untilEl ? (untilEl.value || '') : '';

    const qs = [];
    qs.push('limit=500');
    const ingressEl = document.getElementById('trace-ingress-only');
    const ingressOnly = ingressEl ? !!ingressEl.checked : true;
    qs.push(`ingress_only=${ingressOnly ? '1' : '0'}`);
    if (q) qs.push(`q=${encodeURIComponent(q)}`);
    if (since) qs.push(`since=${encodeURIComponent(since)}`);
    if (until) qs.push(`until=${encodeURIComponent(until)}`);
    const url = `/api/projects/${state.currentProject}/traces?${qs.join('&')}`;

    try {
        const resp = await fetch(url);
        const traces = await resp.json();
        renderTraceDropdown(traces || []);

        try {
            const desired = window.__desiredTraceId || null;
            if (desired) {
                state.selectedTraceId = String(desired);
                const dd = document.getElementById('trace-dropdown');
                if (dd) dd.value = state.selectedTraceId;
                state.traceChatLastCount = 0;
                loadTraceChat(state.selectedTraceId, true);
                if (state.traceAdvanced) loadTraceAdvanced(state.selectedTraceId);
                startTraceChatPolling(state.selectedTraceId);
                window.__desiredTraceId = null;
            }
        } catch (e) {}

        if (!state.selectedTraceId && traces && traces.length > 0) {
            state.selectedTraceId = traces[0].request_id;
            const dd = document.getElementById('trace-dropdown');
            if (dd) dd.value = state.selectedTraceId;
            state.traceChatLastCount = 0;
            loadTraceChat(state.selectedTraceId, true);
            if (state.traceAdvanced) loadTraceAdvanced(state.selectedTraceId);
            startTraceChatPolling(state.selectedTraceId);
        } else if (state.selectedTraceId) {
            const dd = document.getElementById('trace-dropdown');
            if (dd) dd.value = state.selectedTraceId;
            if (!state.traceChatPollInterval) {
                state.traceChatLastCount = 0;
                loadTraceChat(state.selectedTraceId, true);
                startTraceChatPolling(state.selectedTraceId);
            }
            if (state.traceAdvanced) loadTraceAdvanced(state.selectedTraceId);
        } else if (chatEl) {
            chatEl.innerHTML = '<span class="muted">No traces found for the filter.</span>';
        }
    } catch (e) {
        if (chatEl) chatEl.innerHTML = `<div class="error">Failed to load traces: ${escapeHtmlSimple(e)}</div>`;
    }
}

function renderTraceDropdown(traces) {
    const dd = document.getElementById('trace-dropdown');
    if (!dd) return;
    dd.innerHTML = '<option value="">Select a request_id…</option>';
    (traces || []).forEach(t => {
        const rid = t.request_id || '';
        if (!rid) return;
        const started = t.started_at || '';
        const agent = t.root_agent || '';
        const status = t.status || '';
        const opt = document.createElement('option');
        opt.value = rid;
        opt.textContent = `${started} | ${rid} | ${agent} | ${status}`;
        opt.title = opt.textContent;
        dd.appendChild(opt);
    });
}

async function loadTraceAdvanced(requestId) {
    if (!state.currentProject) return;
    const detailEl = document.getElementById('trace-detail');
    if (detailEl) detailEl.innerHTML = 'Loading trace…';

    try {
        const resp = await fetch(`/api/projects/${state.currentProject}/traces/${encodeURIComponent(requestId)}`);
        const data = await resp.json();
        renderTraceDetail(data);
    } catch (e) {
        if (detailEl) detailEl.innerHTML = `<div class="error">Failed to load trace: ${escapeHtmlSimple(e)}</div>`;
    }
}

function startTraceChatPolling(requestId) {
    if (state.traceChatPollInterval) {
        clearInterval(state.traceChatPollInterval);
        state.traceChatPollInterval = null;
    }

    state.traceChatPollInterval = setInterval(() => {
        if (state.selectedTraceId === requestId && state.currentProject) {
            loadTraceChat(requestId, false);
        } else {
            if (state.traceChatPollInterval) {
                clearInterval(state.traceChatPollInterval);
                state.traceChatPollInterval = null;
            }
        }
    }, 2000);
}

async function loadTraceChat(requestId, replaceAll = false) {
    if (!state.currentProject) return;
    const chatEl = document.getElementById('trace-chat');
    if (!chatEl) return;

    if (replaceAll) {
        chatEl.innerHTML = '<span class="muted">Loading chain…</span>';
    }

    try {
        const resp = await fetch(`/api/projects/${state.currentProject}/traces/${encodeURIComponent(requestId)}/llm_chat`);
        const text = await resp.text();
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
        }
        let items = [];
        try {
            items = JSON.parse(text);
        } catch (e) {
            throw new Error(`Invalid JSON: ${text.slice(0, 200)}`);
        }
        if (replaceAll) {
            renderTraceChat(items || []);
        } else {
            appendTraceChat(items || []);
        }
    } catch (e) {
        if (replaceAll) {
            chatEl.innerHTML = `<div class="error">Failed to load chain: ${escapeHtmlSimple(e)}</div>`;
        }
    }
}

function truncateText(s, n) {
    const t = (s === null || s === undefined) ? '' : String(s);
    if (t.length <= n) return { text: t, truncated: false };
    return { text: t.slice(0, n), truncated: true };
}

function fmtTs(ts) {
    if (!ts) return '';
    const n = Number(ts);
    if (!Number.isFinite(n)) return String(ts);
    try {
        return new Date(n * 1000).toISOString();
    } catch {
        return String(ts);
    }
}

function parseUsageJson(raw) {
    if (!raw) return null;
    if (typeof raw === 'object') return raw;
    if (typeof raw !== 'string') return null;
    const s = raw.trim();
    if (!s) return null;
    try {
        return JSON.parse(s);
    } catch (e) {
        return null;
    }
}

function extractLlmUsage(it) {
    const usage = parseUsageJson(it && it.llm_usage_json);
    if (!usage || typeof usage !== 'object') return null;
    const itoks = Number(usage.input_tokens ?? usage.input ?? 0);
    const otoks = Number(usage.output_tokens ?? usage.output ?? 0);
    const ttoks = Number(usage.total_tokens ?? usage.total ?? (itoks + otoks));
    return {
        input: Number.isFinite(itoks) ? itoks : 0,
        output: Number.isFinite(otoks) ? otoks : 0,
        total: Number.isFinite(ttoks) ? ttoks : 0
    };
}

function formatLlmMeta(it, role) {
    const costRaw = (it && it.llm_cost !== undefined && it.llm_cost !== null) ? Number(it.llm_cost) : null;
    const usage = extractLlmUsage(it);
    if (costRaw === null && !usage) return '';
    const cls = role === 'tool' ? 'trace-meta-metric tool' : 'trace-meta-metric';
    const parts = [];
    if (costRaw !== null && Number.isFinite(costRaw)) {
        parts.push(`Cost ${fmtCost(costRaw, 6)}`);
    }
    if (usage) {
        parts.push(`Tokens in ${fmtInt(usage.input)} out ${fmtInt(usage.output)} (${fmtInt(usage.total)})`);
    }
    if (parts.length === 0) return '';
    return `<span class="${cls}">${escapeHtmlSimple(parts.join(' | '))}</span>`;
}

function fmtDurMs(startTs, endTs) {
    const s = Number(startTs);
    if (!Number.isFinite(s)) return '';
    let e = Number(endTs);
    if (!Number.isFinite(e) || e <= 0) {
        e = Date.now() / 1000.0;
    }
    const ms = (e - s) * 1000.0;
    if (!Number.isFinite(ms)) return '';
    if (ms < 0) return 'running';
    return `${ms.toFixed(1)}ms`;
}

function safeJsonStringify(val) {
    try {
        return JSON.stringify(val, null, 2);
    } catch (e) {
        try {
            return String(val);
        } catch (e2) {
            return '[unserializable]';
        }
    }
}

function extractToolCallsFromTraceItem(it) {
    if (it && it.tool_args_json) {
        let args = null;
        const rawArgs = String(it.tool_args_json);
        try {
            args = JSON.parse(rawArgs);
        } catch (e) {
            args = rawArgs;
        }
        return [{
            name: it.tool_call_name || it.name || 'tool',
            args: args
        }];
    }
    if (it && Array.isArray(it.tool_calls)) return it.tool_calls;
    const raw = it && it.tool_calls_json ? String(it.tool_calls_json) : '';
    if (!raw) return [];
    try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        return [];
    }
}

function renderTraceToolCalls(toolCalls) {
    if (!toolCalls || toolCalls.length === 0) return '';
    const blocks = toolCalls.map(tc => {
        const name = tc && (tc.name || tc.tool) ? String(tc.name || tc.tool) : 'tool';
        const args = tc && (tc.args ?? tc.input) ? (tc.args ?? tc.input) : null;
        const argsText = args !== null
            ? (typeof args === 'string' ? args : safeJsonStringify(args))
            : '';
        const argsHtml = args !== null
            ? `<pre class="tool-use-args">${escapeHtmlSimple(argsText)}</pre>`
            : '';
        return `
            <div class="tool-use-block">
                <div class="tool-use-header">🔧 ${escapeHtmlSimple(name)}</div>
                ${argsHtml}
            </div>
        `;
    });
    return blocks.join('');
}

function renderTraceChat(items) {
    const chatEl = document.getElementById('trace-chat');
    if (!chatEl) return;
    if (!items || items.length === 0) {
        chatEl.innerHTML = '<span class="muted">No LLM messages found for this trace yet.</span>';
        state.traceChatLastCount = 0;
        return;
    }
    const blocks = [];
    let lastAgent = null;
    items.forEach((it, idx) => {
        const role = (it.role || 'unknown').toLowerCase();
        const cls = role === 'user' ? 'user' : (role === 'tool' ? 'tool' : 'assistant');
        const content = it.content === null || it.content === undefined ? '' : String(it.content);
        const spanId = it.span_id || '';
        const spanName = it.span_name || '';
        const agent = it.agent_name || '';
        const kind = it.span_kind || '';
        const status = it.span_status || '';
        const startTs = it.span_start_ts || null;
        const endTs = it.span_end_ts || null;
        const dur = fmtDurMs(startTs, endTs);
        const startFmt = fmtTs(startTs);
        const parentKind = it.parent_kind || '';
        const parentName = it.parent_name || '';
        const owner = (parentKind || parentName) ? `${parentKind}${parentName ? ' ' + parentName : ''}` : '';
        const model = (kind === 'llm.call') ? (spanName || '') : '';
        const meta = formatLlmMeta(it, role);
        if (agent && agent !== lastAgent) {
            blocks.push(
                `<div class="trace-agent-divider"><span class="label">Agent: ${escapeHtmlSimple(agent)}</span></div>`
            );
            lastAgent = agent;
        }
        const trunc = truncateText(content, 500);
        const id = `trace_msg_${idx}`;
        const body = `<div class="trace-bubble-body" id="${id}">${escapeHtmlSimple(trunc.text)}${trunc.truncated ? '…' : ''}</div>`;
        const expand = trunc.truncated
            ? `<div class="trace-expand" onclick="toggleTraceMsg('${id}', ${idx})">Expand</div>`
            : '';
        const toolCalls = extractToolCallsFromTraceItem(it);
        const toolBlocks = renderTraceToolCalls(toolCalls);
        blocks.push(`
          <div class="trace-bubble ${cls}">
            <div class="trace-bubble-header muted">
              <span class="mono">${escapeHtmlSimple(role)}</span>
              ${agent ? `<span>${escapeHtmlSimple(agent)}</span>` : ''}
              ${owner ? `<span>${escapeHtmlSimple(owner)}</span>` : ''}
              ${spanId ? `<span class="mono">${escapeHtmlSimple(spanId)}</span>` : ''}
            </div>
            <div class="trace-bubble-subheader">
              <span class="k">Kind</span><span class="v mono">${escapeHtmlSimple(kind)}</span>
              <span class="k">Name</span><span class="v">${escapeHtmlSimple(parentName || '')}</span>
              ${model ? `<span class="k">Model</span><span class="v mono">${escapeHtmlSimple(model)}</span>` : ''}
              ${meta ? `<span class="k">Usage</span>${meta}` : ''}
              <span class="k">Start</span><span class="v mono">${escapeHtmlSimple(startFmt)}</span>
              <span class="k">Dur</span><span class="v mono">${escapeHtmlSimple(dur)}</span>
              <span class="k">Status</span><span class="v mono">${escapeHtmlSimple(status)}</span>
            </div>
            ${body}
            ${expand}
            ${toolBlocks}
          </div>
        `);
    });
    window.__traceChatItems = items;
    window.__traceChatExpanded = window.__traceChatExpanded || {};
    chatEl.innerHTML = blocks.join('');
    state.traceChatLastCount = items.length;
    scrollToBottom('trace-chat');
}

function appendTraceChat(items) {
    const chatEl = document.getElementById('trace-chat');
    if (!chatEl) return;
    if (!items || items.length === 0) return;
    if (items.length <= state.traceChatLastCount) return;

    const newItems = items.slice(state.traceChatLastCount);
    if (newItems.length === 0) return;

    const existingItems = window.__traceChatItems || [];
    let lastAgent = null;
    if (existingItems.length > 0) {
        const lastItem = existingItems[existingItems.length - 1];
        lastAgent = lastItem.agent_name || null;
    }

    const blocks = [];
    newItems.forEach((it, relativeIdx) => {
        const idx = state.traceChatLastCount + relativeIdx;
        const role = (it.role || 'unknown').toLowerCase();
        const cls = role === 'user' ? 'user' : (role === 'tool' ? 'tool' : 'assistant');
        const content = it.content === null || it.content === undefined ? '' : String(it.content);
        const spanId = it.span_id || '';
        const spanName = it.span_name || '';
        const agent = it.agent_name || '';
        const kind = it.span_kind || '';
        const status = it.span_status || '';
        const startTs = it.span_start_ts || null;
        const endTs = it.span_end_ts || null;
        const dur = fmtDurMs(startTs, endTs);
        const startFmt = fmtTs(startTs);
        const parentKind = it.parent_kind || '';
        const parentName = it.parent_name || '';
        const owner = (parentKind || parentName) ? `${parentKind}${parentName ? ' ' + parentName : ''}` : '';
        const model = (kind === 'llm.call') ? (spanName || '') : '';
        const meta = formatLlmMeta(it, role);

        if (agent && agent !== lastAgent) {
            blocks.push(
                `<div class="trace-agent-divider"><span class="label">Agent: ${escapeHtmlSimple(agent)}</span></div>`
            );
            lastAgent = agent;
        }

        const trunc = truncateText(content, 500);
        const id = `trace_msg_${idx}`;
        const body = `<div class="trace-bubble-body" id="${id}">${escapeHtmlSimple(trunc.text)}${trunc.truncated ? '…' : ''}</div>`;
        const expand = trunc.truncated
            ? `<div class="trace-expand" onclick="toggleTraceMsg('${id}', ${idx})">Expand</div>`
            : '';
        const toolCalls = extractToolCallsFromTraceItem(it);
        const toolBlocks = renderTraceToolCalls(toolCalls);
        blocks.push(`
          <div class="trace-bubble ${cls}">
            <div class="trace-bubble-header muted">
              <span class="mono">${escapeHtmlSimple(role)}</span>
              ${agent ? `<span>${escapeHtmlSimple(agent)}</span>` : ''}
              ${owner ? `<span>${escapeHtmlSimple(owner)}</span>` : ''}
              ${spanId ? `<span class="mono">${escapeHtmlSimple(spanId)}</span>` : ''}
            </div>
            <div class="trace-bubble-subheader">
              <span class="k">Kind</span><span class="v mono">${escapeHtmlSimple(kind)}</span>
              <span class="k">Name</span><span class="v">${escapeHtmlSimple(parentName || '')}</span>
              ${model ? `<span class="k">Model</span><span class="v mono">${escapeHtmlSimple(model)}</span>` : ''}
              ${meta ? `<span class="k">Usage</span>${meta}` : ''}
              <span class="k">Start</span><span class="v mono">${escapeHtmlSimple(startFmt)}</span>
              <span class="k">Dur</span><span class="v mono">${escapeHtmlSimple(dur)}</span>
              <span class="k">Status</span><span class="v mono">${escapeHtmlSimple(status)}</span>
            </div>
            ${body}
            ${expand}
            ${toolBlocks}
          </div>
        `);
    });

    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = blocks.join('');
    while (tempDiv.firstChild) {
        chatEl.appendChild(tempDiv.firstChild);
    }

    window.__traceChatItems = items;
    state.traceChatLastCount = items.length;
    scrollToBottom('trace-chat');
}

function toggleTraceMsg(domId, idx) {
    const el = document.getElementById(domId);
    if (!el) return;
    const items = window.__traceChatItems || [];
    const it = items[idx];
    if (!it) return;
    const full = it.content === null || it.content === undefined ? '' : String(it.content);
    window.__traceChatExpanded = window.__traceChatExpanded || {};
    const isExpanded = !!window.__traceChatExpanded[domId];
    if (isExpanded) {
        const trunc = truncateText(full, 500);
        el.textContent = trunc.text + (trunc.truncated ? '…' : '');
        window.__traceChatExpanded[domId] = false;
        const link = el.parentElement && el.parentElement.querySelector('.trace-expand');
        if (link) link.textContent = 'Expand';
    } else {
        el.textContent = full;
        window.__traceChatExpanded[domId] = true;
        const link = el.parentElement && el.parentElement.querySelector('.trace-expand');
        if (link) link.textContent = 'Collapse';
    }

    // Re-apply trace layout when content size changes (prevents overlap glitches).
    if (!state.traceAdvanced) {
        const chat = document.getElementById('trace-chat');
        if (chat) {
            chat.style.flex = '1 1 auto';
            chat.style.height = '';
        }
    }
}

function renderTraceDetail(data) {
    const detailEl = document.getElementById('trace-detail');
    if (!detailEl) return;
    const tr = data && data.trace ? data.trace : null;
    const spans = (data && Array.isArray(data.spans)) ? data.spans : [];

    if (!tr) {
        detailEl.innerHTML = '<div class="muted">Trace not found.</div>';
        return;
    }

    const byId = {};
    const children = {};
    spans.forEach(s => {
        byId[s.span_id] = s;
        const p = s.parent_span_id || '';
        if (!children[p]) children[p] = [];
        children[p].push(s.span_id);
    });
    Object.keys(children).forEach(p => {
        children[p].sort((a, b) => (byId[a].start_ts || 0) - (byId[b].start_ts || 0));
    });

    const roots = spans
        .filter(s => !s.parent_span_id || !byId[s.parent_span_id])
        .sort((a, b) => (a.start_ts || 0) - (b.start_ts || 0))
        .map(s => s.span_id);

    function renderNode(spanId, depth) {
        const s = byId[spanId];
        const kids = children[spanId] || [];
        const dur = fmtDurMs(s.start_ts, s.end_ts);
        const isLlm = (s.kind === 'llm.call');
        const safeSpanId = encodeURIComponent(String(spanId || ''));
        const btn = isLlm ? `<button class="mini-btn" onclick="event.stopPropagation();selectLlmSpan('${safeSpanId}')">LLM</button>` : '';
        const line = `
          <div class="span-line" onclick="selectSpan('${safeSpanId}')">
            <span class="mono">${'&nbsp;'.repeat(depth * 2)}${escapeHtmlSimple(s.kind || '')}</span>
            <span class="span-name">${escapeHtmlSimple(s.name || '')}</span>
            <span class="muted">${escapeHtmlSimple(dur)}</span>
            <span class="muted">${escapeHtmlSimple(s.status || '')}</span>
            ${btn}
          </div>
        `;
        const childHtml = kids.map(k => renderNode(k, depth + 1)).join('');
        return line + childHtml;
    }

    const treeHtml = roots.map(r => renderNode(r, 0)).join('');

    detailEl.innerHTML = `
      <div class="trace-meta">
        <div><span class="muted">request_id</span>: <span class="mono">${escapeHtmlSimple(tr.request_id || '')}</span></div>
        <div class="muted">root_agent=${escapeHtmlSimple(tr.root_agent || '')} root_kind=${escapeHtmlSimple(tr.root_kind || '')} status=${escapeHtmlSimple(tr.status || '')} started_at=${escapeHtmlSimple(tr.started_at || '')}</div>
      </div>
      <div>
        <div class="trace-subtitle">Span tree</div>
        <div class="span-tree">${treeHtml || '<div class="muted">No spans.</div>'}</div>
      </div>
    `;
}

async function selectLlmSpan(spanId) {
    if (!state.currentProject) return;
    try { spanId = decodeURIComponent(String(spanId || '')); } catch { spanId = String(spanId || ''); }
    state.selectedLlmSpanId = spanId;
    if (state.traceChatPollInterval) {
        clearInterval(state.traceChatPollInterval);
        state.traceChatPollInterval = null;
    }
    state.traceChatLastCount = 0;
    const llmEl = document.getElementById('trace-chat');
    if (!llmEl) return;
    llmEl.innerHTML = 'Loading LLM messages…';
    try {
        const url = '/api/projects/' + encodeURIComponent(state.currentProject) + '/spans/' + encodeURIComponent(spanId) + '/llm_messages';
        const resp = await fetch(url);
        const msgs = await resp.json();
        const out = [];
        for (const m of (msgs || [])) {
            const role = (m && m.role) ? String(m.role) : '';
            const nm = (m && m.name) ? (' (' + String(m.name) + ')') : '';
            const content = (m && (m.content !== null && m.content !== undefined)) ? String(m.content) : '';
            out.push('=== ' + role + nm + ' ===\n' + content + '\n');
        }
        const joined = out.join('\n');
        llmEl.innerHTML =
            '<div class="trace-subtitle">LLM messages for span <span class="mono">' +
            escapeHtmlSimple(spanId) +
            '</span></div><pre class="mono pre-wrap">' +
            escapeHtmlSimple(joined) +
            '</pre>';
        scrollToBottom('trace-chat');
    } catch (e) {
        llmEl.innerHTML = '<div class="error">Failed to load LLM messages: ' + escapeHtmlSimple(e) + '</div>';
    }
}

function selectSpan(spanId) {
    try { spanId = decodeURIComponent(String(spanId || '')); } catch { spanId = String(spanId || ''); }
    return;
}

// ============================================================================
// Utilities
// ============================================================================

function fmtInt(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v ?? '');
    try {
        return Math.round(n).toLocaleString();
    } catch (e) {
        return String(Math.round(n));
    }
}

function fmtCost(v, decimals = 6) {
    const n = Number(v);
    const d = (typeof decimals === 'number' && Number.isFinite(decimals))
        ? Math.max(0, Math.min(12, Math.floor(decimals)))
        : 6;
    if (!Number.isFinite(n)) return String(v ?? '');
    try {
        return new Intl.NumberFormat(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }).format(n);
    } catch (e) {
        return n.toFixed(d);
    }
}

function parseMessageContent(content) {
    let textContent = '';
    let toolUses = [];

    if (!content) {
        return { textContent: '', toolUses: [] };
    }

    if (typeof content === 'string') {
        return { textContent: content, toolUses: [] };
    }

    if (Array.isArray(content)) {
        const textParts = [];

        for (const item of content) {
            if (item.type === 'text' && item.text) {
                textParts.push(item.text);
            } else if (item.type === 'tool_use') {
                toolUses.push({
                    id: item.id,
                    name: item.name,
                    input: item.input
                });
            }
        }

        textContent = textParts.join('\n');
    } else if (typeof content === 'object') {
        textContent = JSON.stringify(content, null, 2);
    }

    return { textContent, toolUses };
}

function parseMarkdownContent(text) {
    if (!text || typeof text !== 'string') {
        return [{ type: 'text', content: text || '' }];
    }

    const parts = [];
    const codeBlockRegex = /```(\w*)\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    while ((match = codeBlockRegex.exec(text)) !== null) {
        if (match.index > lastIndex) {
            const textBefore = text.slice(lastIndex, match.index).trim();
            if (textBefore) {
                parts.push({ type: 'text', content: textBefore });
            }
        }

        const lang = match[1] || '';
        const code = match[2];
        parts.push({ type: 'code', lang: lang, content: code });

        lastIndex = match.index + match[0].length;
    }

    if (lastIndex < text.length) {
        const remaining = text.slice(lastIndex).trim();
        if (remaining) {
            parts.push({ type: 'text', content: remaining });
        }
    }

    if (parts.length === 0) {
        parts.push({ type: 'text', content: text });
    }

    return parts;
}

function isJsonString(str) {
    if (typeof str !== 'string') return false;
    const trimmed = str.trim();
    return (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
           (trimmed.startsWith('[') && trimmed.endsWith(']'));
}

function scrollToBottom(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeHtmlSimple(s) {
    const t = (s === null || s === undefined) ? '' : String(s);
    return t.replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
}

// Make functions available globally for onclick handlers
window.switchRightTab = switchRightTab;
window.onProjectChange = onProjectChange;
window.refreshProjects = refreshProjects;
window.onLogFileChange = onLogFileChange;
window.onChatFileChange = onChatFileChange;
window.switchToLiveLogs = switchToLiveLogs;
window.switchToLiveChat = switchToLiveChat;
window.startChat = startChat;
window.resetChatOverlay = resetChatOverlay;
window.onHideToolMessagesToggle = onHideToolMessagesToggle;
window.loadOlderMessages = loadOlderMessages;
window.loadOlderLogs = loadOlderLogs;
window.loadCostSummary = loadCostSummary;
window.backToCostSummary = backToCostSummary;
window.onAgentLimitChange = onAgentLimitChange;
window.agentPrevPage = agentPrevPage;
window.agentNextPage = agentNextPage;
window.loadTracesDropdown = loadTracesDropdown;
window.onTraceDropdownChange = onTraceDropdownChange;
window.onTraceAdvancedToggle = onTraceAdvancedToggle;
window.toggleTraceMsg = toggleTraceMsg;
window.selectLlmSpan = selectLlmSpan;
window.selectSpan = selectSpan;
window.toggleLogAutoScroll = toggleLogAutoScroll;
window.toggleChatAutoScroll = toggleChatAutoScroll;
window.onChartTimeRangeChange = onChartTimeRangeChange;
window.toggleChatCollapse = toggleChatCollapse;
