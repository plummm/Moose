"""Core Web UI HTML/CSS/JS Generator for Moose Framework.

Provides the main dashboard page with:
- MOOSE ASCII art header
- Project dropdown selector
- Online agents section
- Logging section with file selector and live streaming
- Chat section with file selector and live streaming
"""

# MOOSE ASCII Art
MOOSE_ASCII = r"""
 __  __  ___   ___  ____  _____ 
|  \/  |/ _ \ / _ \/ ___|| ____|
| |\/| | | | | | | \___ \|  _|  
| |  | | |_| | |_| |___) | |___ 
|_|  |_|\___/ \___/|____/|_____|
"""


def get_dashboard_html() -> str:
    """Generate the main dashboard HTML page.
    
    Returns:
        Complete HTML page as string
    """
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Moose Dashboard</title>
    <style>
{get_css()}
    </style>
</head>
<body>
    <div class="page-layout">
        <!-- Left Panel: Chat Section -->
        <section class="chat-panel" id="chat-panel">
            <div class="section-header">
                <h2>Chat</h2>
                <div class="section-controls">
                    <label class="chat-toggle">
                        <input type="checkbox" id="hide-tool-messages" onchange="onHideToolMessagesToggle()" />
                        <span>Hide tool messages</span>
                    </label>
                    <select id="chat-file-dropdown" onchange="onChatFileChange()">
                        <option value="live">Live Stream</option>
                    </select>
                    <button class="refresh-btn" onclick="switchToLiveChat()" title="Switch to live stream">
                        &#x21bb;
                    </button>
                </div>
            </div>
            <div class="chat-container" id="chat-container">
                <div class="chat-messages" id="chat-messages">
                    <!-- Messages will be inserted here -->
                </div>
            </div>
        </section>
        
        <!-- Resize Handle -->
        <div class="resize-handle" id="resize-handle"></div>
        
        <!-- Right Panel: Header, Agents, Logging -->
        <div class="right-panel" id="right-panel">
            <!-- Header with ASCII Art and Project Selector -->
            <header class="header">
                <pre class="ascii-art">{MOOSE_ASCII}</pre>
                <div class="project-selector">
                    <label for="project-dropdown">Project:</label>
                    <select id="project-dropdown" onchange="onProjectChange()">
                        <option value="">Loading...</option>
                    </select>
                    <button class="refresh-btn" onclick="refreshProjects(true)" title="Refresh projects list">
                        &#x21bb;
                    </button>
                </div>
            </header>

            <!-- Tabs -->
            <div class="tab-bar">
                <button id="tab-btn-overview" class="tab-btn active" onclick="switchRightTab('overview')">Overview</button>
                <button id="tab-btn-costs" class="tab-btn" onclick="switchRightTab('costs')">Costs</button>
                <button id="tab-btn-traces" class="tab-btn" onclick="switchRightTab('traces')">Traces</button>
            </div>

            <!-- Overview tab content -->
            <div id="tab-content-overview" class="tab-content active">
                <!-- Online Agents Section -->
                <section class="agents-section">
                    <h2>Online Agents</h2>
                    <div class="agents-table-container">
                        <table class="agents-table">
                            <thead>
                                <tr>
                                    <th>Agent Name</th>
                                    <th>Status</th>
                                    <th>Container</th>
                                    <th>Interactive Mode</th>
                                    <th>Link</th>
                                </tr>
                            </thead>
                            <tbody id="agents-tbody">
                                <tr>
                                    <td colspan="5" class="loading">Loading agents...</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </section>

                <!-- Logging Section -->
                <section class="logging-section">
                    <div class="section-header">
                        <h2>Logging</h2>
                        <div class="section-controls">
                            <select id="log-file-dropdown" onchange="onLogFileChange()">
                                <option value="live">Live Stream</option>
                            </select>
                            <button class="refresh-btn" onclick="switchToLiveLogs()" title="Switch to live stream">
                                &#x21bb;
                            </button>
                        </div>
                    </div>
                    <div class="log-container" id="log-container">
                        <div class="log-entries" id="log-entries">
                            <!-- Log entries will be inserted here -->
                        </div>
                    </div>
                </section>
            </div>

            <!-- Costs tab content -->
            <div id="tab-content-costs" class="tab-content">
                <section class="costs-section">
                    <div class="section-header">
                        <h2>Costs</h2>
                        <div class="section-controls">
                            <button class="refresh-btn" onclick="loadCostSummary()" title="Refresh cost summary">
                                &#x21bb;
                            </button>
                        </div>
                    </div>

                    <div class="costs-grid">
                        <div class="costs-card">
                            <div class="costs-card-title">Project totals</div>
                            <div id="cost-totals" class="costs-metrics">Loading…</div>
                        </div>

                        <div class="costs-card">
                            <div class="costs-card-title">By agent</div>
                            <div class="costs-table-container">
                                <table class="costs-table">
                                    <thead>
                                        <tr>
                                            <th>Agent</th>
                                            <th>Cost (USD)</th>
                                            <th>Input tokens</th>
                                            <th>Output tokens</th>
                                            <th>Total tokens</th>
                                        </tr>
                                    </thead>
                                    <tbody id="cost-by-agent-tbody">
                                        <tr><td colspan="5" class="loading">Loading…</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div class="costs-card">
                            <div class="costs-card-title">Cost per day (stacked)</div>
                            <div id="chart-legend-cost" class="chart-legend"></div>
                            <div id="chart-cost" class="stacked-chart"></div>
                        </div>

                        <div class="costs-card">
                            <div class="costs-card-title">Tokens per day (stacked)</div>
                            <div id="chart-legend-tokens" class="chart-legend"></div>
                            <div id="chart-tokens" class="stacked-chart"></div>
                        </div>
                    </div>
                </section>
            </div>

            <!-- Traces tab content -->
            <div id="tab-content-traces" class="tab-content">
                <section class="traces-section">
                    <div class="section-header">
                        <h2>Tracing</h2>
                        <div class="section-controls">
                            <label class="muted">Since</label>
                            <input id="trace-since" type="date" onchange="loadTracesDropdown()" />
                            <label class="muted">Until</label>
                            <input id="trace-until" type="date" onchange="loadTracesDropdown()" />
                            <input id="trace-q" placeholder="Filter by request_id…" onkeydown="if(event.key==='Enter') loadTracesDropdown();" />
                            <select id="trace-dropdown" onchange="onTraceDropdownChange()">
                                <option value="">Select a request_id…</option>
                            </select>
                            <label class="chat-toggle" title="Show only HTTP ingress root traces">
                                <input type="checkbox" id="trace-ingress-only" checked onchange="loadTracesDropdown()" />
                                <span>Ingress only</span>
                            </label>
                            <label class="chat-toggle">
                                <input type="checkbox" id="trace-advanced-toggle" onchange="onTraceAdvancedToggle()" />
                                <span>Advanced</span>
                            </label>
                            <button class="refresh-btn" onclick="loadTracesDropdown()" title="Refresh traces">&#x21bb;</button>
                        </div>
                    </div>
                    <div class="trace-main">
                        <div id="trace-chat" class="trace-chat muted">Select a request_id to view the chain.</div>
                        <div id="trace-advanced" class="trace-advanced" style="display:none;">
                            <div id="trace-detail" class="trace-detail muted">Advanced: span tree</div>
                        </div>
                    </div>
                </section>
            </div>
        </div>
    </div>
    
    <script>
{get_javascript()}
    </script>
</body>
</html>'''


def get_css() -> str:
    """Get the CSS styles for the dashboard.
    
    Returns:
        CSS string
    """
    return '''
:root {
    --bg-primary: #1a1a2e;
    --bg-secondary: #16213e;
    --bg-tertiary: #0f3460;
    --text-primary: #eaeaea;
    --text-secondary: #a0a0a0;
    --accent-blue: #4a90d9;
    --accent-green: #4caf50;
    --accent-orange: #ff9800;
    --accent-purple: #9c27b0;
    --accent-red: #f44336;
    --border-color: #2a2a4a;
    --human-color: #2196F3;
    --ai-color: #9c27b0;
    --tool-color: #ff9800;
    --system-color: #607d8b;
    --chat-panel-width: 400px;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    height: 100vh;
    overflow: hidden;
}

/* Page Layout - Horizontal Split */
.page-layout {
    display: flex;
    height: 100vh;
    width: 100%;
}

/* Full-width traces mode (hide chat panel + resize handle) */
.page-layout.traces-full .chat-panel {
    display: none;
}
.page-layout.traces-full .resize-handle {
    display: none;
}

/* Left Panel - Chat */
.chat-panel {
    width: var(--chat-panel-width);
    min-width: 300px;
    max-width: 800px;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border-color);
    display: flex;
    flex-direction: column;
    padding: 15px;
}

/* Resize Handle */
.resize-handle {
    width: 6px;
    background: var(--border-color);
    cursor: col-resize;
    transition: background 0.2s;
    flex-shrink: 0;
}

.resize-handle:hover,
.resize-handle.dragging {
    background: var(--accent-blue);
}

/* Right Panel */
.right-panel {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    padding: 20px;
    min-width: 400px;
}

/* Tabs */
.tab-bar {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
    padding: 10px;
    background: var(--bg-secondary);
    border-radius: 10px;
    border: 1px solid var(--border-color);
    flex-shrink: 0;
}

.tab-btn {
    padding: 8px 12px;
    border-radius: 8px;
    border: 1px solid var(--border-color);
    background: var(--bg-tertiary);
    color: var(--text-primary);
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    transition: background 0.2s;
}

.tab-btn:hover {
    background: var(--accent-blue);
}

.tab-btn.active {
    background: var(--accent-blue);
    border-color: var(--accent-blue);
}

.tab-content {
    display: none;
    overflow: hidden;
}

.tab-content.active {
    display: block;
}

/* ------------------------------------------------------------------
   Traces section (minimal styles)
   ------------------------------------------------------------------ */
.trace-main {
    /* Avoid nested scroll containers; chat should be the primary scroller. */
    height: calc(100vh - 180px);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    min-height: 0;
}

.trace-chat {
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 10px;
    background: var(--bg-primary);
    flex: 1 1 auto;
    min-height: 0;
    overflow: auto;
    /* Ensure the last line isn't clipped under the bottom edge/scrollbar overlay */
    padding-bottom: 22px;
}

.trace-advanced {
    margin-top: 12px;
    flex: 0 0 auto;
    max-height: 45vh;
    overflow: auto;
}

.trace-detail {
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 10px;
    background: var(--bg-primary);
    overflow: auto;
}

/* Chat-bubble look for trace chain */
.trace-bubble {
    max-width: 900px;
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid var(--border-color);
    background: var(--bg-secondary);
    margin: 8px 0;
}
.trace-bubble.user {
    margin-left: auto;
    background: rgba(74, 144, 217, 0.20);
}
.trace-bubble.assistant {
    margin-right: auto;
}
.trace-bubble.tool {
    margin-right: auto;
    background: rgba(255, 152, 0, 0.14);
}
.trace-bubble-header {
    font-size: 11px;
    margin-bottom: 6px;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}
.trace-bubble-subheader {
    font-size: 11px;
    margin-bottom: 6px;
    color: var(--text-secondary);
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}
.trace-bubble-subheader .k {
    opacity: 0.85;
}
.trace-bubble-subheader .v {
    color: var(--text-primary);
    opacity: 0.9;
}
.trace-bubble-subheader .mono {
    font-family: 'Courier New', monospace;
}
.trace-bubble-body {
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-word;
}

/* Divider between different agents in a trace chat stream */
.trace-agent-divider {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 14px 0;
    opacity: 0.9;
}
.trace-agent-divider::before,
.trace-agent-divider::after {
    content: "";
    flex: 1;
    height: 1px;
    background: var(--border-color);
}
.trace-agent-divider .label {
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid var(--border-color);
    background: rgba(96, 125, 139, 0.12);
    color: var(--text-secondary);
    white-space: nowrap;
}
.trace-expand {
    margin-top: 6px;
    font-size: 11px;
    cursor: pointer;
    color: var(--accent-blue);
    user-select: none;
}
.traces-layout {
    display: grid;
    grid-template-columns: 360px 1fr;
    gap: 12px;
    align-items: start;
}

.trace-list {
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 8px;
    background: var(--bg-primary);
    max-height: 70vh;
    overflow: auto;
}

.trace-row {
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 8px;
    cursor: pointer;
}

.trace-row:hover {
    background: var(--bg-tertiary);
    border-color: var(--border-color);
}

.trace-selected {
    border-color: var(--accent-blue);
}

.trace-row-top {
    font-size: 12px;
    margin-bottom: 4px;
}

.trace-row-sub {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 11px;
}

.trace-detail {
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 10px;
    background: var(--bg-primary);
    max-height: 70vh;
    overflow: auto;
}

.trace-meta {
    margin-bottom: 10px;
}

/* Timeline moved into per-message subheaders in the main chat view. */

.trace-subtitle {
    font-size: 12px;
    font-weight: 600;
    margin: 8px 0;
}

.span-tree {
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 8px;
}

.span-line {
    display: flex;
    gap: 8px;
    align-items: baseline;
    padding: 2px 0;
}

.span-name {
    flex: 1;
}

.mini-btn {
    font: inherit;
    padding: 2px 6px;
    border-radius: 8px;
    border: 1px solid var(--border-color);
    background: var(--bg-primary);
    color: var(--text-primary);
    cursor: pointer;
}


.llm-detail {
    margin-top: 10px;
}

.mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.pre-wrap {
    white-space: pre-wrap;
}

/* Header */
.header {
    text-align: center;
    margin-bottom: 20px;
    padding: 15px;
    background: var(--bg-secondary);
    border-radius: 10px;
    border: 1px solid var(--border-color);
    flex-shrink: 0;
}

.ascii-art {
    font-family: 'Courier New', monospace;
    color: var(--accent-blue);
    font-size: 12px;
    line-height: 1.2;
    margin-bottom: 10px;
}

.project-selector {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
}

.project-selector label {
    font-weight: 600;
    color: var(--text-secondary);
}

.project-selector select {
    padding: 8px 15px;
    border-radius: 5px;
    border: 1px solid var(--border-color);
    background: var(--bg-tertiary);
    color: var(--text-primary);
    font-size: 14px;
    cursor: pointer;
    min-width: 200px;
}

/* Agents Section */
.agents-section {
    margin-bottom: 20px;
    padding: 15px;
    background: var(--bg-secondary);
    border-radius: 10px;
    border: 1px solid var(--border-color);
    flex-shrink: 0;
}

.agents-section h2 {
    font-size: 16px;
    margin-bottom: 10px;
    color: var(--accent-blue);
}

.agents-table-container {
    overflow-x: auto;
}

.agents-table {
    width: 100%;
    border-collapse: collapse;
}

.agents-table th,
.agents-table td {
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border-color);
}

.agents-table th {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
}

.agents-table td {
    font-size: 13px;
}

.agents-table .loading {
    text-align: center;
    color: var(--text-secondary);
    font-style: italic;
}

.status-running { color: var(--accent-green); }
.status-stopped { color: var(--text-secondary); }
.status-error { color: var(--accent-red); }

.agent-link {
    color: var(--accent-blue);
    text-decoration: none;
}

.agent-link:hover {
    text-decoration: underline;
}

.container-name {
    font-family: 'Courier New', monospace;
    font-size: 11px;
    color: var(--text-secondary);
}

.interactive-mode {
    font-size: 11px;
    color: var(--accent-orange);
}

/* Section Headers */
.section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
    flex-shrink: 0;
}

.section-header h2 {
    font-size: 16px;
    color: var(--accent-blue);
}

.section-controls {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
}

.chat-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-secondary);
    user-select: none;
    cursor: pointer;
    flex-shrink: 0;
    white-space: nowrap;
}

.chat-toggle input {
    cursor: pointer;
}

.section-controls select {
    padding: 5px 10px;
    border-radius: 4px;
    border: 1px solid var(--border-color);
    background: var(--bg-tertiary);
    color: var(--text-primary);
    font-size: 12px;
    cursor: pointer;
    flex: 1 1 420px;
    min-width: 220px;
    max-width: 900px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.refresh-btn {
    padding: 5px 10px;
    border-radius: 4px;
    border: 1px solid var(--border-color);
    background: var(--bg-tertiary);
    color: var(--text-primary);
    cursor: pointer;
    font-size: 16px;
    transition: background 0.2s;
}

.refresh-btn:hover {
    background: var(--accent-blue);
}

.chat-container {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    background: var(--bg-primary);
    border-radius: 8px;
}

.chat-messages {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

/* Message Styles */
.message {
    display: flex;
    gap: 10px;
    width: 100%;
    max-width: 100%;
}

.message.human {
    justify-content: flex-end;
    flex-direction: row-reverse;
}

.message.ai,
.message.tool {
    justify-content: flex-start;
}

.message-avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    flex-shrink: 0;
}

.message.human .message-avatar {
    background: var(--human-color);
}

.message.ai .message-avatar {
    background: var(--ai-color);
}

.message.tool .message-avatar {
    background: var(--tool-color);
}

.message-content {
    flex: 1;
    min-width: 0;
    max-width: calc(100% - 50px);
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 13px;
    line-height: 1.5;
    word-wrap: break-word;
    overflow-wrap: break-word;
}

.message.human .message-content {
    background: var(--human-color);
    border-bottom-right-radius: 4px;
}

.message.ai .message-content {
    background: var(--bg-tertiary);
    border: 1px solid var(--ai-color);
    border-bottom-left-radius: 4px;
}

.message.tool .message-content {
    background: var(--bg-tertiary);
    border: 1px solid var(--tool-color);
    border-bottom-left-radius: 4px;
}

/* JSON code block */
.json-block {
    background: var(--bg-primary);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 10px;
    margin-top: 8px;
    font-family: 'Courier New', monospace;
    font-size: 11px;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
}

/* Tool use block (within AI message) */
.tool-use-block {
    background: var(--bg-primary);
    border: 1px solid var(--tool-color);
    border-radius: 6px;
    padding: 8px 10px;
    margin-top: 8px;
}

.tool-use-header {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--tool-color);
    font-weight: 600;
    margin-bottom: 6px;
}

.tool-use-args {
    font-family: 'Courier New', monospace;
    font-size: 10px;
    background: var(--bg-tertiary);
    border-radius: 4px;
    padding: 6px 8px;
    white-space: pre-wrap;
    word-break: break-all;
}


/* Message header with type and timestamp */
.message-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
}

.message-type-label {
    font-size: 10px;
    text-transform: uppercase;
    opacity: 0.7;
}

.message-timestamp {
    font-size: 10px;
    color: var(--text-secondary);
    opacity: 0.7;
}

.message-text {
    white-space: pre-wrap;
}

.message-footer {
    margin-top: 8px;
    font-size: 11px;
    color: var(--text-secondary);
    opacity: 0.9;
}

.expand-toggle {
    margin-top: 8px;
    display: inline-block;
    cursor: pointer;
    color: var(--accent-blue);
    font-size: 12px;
    user-select: none;
}

.expand-toggle:hover {
    text-decoration: underline;
}

/* Embedded System Message (within other messages) */
.embedded-system-toggle {
    cursor: pointer;
    padding: 4px 8px;
    margin-bottom: 8px;
    background: var(--bg-primary);
    border: 1px solid var(--system-color);
    border-radius: 4px;
    font-size: 11px;
    color: var(--system-color);
    display: inline-flex;
    align-items: center;
    gap: 5px;
    transition: all 0.2s;
}

.embedded-system-toggle:hover {
    background: var(--system-color);
    color: var(--text-primary);
}

.embedded-system-toggle.expanded {
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
    margin-bottom: 0;
}

.embedded-system-content {
    display: none;
    padding: 8px;
    margin-bottom: 8px;
    background: var(--bg-primary);
    border: 1px solid var(--system-color);
    border-top: none;
    border-radius: 0 0 4px 4px;
    font-size: 11px;
    color: var(--text-secondary);
    white-space: pre-wrap;
    max-height: 200px;
    overflow-y: auto;
}

.embedded-system-content.expanded {
    display: block;
}

/* Tool call ID styling */
.tool-call-id {
    font-size: 10px;
    opacity: 0.7;
    margin-top: 4px;
    color: var(--tool-color);
}


/* Logging Section */
.logging-section {
    padding: 15px;
    background: var(--bg-secondary);
    border-radius: 10px;
    border: 1px solid var(--border-color);
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 200px;
    overflow: hidden;
}

/* Costs */
.costs-section {
    padding: 15px;
    background: var(--bg-secondary);
    border-radius: 10px;
    border: 1px solid var(--border-color);
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 200px;
    overflow: hidden;
}

.costs-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 12px;
    overflow: auto;
    padding-right: 4px;
}

.costs-card {
    background: var(--bg-primary);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 12px;
}

.costs-card-title {
    font-size: 12px;
    color: var(--accent-blue);
    font-weight: 700;
    margin-bottom: 8px;
}

.costs-metrics {
    font-family: 'Courier New', monospace;
    font-size: 12px;
    line-height: 1.5;
}

.costs-table-container {
    overflow-x: auto;
}

.costs-table {
    width: 100%;
    border-collapse: collapse;
}

.costs-table th,
.costs-table td {
    padding: 8px 10px;
    text-align: left;
    border-bottom: 1px solid var(--border-color);
    font-size: 12px;
}

.costs-table th {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
}

.stacked-chart {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.chart-row {
    display: grid;
    grid-template-columns: 90px 1fr 90px;
    gap: 10px;
    align-items: center;
}

.chart-date {
    font-family: 'Courier New', monospace;
    font-size: 11px;
    color: var(--text-secondary);
}

.chart-bar {
    height: 14px;
    border-radius: 8px;
    overflow: hidden;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    display: flex;
}

.chart-seg {
    height: 100%;
}

.chart-value {
    font-family: 'Courier New', monospace;
    font-size: 11px;
    color: var(--text-secondary);
    text-align: right;
}

.chart-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 8px;
}

.legend-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--text-secondary);
}

.legend-swatch {
    width: 10px;
    height: 10px;
    border-radius: 3px;
    border: 1px solid var(--border-color);
}

.log-container {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    background: var(--bg-primary);
    border-radius: 8px;
    font-family: 'Courier New', monospace;
    font-size: 12px;
}

.log-entries {
    display: flex;
    flex-direction: column;
    gap: 2px;
}

.log-entry {
    padding: 4px 8px;
    border-radius: 3px;
    white-space: pre-wrap;
    word-wrap: break-word;
}

.log-entry.DEBUG { color: var(--text-secondary); }
.log-entry.INFO { color: var(--text-primary); }
.log-entry.WARNING { color: var(--accent-orange); }
.log-entry.ERROR { color: var(--accent-red); }
.log-entry.CRITICAL { color: #ff5252; background: rgba(244, 67, 54, 0.1); }

.log-time {
    color: var(--text-secondary);
    margin-right: 10px;
}

.log-level {
    font-weight: 600;
    margin-right: 10px;
    display: inline-block;
    min-width: 60px;
}

/* Scrollbar styling */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: var(--bg-primary);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb {
    background: var(--border-color);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--accent-blue);
}
'''


def get_javascript() -> str:
    """Get the JavaScript for the dashboard.
    
    Returns:
        JavaScript string
    """
    return '''
// State
let currentProject = null;
let logEventSource = null;
let chatEventSource = null;

// Buffers for preserving streamed messages when switching views
let streamedLogs = [];
let streamedMessages = [];

// Pending system message to attach to next message
let pendingSystemMessage = null;

// View state
let logViewMode = 'live';  // 'live' or 'historical'
let chatViewMode = 'live';  // 'live' or 'historical'
let rightTab = 'overview';
let hideToolMessages = false; // session-only

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    loadProjects();
    initResizeHandle();
});

function onHideToolMessagesToggle() {
    const cb = document.getElementById('hide-tool-messages');
    hideToolMessages = !!(cb && cb.checked);

    // Re-render current chat view
    const dropdown = document.getElementById('chat-file-dropdown');
    const file = dropdown ? dropdown.value : 'live';
    if (file === 'live') {
        switchToLiveChat();
    } else {
        loadHistoricalChat(file);
    }
}

function switchRightTab(tabName) {
    rightTab = tabName;
    const btnOverview = document.getElementById('tab-btn-overview');
    const btnCosts = document.getElementById('tab-btn-costs');
    const btnTraces = document.getElementById('tab-btn-traces');
    const contentOverview = document.getElementById('tab-content-overview');
    const contentCosts = document.getElementById('tab-content-costs');
    const contentTraces = document.getElementById('tab-content-traces');
    const pageLayout = document.querySelector('.page-layout');

    if (tabName === 'costs') {
        btnOverview.classList.remove('active');
        btnCosts.classList.add('active');
        btnTraces.classList.remove('active');
        contentOverview.classList.remove('active');
        contentCosts.classList.add('active');
        contentTraces.classList.remove('active');
        if (pageLayout) pageLayout.classList.remove('traces-full');
        // Stop trace polling when switching away
        if (traceChatPollInterval) {
            clearInterval(traceChatPollInterval);
            traceChatPollInterval = null;
        }
        // lazy load
        loadCostSummary();
    } else if (tabName === 'traces') {
        btnOverview.classList.remove('active');
        btnCosts.classList.remove('active');
        btnTraces.classList.add('active');
        contentOverview.classList.remove('active');
        contentCosts.classList.remove('active');
        contentTraces.classList.add('active');
        if (pageLayout) pageLayout.classList.add('traces-full');
        // Initialize date inputs to today if empty
        const sinceEl = document.getElementById('trace-since');
        const untilEl = document.getElementById('trace-until');
        const t = _todayIso();
        if (sinceEl && !sinceEl.value) sinceEl.value = t;
        if (untilEl && !untilEl.value) untilEl.value = t;
        loadTracesDropdown();
    } else {
        btnOverview.classList.add('active');
        btnCosts.classList.remove('active');
        btnTraces.classList.remove('active');
        contentOverview.classList.add('active');
        contentCosts.classList.remove('active');
        contentTraces.classList.remove('active');
        if (pageLayout) pageLayout.classList.remove('traces-full');
        // Stop trace polling when switching away
        if (traceChatPollInterval) {
            clearInterval(traceChatPollInterval);
            traceChatPollInterval = null;
        }
    }
}

// Initialize resize handle for chat panel
function initResizeHandle() {
    const resizeHandle = document.getElementById('resize-handle');
    const chatPanel = document.getElementById('chat-panel');
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

// Load available projects
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
        
        projects.forEach((project, index) => {
            const option = document.createElement('option');
            option.value = project;
            option.textContent = project;
            dropdown.appendChild(option);
        });
        
        // Preserve selection when possible; otherwise fall back to first project.
        let desired = (preserveSelection ? (prev || currentProject || '') : '') || '';
        if (desired && projects.includes(desired)) {
            currentProject = desired;
        } else {
            currentProject = projects[0];
        }
        dropdown.value = currentProject;

        loadProjectData();
    } catch (error) {
        console.error('Failed to load projects:', error);
        document.getElementById('project-dropdown').innerHTML = 
            '<option value="">Error loading projects</option>';
    }
}

async function refreshProjects(showToast) {
    // For now, just reload dropdown and preserve selection. showToast reserved for future UI.
    await loadProjects(true);
}

// Handle project change
function onProjectChange() {
    const dropdown = document.getElementById('project-dropdown');
    currentProject = dropdown.value;
    
    // Clear buffers when switching projects
    streamedLogs = [];
    streamedMessages = [];
    
    // Stop trace polling when switching projects
    if (traceChatPollInterval) {
        clearInterval(traceChatPollInterval);
        traceChatPollInterval = null;
    }
    selectedTraceId = null;
    traceChatLastCount = 0;
    
    // Reset view modes to live
    logViewMode = 'live';
    chatViewMode = 'live';
    document.getElementById('log-file-dropdown').value = 'live';
    document.getElementById('chat-file-dropdown').value = 'live';
    
    loadProjectData();
}

// Load all data for current project
function loadProjectData() {
    if (!currentProject) return;
    
    loadAgents();
    loadLogFiles();
    loadChatFiles();
    connectLogStream();
    connectChatStream();
    // Keep cost summary up to date for the selected project (even if tab not visible)
    loadCostSummary();
    if (rightTab === 'traces') {
        loadTracesDropdown();
    }
}

// ---------------------------------------------------------------------
// Tracing UI
// ---------------------------------------------------------------------
let selectedTraceId = null;
let selectedLlmSpanId = null;
let traceAdvanced = false;
let traceChatLastCount = 0; // Track last message count for auto-append
let traceChatPollInterval = null; // Polling interval for trace updates

function _escapeHtml(s) {
    const t = (s === null || s === undefined) ? '' : String(s);
    return t.replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
}

function _fmtTs(ts) {
    if (!ts) return '';
    const n = Number(ts);
    if (!Number.isFinite(n)) return String(ts);
    try {
        return new Date(n * 1000).toISOString();
    } catch {
        return String(ts);
    }
}

function _fmtDurMs(startTs, endTs) {
    const s = Number(startTs);
    if (!Number.isFinite(s)) return '';

    // endTs is missing for running spans; treat as "now"
    let e = Number(endTs);
    if (!Number.isFinite(e) || e <= 0) {
        e = Date.now() / 1000.0;
    }

    const ms = (e - s) * 1000.0;
    if (!Number.isFinite(ms)) return '';
    // Guard against negative values if clocks/data are inconsistent
    if (ms < 0) return 'running';
    return `${ms.toFixed(1)}ms`;
}

function _todayIso() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
}

function onTraceAdvancedToggle() {
    const cb = document.getElementById('trace-advanced-toggle');
    traceAdvanced = !!(cb && cb.checked);
    const adv = document.getElementById('trace-advanced');
    if (adv) adv.style.display = traceAdvanced ? '' : 'none';
    // Refresh current trace detail if selected
    if (selectedTraceId) {
        // Only replace if not already polling (to preserve auto-append behavior)
        if (!traceChatPollInterval) {
            traceChatLastCount = 0;
            loadTraceChat(selectedTraceId, true);
            startTraceChatPolling(selectedTraceId);
        }
        if (traceAdvanced) {
            loadTraceAdvanced(selectedTraceId);
        }
    }
}

function onTraceDropdownChange() {
    const dd = document.getElementById('trace-dropdown');
    const rid = dd ? (dd.value || '') : '';
    if (!rid) {
        // Clear selection - stop polling
        selectedTraceId = null;
        traceChatLastCount = 0;
        if (traceChatPollInterval) {
            clearInterval(traceChatPollInterval);
            traceChatPollInterval = null;
        }
        return;
    }
    selectedTraceId = rid;
    traceChatLastCount = 0; // Reset count when selecting new trace
    try { if (dd) dd.title = dd.options && dd.selectedIndex >= 0 ? (dd.options[dd.selectedIndex].textContent || '') : ''; } catch (e) {}
    loadTraceChat(rid, true); // true = initial load (replace all)
    if (traceAdvanced) {
        loadTraceAdvanced(rid);
    }
    // Start polling for updates
    startTraceChatPolling(rid);
}

async function loadTracesDropdown() {
    if (!currentProject) return;
    const chatEl = document.getElementById('trace-chat');
    // Don't wipe the chat panel when refreshing the dropdown; it can leave the UI stuck
    // in "Loading trace list…" if we don't reload the selected trace afterwards.
    const hadSelection = !!selectedTraceId;
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
    const url = `/api/projects/${currentProject}/traces?${qs.join('&')}`;

    try {
        const resp = await fetch(url);
        const traces = await resp.json();
        renderTraceDropdown(traces || []);
        if (!selectedTraceId && traces && traces.length > 0) {
            selectedTraceId = traces[0].request_id;
            const dd = document.getElementById('trace-dropdown');
            if (dd) dd.value = selectedTraceId;
            traceChatLastCount = 0;
            loadTraceChat(selectedTraceId, true);
            if (traceAdvanced) loadTraceAdvanced(selectedTraceId);
            startTraceChatPolling(selectedTraceId);
        } else if (selectedTraceId) {
            // Keep selection if present in list
            const dd = document.getElementById('trace-dropdown');
            if (dd) dd.value = selectedTraceId;
            // Refresh chat + advanced panes for current selection (fixes "Loading trace list…" getting stuck).
            // Only replace if not already polling (to preserve auto-append behavior)
            if (!traceChatPollInterval) {
                traceChatLastCount = 0;
                loadTraceChat(selectedTraceId, true);
                startTraceChatPolling(selectedTraceId);
            }
            if (traceAdvanced) loadTraceAdvanced(selectedTraceId);
        } else if (chatEl) {
            chatEl.innerHTML = '<span class="muted">No traces found for the filter.</span>';
        }
    } catch (e) {
        if (chatEl) chatEl.innerHTML = `<div class="error">Failed to load traces: ${_escapeHtml(e)}</div>`;
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
    try {
        dd.title = dd.options && dd.selectedIndex >= 0 ? (dd.options[dd.selectedIndex].textContent || '') : '';
    } catch (e) {}
}

async function loadTraceAdvanced(requestId) {
    if (!currentProject) return;
    const detailEl = document.getElementById('trace-detail');
    if (detailEl) detailEl.innerHTML = 'Loading trace…';

    try {
        const resp = await fetch(`/api/projects/${currentProject}/traces/${encodeURIComponent(requestId)}`);
        const data = await resp.json();
        renderTraceDetail(data);
    } catch (e) {
        if (detailEl) detailEl.innerHTML = `<div class="error">Failed to load trace: ${_escapeHtml(e)}</div>`;
    }
}

function startTraceChatPolling(requestId) {
    // Stop any existing polling
    if (traceChatPollInterval) {
        clearInterval(traceChatPollInterval);
        traceChatPollInterval = null;
    }
    
    // Poll every 2 seconds for new messages
    traceChatPollInterval = setInterval(() => {
        if (selectedTraceId === requestId && currentProject) {
            loadTraceChat(requestId, false); // false = append mode
        } else {
            // Selection changed, stop polling
            if (traceChatPollInterval) {
                clearInterval(traceChatPollInterval);
                traceChatPollInterval = null;
            }
        }
    }, 2000);
}

async function loadTraceChat(requestId, replaceAll = false) {
    if (!currentProject) return;
    const chatEl = document.getElementById('trace-chat');
    if (!chatEl) return;
    
    if (replaceAll) {
        chatEl.innerHTML = '<span class="muted">Loading chain…</span>';
    }

    try {
        const resp = await fetch(`/api/projects/${currentProject}/traces/${encodeURIComponent(requestId)}/llm_chat`);
        const items = await resp.json();
        if (replaceAll) {
            renderTraceChat(items || []);
        } else {
            appendTraceChat(items || []);
        }
    } catch (e) {
        if (replaceAll) {
            chatEl.innerHTML = `<div class="error">Failed to load chain: ${_escapeHtml(e)}</div>`;
        }
    }
}

function _truncate(s, n) {
    const t = (s === null || s === undefined) ? '' : String(s);
    if (t.length <= n) return {text: t, truncated: false};
    return {text: t.slice(0, n), truncated: true};
}

function renderTraceChat(items) {
    const chatEl = document.getElementById('trace-chat');
    if (!chatEl) return;
    if (!items || items.length === 0) {
        chatEl.innerHTML = '<span class="muted">No LLM messages found for this trace yet.</span>';
        traceChatLastCount = 0;
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
        const dur = _fmtDurMs(startTs, endTs);
        const startFmt = _fmtTs(startTs);
        const parentKind = it.parent_kind || '';
        const parentName = it.parent_name || '';
        const owner = (parentKind || parentName) ? `${parentKind}${parentName ? ' ' + parentName : ''}` : '';
        // For llm.call spans, span_name is the model name (avoid duplicating it under "Name").
        const model = (kind === 'llm.call') ? (spanName || '') : '';
        if (agent && agent !== lastAgent) {
            blocks.push(
                `<div class="trace-agent-divider"><span class="label">Agent: ${_escapeHtml(agent)}</span></div>`
            );
            lastAgent = agent;
        }
        const trunc = _truncate(content, 500);
        const id = `trace_msg_${idx}`;
        const body = `<div class="trace-bubble-body" id="${id}">${_escapeHtml(trunc.text)}${trunc.truncated ? '…' : ''}</div>`;
        const expand = trunc.truncated
            ? `<div class="trace-expand" onclick="toggleTraceMsg('${id}', ${idx})">Expand</div>`
            : '';
        blocks.push(`
          <div class="trace-bubble ${cls}">
            <div class="trace-bubble-header muted">
              <span class="mono">${_escapeHtml(role)}</span>
              ${agent ? `<span>${_escapeHtml(agent)}</span>` : ''}
              ${owner ? `<span>${_escapeHtml(owner)}</span>` : ''}
              ${spanId ? `<span class="mono">${_escapeHtml(spanId)}</span>` : ''}
            </div>
            <div class="trace-bubble-subheader">
              <span class="k">Kind</span><span class="v mono">${_escapeHtml(kind)}</span>
              <span class="k">Name</span><span class="v">${_escapeHtml(parentName || '')}</span>
              ${model ? `<span class="k">Model</span><span class="v mono">${_escapeHtml(model)}</span>` : ''}
              <span class="k">Start</span><span class="v mono">${_escapeHtml(startFmt)}</span>
              <span class="k">Dur</span><span class="v mono">${_escapeHtml(dur)}</span>
              <span class="k">Status</span><span class="v mono">${_escapeHtml(status)}</span>
            </div>
            ${body}
            ${expand}
          </div>
        `);
    });
    // Store full items for expand/collapse
    window.__traceChatItems = items;
    window.__traceChatExpanded = window.__traceChatExpanded || {};
    chatEl.innerHTML = blocks.join('');
    traceChatLastCount = items.length;
    scrollToBottom('trace-chat');
}

function appendTraceChat(items) {
    const chatEl = document.getElementById('trace-chat');
    if (!chatEl) return;
    if (!items || items.length === 0) {
        return; // No new messages
    }
    
    // Only append new messages (after traceChatLastCount)
    if (items.length <= traceChatLastCount) {
        return; // No new messages
    }
    
    const newItems = items.slice(traceChatLastCount);
    if (newItems.length === 0) {
        return;
    }
    
    // Get the last agent from existing items to check if we need a divider
    const existingItems = window.__traceChatItems || [];
    let lastAgent = null;
    if (existingItems.length > 0) {
        const lastItem = existingItems[existingItems.length - 1];
        lastAgent = lastItem.agent_name || null;
    }
    
    const blocks = [];
    newItems.forEach((it, relativeIdx) => {
        const idx = traceChatLastCount + relativeIdx;
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
        const dur = _fmtDurMs(startTs, endTs);
        const startFmt = _fmtTs(startTs);
        const parentKind = it.parent_kind || '';
        const parentName = it.parent_name || '';
        const owner = (parentKind || parentName) ? `${parentKind}${parentName ? ' ' + parentName : ''}` : '';
        const model = (kind === 'llm.call') ? (spanName || '') : '';
        
        // Add agent divider if agent changed
        if (agent && agent !== lastAgent) {
            blocks.push(
                `<div class="trace-agent-divider"><span class="label">Agent: ${_escapeHtml(agent)}</span></div>`
            );
            lastAgent = agent;
        }
        
        const trunc = _truncate(content, 500);
        const id = `trace_msg_${idx}`;
        const body = `<div class="trace-bubble-body" id="${id}">${_escapeHtml(trunc.text)}${trunc.truncated ? '…' : ''}</div>`;
        const expand = trunc.truncated
            ? `<div class="trace-expand" onclick="toggleTraceMsg('${id}', ${idx})">Expand</div>`
            : '';
        blocks.push(`
          <div class="trace-bubble ${cls}">
            <div class="trace-bubble-header muted">
              <span class="mono">${_escapeHtml(role)}</span>
              ${agent ? `<span>${_escapeHtml(agent)}</span>` : ''}
              ${owner ? `<span>${_escapeHtml(owner)}</span>` : ''}
              ${spanId ? `<span class="mono">${_escapeHtml(spanId)}</span>` : ''}
            </div>
            <div class="trace-bubble-subheader">
              <span class="k">Kind</span><span class="v mono">${_escapeHtml(kind)}</span>
              <span class="k">Name</span><span class="v">${_escapeHtml(parentName || '')}</span>
              ${model ? `<span class="k">Model</span><span class="v mono">${_escapeHtml(model)}</span>` : ''}
              <span class="k">Start</span><span class="v mono">${_escapeHtml(startFmt)}</span>
              <span class="k">Dur</span><span class="v mono">${_escapeHtml(dur)}</span>
              <span class="k">Status</span><span class="v mono">${_escapeHtml(status)}</span>
            </div>
            ${body}
            ${expand}
          </div>
        `);
    });
    
    // Append new blocks to existing content
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = blocks.join('');
    while (tempDiv.firstChild) {
        chatEl.appendChild(tempDiv.firstChild);
    }
    
    // Update stored items and count
    window.__traceChatItems = items;
    traceChatLastCount = items.length;
    
    // Auto-scroll to bottom
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
        const trunc = _truncate(full, 500);
        el.textContent = trunc.text + '…';
        window.__traceChatExpanded[domId] = false;
        // update link text
        const link = el.parentElement && el.parentElement.querySelector('.trace-expand');
        if (link) link.textContent = 'Expand';
    } else {
        el.textContent = full;
        window.__traceChatExpanded[domId] = true;
        const link = el.parentElement && el.parentElement.querySelector('.trace-expand');
        if (link) link.textContent = 'Collapse';
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

    // Build parent->children map
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

    // Roots: parent missing or null/empty
    const roots = spans
        .filter(s => !s.parent_span_id || !byId[s.parent_span_id])
        .sort((a, b) => (a.start_ts || 0) - (b.start_ts || 0))
        .map(s => s.span_id);

    function renderNode(spanId, depth) {
        const s = byId[spanId];
        const kids = children[spanId] || [];
        const dur = _fmtDurMs(s.start_ts, s.end_ts);
        const isLlm = (s.kind === 'llm.call');
        // IMPORTANT: do not HTML-escape values used inside inline JS string literals.
        // HTML entities are decoded by the browser before JS executes, which can reintroduce quotes and break syntax.
        const safeSpanId = encodeURIComponent(String(spanId || ''));
        const btn = isLlm ? `<button class="mini-btn" onclick="event.stopPropagation();selectLlmSpan('${safeSpanId}')">LLM</button>` : '';
        const line = `
          <div class="span-line" onclick="selectSpan('${safeSpanId}')">
            <span class="mono">${'&nbsp;'.repeat(depth * 2)}${_escapeHtml(s.kind || '')}</span>
            <span class="span-name">${_escapeHtml(s.name || '')}</span>
            <span class="muted">${_escapeHtml(dur)}</span>
            <span class="muted">${_escapeHtml(s.status || '')}</span>
            ${btn}
          </div>
        `;
        const childHtml = kids.map(k => renderNode(k, depth + 1)).join('');
        return line + childHtml;
    }

    const treeHtml = roots.map(r => renderNode(r, 0)).join('');

    detailEl.innerHTML = `
      <div class="trace-meta">
        <div><span class="muted">request_id</span>: <span class="mono">${_escapeHtml(tr.request_id || '')}</span></div>
        <div class="muted">root_agent=${_escapeHtml(tr.root_agent || '')} root_kind=${_escapeHtml(tr.root_kind || '')} status=${_escapeHtml(tr.status || '')} started_at=${_escapeHtml(tr.started_at || '')}</div>
      </div>
      <div>
        <div class="trace-subtitle">Span tree</div>
        <div class="span-tree">${treeHtml || '<div class="muted">No spans.</div>'}</div>
      </div>
    `;
}

async function selectLlmSpan(spanId) {
    if (!currentProject) return;
    // spanId may be URL-encoded when coming from inline onclick handlers.
    try { spanId = decodeURIComponent(String(spanId || '')); } catch { spanId = String(spanId || ''); }
    selectedLlmSpanId = spanId;
    // Stop polling when viewing a specific span (different view mode)
    if (traceChatPollInterval) {
        clearInterval(traceChatPollInterval);
        traceChatPollInterval = null;
    }
    traceChatLastCount = 0;
    const llmEl = document.getElementById('trace-chat');
    if (!llmEl) return;
    llmEl.innerHTML = 'Loading LLM messages…';
    try {
        const url = '/api/projects/' + encodeURIComponent(currentProject) + '/spans/' + encodeURIComponent(spanId) + '/llm_messages';
        const resp = await fetch(url);
        const msgs = await resp.json();
        const out = [];
        for (const m of (msgs || [])) {
            const role = (m && m.role) ? String(m.role) : '';
            const nm = (m && m.name) ? (' (' + String(m.name) + ')') : '';
            const content = (m && (m.content !== null && m.content !== undefined)) ? String(m.content) : '';
            out.push('=== ' + role + nm + ' ===\\n' + content + '\\n');
        }
        const joined = out.join('\\n');
        llmEl.innerHTML =
            '<div class="trace-subtitle">LLM messages for span <span class="mono">' +
            _escapeHtml(spanId) +
            '</span></div><pre class="mono pre-wrap">' +
            _escapeHtml(joined) +
            '</pre>';
        scrollToBottom('trace-chat');
    } catch (e) {
        llmEl.innerHTML = '<div class="error">Failed to load LLM messages: ' + _escapeHtml(e) + '</div>';
    }
}

function selectSpan(spanId) {
    // spanId may be URL-encoded from inline onclick handlers.
    try { spanId = decodeURIComponent(String(spanId || '')); } catch { spanId = String(spanId || ''); }
    // currently a no-op placeholder (could be used to highlight in timeline/tree)
    return;
}

// Load agents for current project
async function loadAgents() {
    const tbody = document.getElementById('agents-tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="loading">Loading agents...</td></tr>';

    try {
        const response = await fetch(`/api/projects/${currentProject}/agents`);
        const agents = await response.json();

        if (agents.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="loading">No agents found</td></tr>';
            return;
        }

        tbody.innerHTML = '';
        agents.forEach(agent => {
            const row = document.createElement('tr');
            const statusClass = `status-${agent.status}`;
            const statusIcon = agent.status === 'running' ? '🟢' :
                              agent.status === 'error' ? '🔴' : '⚪';

            row.innerHTML = `
                <td>${agent.name}</td>
                <td class="${statusClass}">${statusIcon} ${agent.status}</td>
                <td class="container-name">${agent.container || ''}</td>
                <td class="interactive-mode">${agent.interactive_mode || '-'}</td>
                <td>${agent.url ? `<a href="${agent.url}" target="_blank" class="agent-link">Open</a>` : '-'}</td>
            `;
            tbody.appendChild(row);
        });
    } catch (error) {
        console.error('Failed to load agents:', error);
        tbody.innerHTML = '<tr><td colspan="5" class="loading">Error loading agents</td></tr>';
    }
}

// Load available log files
async function loadLogFiles() {
    try {
        const response = await fetch(`/api/projects/${currentProject}/logs/files`);
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

// Load available chat files
async function loadChatFiles() {
    try {
        const response = await fetch(`/api/projects/${currentProject}/chat/files`);
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

// Handle log file selection change
function onLogFileChange() {
    const dropdown = document.getElementById('log-file-dropdown');
    const file = dropdown.value;
    
    if (file === 'live') {
        switchToLiveLogs();
    } else {
        loadHistoricalLogs(file);
    }
}

// Handle chat file selection change
function onChatFileChange() {
    const dropdown = document.getElementById('chat-file-dropdown');
    const file = dropdown.value;
    
    if (file === 'live') {
        switchToLiveChat();
    } else {
        loadHistoricalChat(file);
    }
}

// Switch to live log streaming
function switchToLiveLogs() {
    logViewMode = 'live';
    document.getElementById('log-file-dropdown').value = 'live';
    
    // Render all buffered logs
    const container = document.getElementById('log-entries');
    container.innerHTML = '';
    streamedLogs.forEach(entry => appendLogEntry(entry));
    
    // Reconnect stream if needed
    if (!logEventSource || logEventSource.readyState === EventSource.CLOSED) {
        connectLogStream();
    }
    
    scrollToBottom('log-container');
}

// Switch to live chat streaming
function switchToLiveChat() {
    chatViewMode = 'live';
    document.getElementById('chat-file-dropdown').value = 'live';

    // Reset pending system message
    pendingSystemMessage = null;
    
    // Process and render all buffered messages
    const processed = processMessagesForDisplay(streamedMessages);
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';
    processed.forEach(msg => {
        const div = createChatMessageElement(msg);
        container.appendChild(div);
    });

    // Reconnect stream if needed
    if (!chatEventSource || chatEventSource.readyState === EventSource.CLOSED) {
        connectChatStream();
    }

    scrollToBottom('chat-container');
}

// Load historical logs
async function loadHistoricalLogs(filename) {
    logViewMode = 'historical';
    
    try {
        const response = await fetch(`/api/projects/${currentProject}/logs?file=${filename}`);
        const logs = await response.json();
        
        const container = document.getElementById('log-entries');
        container.innerHTML = '';
        
        logs.forEach(entry => {
            const div = createLogEntryElement(entry);
            container.appendChild(div);
        });
        
        scrollToBottom('log-container');
    } catch (error) {
        console.error('Failed to load historical logs:', error);
    }
}

// Load historical chat
async function loadHistoricalChat(filename) {
    chatViewMode = 'historical';
    
    try {
        const response = await fetch(`/api/projects/${currentProject}/chat?file=${filename}`);
        const messages = await response.json();
        
        // Process messages to group system messages with following messages
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

// Connect to log SSE stream
function connectLogStream() {
    if (logEventSource) {
        logEventSource.close();
    }
    
    if (!currentProject) return;
    
    logEventSource = new EventSource(`/api/projects/${currentProject}/logs/stream`);
    
    logEventSource.onmessage = function(event) {
        const entry = JSON.parse(event.data);
        
        // Always add to buffer
        streamedLogs.push(entry);
        
        // Only append to UI if in live mode
        if (logViewMode === 'live') {
            appendLogEntry(entry);
            scrollToBottom('log-container');
        }
    };
    
    logEventSource.onerror = function(error) {
        console.error('Log stream error:', error);
        // Try to reconnect after 5 seconds
        setTimeout(() => {
            if (currentProject && logViewMode === 'live') {
                connectLogStream();
            }
        }, 5000);
    };
}

// Connect to chat SSE stream
function connectChatStream() {
    if (chatEventSource) {
        chatEventSource.close();
    }
    
    if (!currentProject) return;
    
    chatEventSource = new EventSource(`/api/projects/${currentProject}/chat/stream`);
    
    chatEventSource.onmessage = function(event) {
        const message = JSON.parse(event.data);
        
        // Always add to buffer
        streamedMessages.push(message);
        
        // Only append to UI if in live mode
        if (chatViewMode === 'live') {
            appendChatMessage(message);
            scrollToBottom('chat-container');
        }
    };
    
    chatEventSource.onerror = function(error) {
        console.error('Chat stream error:', error);
        // Try to reconnect after 5 seconds
        setTimeout(() => {
            if (currentProject && chatViewMode === 'live') {
                connectChatStream();
            }
        }, 5000);
    };
}

// Append a log entry to the UI
function appendLogEntry(entry) {
    const container = document.getElementById('log-entries');
    const div = createLogEntryElement(entry);
    container.appendChild(div);
}

// Create a log entry element
function createLogEntryElement(entry) {
    const div = document.createElement('div');
    div.className = `log-entry ${entry.level || 'INFO'}`;
    
    const time = entry.time ? entry.time.split('T').pop().split('.')[0] : '';
    const level = entry.level || 'INFO';
    const message = entry.message || JSON.stringify(entry);
    
    div.innerHTML = `<span class="log-time">${time}</span><span class="log-level">[${level}]</span>${escapeHtml(message)}`;
    
    return div;
}

// Append a chat message to the UI
function appendChatMessage(message) {
    const type = message.type || 'unknown';
    
    // If it's a system message, store it to attach to next message
    if (type === 'system') {
        pendingSystemMessage = message;
        return; // Don't render standalone system message
    }

    // Hide tool messages (tool result messages only)
    if (hideToolMessages && type === 'tool') {
        // Do NOT clear pendingSystemMessage; attach it to next non-tool message.
        return;
    }
    
    // Attach pending system message if exists
    if (pendingSystemMessage) {
        message.systemMessage = pendingSystemMessage;
        pendingSystemMessage = null;
    }
    
    const container = document.getElementById('chat-messages');
    const div = createChatMessageElement(message);
    container.appendChild(div);
}

// Process messages for display (group system messages with following messages)
function processMessagesForDisplay(messages) {
    const processed = [];
    let pendingSystem = null;
    
    for (const msg of messages) {
        const type = msg.type || 'unknown';
        
        if (type === 'system') {
            pendingSystem = msg;
        } else if (hideToolMessages && type === 'tool') {
            // Skip tool messages but keep pending system for next non-tool message.
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

// Create a chat message element
function createChatMessageElement(message) {
    const type = message.type || 'unknown';
    const rawContent = message.content;
    const timestamp = message.timestamp || '';
    
    const div = document.createElement('div');
    div.className = `message ${type}`;
    
    // Avatar
    const avatarIcons = {
        'human': '👤',
        'ai': '🤖',
        'tool': '🔧'
    };
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = avatarIcons[type] || '❓';
    
    // Content wrapper
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    // Header with type label and timestamp
    const headerDiv = document.createElement('div');
    headerDiv.className = 'message-header';

    const typeLabel = document.createElement('span');
    typeLabel.className = 'message-type-label';
    // For AI messages, show agent name and/or model name
    if (type === 'ai') {
        if (message.agent_name && message.model_name) {
            // Both available: "Agent Name (Model Name)"
            typeLabel.textContent = `${message.agent_name} (${message.model_name})`;
        } else if (message.agent_name) {
            // Only agent name
            typeLabel.textContent = message.agent_name;
        } else if (message.model_name) {
            // Only model name
            typeLabel.textContent = message.model_name;
        } else {
            typeLabel.textContent = 'AI';
        }
    } else if (type === 'tool') {
        // Show tool name for tool result messages.
        // Tool call id is displayed separately below; don't use it as a "name".
        typeLabel.textContent = message.tool_name || 'TOOL';
    } else {
        typeLabel.textContent = type.toUpperCase();
    }
    headerDiv.appendChild(typeLabel);
    
    // Timestamp on right
    if (timestamp) {
        const timeLabel = document.createElement('span');
        timeLabel.className = 'message-timestamp';
        const timeStr = timestamp.includes('T') 
            ? timestamp.split('T')[1].split('.')[0] 
            : timestamp;
        timeLabel.textContent = timeStr;
        headerDiv.appendChild(timeLabel);
    }
    
    contentDiv.appendChild(headerDiv);
    
    // Embedded System Message (expandable)
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
    
    // Parse and display content
    const { textContent, toolUses } = parseMessageContent(rawContent);
    
    // Display text content (truncate to first 150 words with expand/collapse)
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
    
    // Display tool uses - prefer content array's tool_use, fallback to tool_calls field
    // (They often contain the same data, so only show one)
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
    
    // Tool call ID (for tool messages)
    if (message.tool_call_id) {
        const tcIdDiv = document.createElement('div');
        tcIdDiv.className = 'tool-call-id';
        tcIdDiv.textContent = `Tool call id: ${message.tool_call_id}`;
        contentDiv.appendChild(tcIdDiv);
    }

    // Cost/tokens footer (for AI responses)
    const usage = message.usage || message.usage_metadata || null;
    const cost = (typeof message.cost === 'number') ? message.cost : null;
    if (cost !== null || usage) {
        const it = usage && (usage.input_tokens ?? usage.input ?? 0);
        const ot = usage && (usage.output_tokens ?? usage.output ?? 0);
        const tt = usage && (usage.total_tokens ?? usage.total ?? ((it || 0) + (ot || 0)));
        const footer = document.createElement('div');
        footer.className = 'message-footer';
        const parts = [];
        if (cost !== null) parts.push(`Cost: $${cost.toFixed(6)}`);
        if (usage) parts.push(`Tokens: in ${it || 0}, out ${ot || 0} (${tt || 0})`);
        footer.textContent = parts.join(' | ');
        contentDiv.appendChild(footer);
    }
    
    div.appendChild(avatar);
    div.appendChild(contentDiv);
    
    return div;
}

async function loadCostSummary() {
    if (!currentProject) return;
    try {
        const resp = await fetch(`/api/projects/${currentProject}/llm/usage_summary`);
        const data = await resp.json();
        renderCostSummary(data);
    } catch (e) {
        const totalsEl = document.getElementById('cost-totals');
        if (totalsEl) totalsEl.textContent = 'Failed to load cost summary.';
    }
}

function _agentColor(agent) {
    // deterministic hue hash
    let h = 0;
    for (let i = 0; i < (agent || '').length; i++) h = (h * 31 + agent.charCodeAt(i)) >>> 0;
    const hue = h % 360;
    return `hsl(${hue}, 70%, 55%)`;
}

function renderCostSummary(data) {
    const totalsEl = document.getElementById('cost-totals');
    const tbody = document.getElementById('cost-by-agent-tbody');
    if (!totalsEl || !tbody) return;

    const totals = (data && data.totals) ? data.totals : { cost: 0, tokens: { input: 0, output: 0, total: 0 } };
    const tokens = totals.tokens || { input: 0, output: 0, total: 0 };
    totalsEl.textContent = `Total cost: $${Number(totals.cost || 0).toFixed(6)}\n` +
                           `Tokens: in ${tokens.input || 0}, out ${tokens.output || 0} (total ${tokens.total || 0})`;

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
            row.innerHTML = `
                <td>${escapeHtml(agent)}</td>
                <td>$${Number(a.cost || 0).toFixed(6)}</td>
                <td>${t.input || 0}</td>
                <td>${t.output || 0}</td>
                <td>${t.total || 0}</td>
            `;
            tbody.appendChild(row);
        }
    }

    const perDay = Array.isArray(data && data.per_day) ? data.per_day : [];
    renderStackedChart('chart-cost', 'chart-legend-cost', perDay, 'cost');
    renderStackedChart('chart-tokens', 'chart-legend-tokens', perDay, 'tokens');
}

function renderStackedChart(containerId, legendId, perDay, kind) {
    const container = document.getElementById(containerId);
    const legend = document.getElementById(legendId);
    if (!container || !legend) return;

    // Collect agents
    const agentSet = new Set();
    for (const d of perDay) {
        const by = (d && d.by_agent) ? d.by_agent : {};
        for (const a of Object.keys(by)) agentSet.add(a);
    }
    const agents = Array.from(agentSet).sort();

    // Legend
    legend.innerHTML = '';
    for (const a of agents) {
        const item = document.createElement('div');
        item.className = 'legend-item';
        const sw = document.createElement('div');
        sw.className = 'legend-swatch';
        sw.style.background = _agentColor(a);
        item.appendChild(sw);
        const label = document.createElement('span');
        label.textContent = a;
        item.appendChild(label);
        legend.appendChild(item);
    }

    // Max for scaling
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
            seg.style.background = _agentColor(a);
            seg.style.width = `${(val / maxTotal) * 100}%`;
            bar.appendChild(seg);
        }

        const valueEl = document.createElement('div');
        valueEl.className = 'chart-value';
        valueEl.textContent = kind === 'cost' ? `$${total.toFixed(3)}` : `${Math.round(total)}`;

        row.appendChild(dateEl);
        row.appendChild(bar);
        row.appendChild(valueEl);
        container.appendChild(row);
    });
}

// Parse message content - handles string or array format
function parseMessageContent(content) {
    let textContent = '';
    let toolUses = [];
    
    if (!content) {
        return { textContent: '', toolUses: [] };
    }
    
    // If content is a string
    if (typeof content === 'string') {
        return { textContent: content, toolUses: [] };
    }
    
    // If content is an array (e.g., Anthropic format with text and tool_use)
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
        
        textContent = textParts.join('\\n');
    }
    
    // If content is an object (might be JSON)
    else if (typeof content === 'object') {
        textContent = JSON.stringify(content, null, 2);
    }
    
    return { textContent, toolUses };
}

// Parse markdown content to extract code blocks
function parseMarkdownContent(text) {
    if (!text || typeof text !== 'string') {
        return [{ type: 'text', content: text || '' }];
    }
    
    const parts = [];

    const codeBlockRegex = /```(\\w*)\\n([\\s\\S]*?)```/g;
    let lastIndex = 0;
    let match;
    
    while ((match = codeBlockRegex.exec(text)) !== null) {
        // Add text before this code block
        if (match.index > lastIndex) {
            const textBefore = text.slice(lastIndex, match.index).trim();
            if (textBefore) {
                parts.push({ type: 'text', content: textBefore });
            }
        }
        
        // Add the code block
        const lang = match[1] || '';
        const code = match[2];
        parts.push({ type: 'code', lang: lang, content: code });
        
        lastIndex = match.index + match[0].length;
    }
    
    // Add remaining text after last code block
    if (lastIndex < text.length) {
        const remaining = text.slice(lastIndex).trim();
        if (remaining) {
            parts.push({ type: 'text', content: remaining });
        }
    }
    
    // If no code blocks found, return original text
    if (parts.length === 0) {
        parts.push({ type: 'text', content: text });
    }
    
    return parts;
}

// Check if a string looks like JSON
function isJsonString(str) {
    if (typeof str !== 'string') return false;
    const trimmed = str.trim();
    return (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
           (trimmed.startsWith('[') && trimmed.endsWith(']'));
}

// Helper: Scroll container to bottom
function scrollToBottom(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

// Helper: Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
'''

