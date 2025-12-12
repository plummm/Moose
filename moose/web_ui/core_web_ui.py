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
                </div>
            </header>
            
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
}

.section-controls select {
    padding: 5px 10px;
    border-radius: 4px;
    border: 1px solid var(--border-color);
    background: var(--bg-tertiary);
    color: var(--text-primary);
    font-size: 12px;
    cursor: pointer;
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

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    loadProjects();
    initResizeHandle();
});

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
async function loadProjects() {
    try {
        const response = await fetch('/api/projects');
        const projects = await response.json();
        
        const dropdown = document.getElementById('project-dropdown');
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
        
        // Select first project
        currentProject = projects[0];
        loadProjectData();
    } catch (error) {
        console.error('Failed to load projects:', error);
        document.getElementById('project-dropdown').innerHTML = 
            '<option value="">Error loading projects</option>';
    }
}

// Handle project change
function onProjectChange() {
    const dropdown = document.getElementById('project-dropdown');
    currentProject = dropdown.value;
    
    // Clear buffers when switching projects
    streamedLogs = [];
    streamedMessages = [];
    
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
    
    // Display text content
    if (textContent) {
        const textDiv = document.createElement('div');
        textDiv.className = 'message-text';

        // Parse markdown to extract code blocks
        const contentParts = parseMarkdownContent(textContent);
        
        for (const part of contentParts) {
            if (part.type === 'code') {
                // Code block - check if it's JSON and pretty-print
                const codeBlock = document.createElement('pre');
                codeBlock.className = 'json-block';
                
                // Try to parse and pretty-print JSON
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
                textDiv.appendChild(codeBlock);
            } else {
                // Regular text - check if entire text is JSON
                if (isJsonString(part.content)) {
                    try {
                        const jsonObj = JSON.parse(part.content);
                        const jsonBlock = document.createElement('pre');
                        jsonBlock.className = 'json-block';
                        jsonBlock.textContent = JSON.stringify(jsonObj, null, 2);
                        textDiv.appendChild(jsonBlock);
                    } catch {
                        const textSpan = document.createElement('span');
                        textSpan.textContent = part.content;
                        textDiv.appendChild(textSpan);
                    }
                } else {
                    const textSpan = document.createElement('span');
                    textSpan.textContent = part.content;
                    textDiv.appendChild(textSpan);
                }
            }
        }
        contentDiv.appendChild(textDiv);
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
        tcIdDiv.textContent = `Tool: ${message.tool_name || message.tool_call_id}`;
        contentDiv.appendChild(tcIdDiv);
    }
    
    div.appendChild(avatar);
    div.appendChild(contentDiv);
    
    return div;
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
