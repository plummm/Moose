"""Web UI for agent HTTP server.

Provides a web interface for viewing agent information, endpoints, and logs.
"""

from typing import Dict, Any, List
try:
    from flask import render_template_string, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


def get_moose_ascii_art() -> str:
    """Generate ASCII art for MOOSE title."""
    return """
    ███╗   ███╗ ██████╗  ██████╗ ███████╗███████╗
    ████╗ ████║██╔═══██╗██╔═══██╗██╔════╝██╔════╝
    ██╔████╔██║██║   ██║██║   ██║███████╗█████╗  
    ██║╚██╔╝██║██║   ██║██║   ██║╚════██║██╔══╝  
    ██║ ╚═╝ ██║╚██████╔╝╚██████╔╝███████║███████╗
    ╚═╝     ╚═╝ ╚═════╝  ╚═════╝ ╚══════╝╚══════╝
    """


def generate_homepage_html(
    agent_name: str,
    agent_description: str,
    agent_version: str,
    endpoints: List[Dict[str, Any]],
    http_port: int,
    auth_enabled: bool
) -> str:
    """Generate HTML for agent homepage."""
    
    moose_ascii = get_moose_ascii_art()
    
    # Generate endpoints table rows
    endpoints_html = ""
    for endpoint in endpoints:
        path = endpoint.get('path', '')
        method = endpoint.get('method', 'GET').upper()
        description = endpoint.get('description', '')
        auth_required = endpoint.get('auth_required', False)
        
        # Determine if this is a custom endpoint or standard
        is_custom = path not in ['/health', '/']
        
        # Generate view button
        view_button = f'<a href="{path}" class="btn btn-view" target="_blank">View</a>'
        
        auth_badge = '<span class="badge badge-auth">Auth Required</span>' if auth_required else ''
        
        endpoints_html += f'''
        <tr>
            <td><code>{method}</code></td>
            <td><code>{path}</code></td>
            <td>{description or '-'}</td>
            <td>{auth_badge}</td>
            <td>{view_button}</td>
        </tr>
        '''
        
        # Generate POST form if method is POST (as a separate row)
        if method == 'POST' and is_custom:
            safe_id = path.replace('/', '-').replace('_', '-').replace(' ', '-').strip('-')
            post_form = f'''
        <tr class="post-form-row">
            <td colspan="5">
                <div class="post-form" id="form-{safe_id}">
                    <h4>POST Parameters for <code>{path}</code></h4>
                    <textarea id="params-{safe_id}" 
                              placeholder='Enter JSON parameters, e.g. {{"key": "value"}}'
                              rows="4"></textarea>
                    <button onclick="sendPost('{path}', '{safe_id}')" 
                            class="btn btn-send">Send POST Request</button>
                    <div id="result-{safe_id}" class="result-area"></div>
                </div>
            </td>
        </tr>
            '''
            endpoints_html += post_form
    
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{agent_name} - Moose Agent</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Courier New', monospace;
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 20px;
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: #252526;
            border-radius: 8px;
            border: 1px solid #3e3e42;
        }}
        
        .ascii-art {{
            color: #4ec9b0;
            font-size: 12px;
            line-height: 1.2;
            margin-bottom: 20px;
            white-space: pre;
            font-family: 'Courier New', monospace;
        }}
        
        .agent-info {{
            background: #252526;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            border: 1px solid #3e3e42;
        }}
        
        .agent-info h2 {{
            color: #4ec9b0;
            margin-bottom: 15px;
            border-bottom: 2px solid #3e3e42;
            padding-bottom: 10px;
        }}
        
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        
        .info-item {{
            background: #1e1e1e;
            padding: 10px;
            border-radius: 4px;
            border: 1px solid #3e3e42;
        }}
        
        .info-label {{
            color: #858585;
            font-size: 0.9em;
            margin-bottom: 5px;
        }}
        
        .info-value {{
            color: #d4d4d4;
            font-weight: bold;
        }}
        
        .section {{
            background: #252526;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            border: 1px solid #3e3e42;
        }}
        
        .section h2 {{
            color: #4ec9b0;
            margin-bottom: 15px;
            border-bottom: 2px solid #3e3e42;
            padding-bottom: 10px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        
        th {{
            background: #1e1e1e;
            color: #4ec9b0;
            padding: 12px;
            text-align: left;
            border-bottom: 2px solid #3e3e42;
        }}
        
        td {{
            padding: 12px;
            border-bottom: 1px solid #3e3e42;
        }}
        
        tr:hover {{
            background: #2d2d30;
        }}
        
        code {{
            background: #1e1e1e;
            padding: 2px 6px;
            border-radius: 3px;
            color: #ce9178;
            font-family: 'Courier New', monospace;
        }}
        
        .btn {{
            padding: 6px 12px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            font-size: 0.9em;
            transition: all 0.3s;
        }}
        
        .btn-view {{
            background: #0e639c;
            color: white;
        }}
        
        .btn-view:hover {{
            background: #1177bb;
        }}
        
        .btn-send {{
            background: #4ec9b0;
            color: #1e1e1e;
            margin-top: 10px;
        }}
        
        .btn-send:hover {{
            background: #5ed9c0;
        }}
        
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 0.8em;
            font-weight: bold;
        }}
        
        .badge-auth {{
            background: #f48771;
            color: #1e1e1e;
        }}
        
        .post-form-row {{
            background: #1e1e1e;
        }}
        
        .post-form-row:hover {{
            background: #1e1e1e;
        }}
        
        .post-form {{
            margin-top: 15px;
            padding: 15px;
            background: #1e1e1e;
            border-radius: 4px;
            border: 1px solid #3e3e42;
        }}
        
        .post-form h4 {{
            color: #4ec9b0;
            margin-bottom: 10px;
        }}
        
        textarea {{
            width: 100%;
            background: #1e1e1e;
            color: #d4d4d4;
            border: 1px solid #3e3e42;
            border-radius: 4px;
            padding: 10px;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
            resize: vertical;
        }}
        
        textarea:focus {{
            outline: none;
            border-color: #4ec9b0;
        }}
        
        .result-area {{
            margin-top: 15px;
            padding: 10px;
            background: #1e1e1e;
            border-radius: 4px;
            border: 1px solid #3e3e42;
            max-height: 300px;
            overflow-y: auto;
            display: none;
        }}
        
        .result-area.show {{
            display: block;
        }}
        
        .result-area pre {{
            color: #d4d4d4;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        
        .logs-section {{
            max-height: 400px;
            overflow-y: auto;
            background: #1e1e1e;
            padding: 15px;
            border-radius: 4px;
            border: 1px solid #3e3e42;
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
        }}
        
        .log-entry {{
            margin-bottom: 5px;
            padding: 5px;
            border-left: 3px solid #3e3e42;
            padding-left: 10px;
        }}
        
        .log-entry.info {{
            border-left-color: #4ec9b0;
        }}
        
        .log-entry.warning {{
            border-left-color: #f48771;
        }}
        
        .log-entry.error {{
            border-left-color: #f48771;
        }}
        
        .log-time {{
            color: #858585;
            margin-right: 10px;
        }}
        
        .refresh-btn {{
            background: #0e639c;
            color: white;
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin-bottom: 10px;
        }}
        
        .refresh-btn:hover {{
            background: #1177bb;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="ascii-art">{moose_ascii}</div>
            <h1 style="color: #4ec9b0; margin-top: 20px;">Agent Dashboard</h1>
        </div>
        
        <div class="agent-info">
            <h2>Agent Information</h2>
            <div class="info-grid">
                <div class="info-item">
                    <div class="info-label">Name</div>
                    <div class="info-value">{agent_name}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Description</div>
                    <div class="info-value">{agent_description or 'N/A'}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Version</div>
                    <div class="info-value">{agent_version or 'N/A'}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Port</div>
                    <div class="info-value">{http_port}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Authentication</div>
                    <div class="info-value">{'Enabled' if auth_enabled else 'Disabled'}</div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>Available Endpoints</h2>
            <table>
                <thead>
                    <tr>
                        <th>Method</th>
                        <th>Path</th>
                        <th>Description</th>
                        <th>Auth</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {endpoints_html}
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <h2>Agent Logs</h2>
            <button class="refresh-btn" onclick="refreshLogs()">Refresh Logs</button>
            <div class="logs-section" id="logs-container">
                <div class="log-entry info">
                    <span class="log-time">[Loading...]</span>
                    <span>Click refresh to load logs</span>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        function sendPost(path, formId) {{
            const paramsTextarea = document.getElementById('params-' + formId);
            const resultDiv = document.getElementById('result-' + formId);
            const paramsText = paramsTextarea.value.trim();
            
            let params = {{}};
            if (paramsText) {{
                try {{
                    params = JSON.parse(paramsText);
                }} catch (e) {{
                    resultDiv.innerHTML = '<pre style="color: #f48771;">Error: Invalid JSON\\n' + e.message + '</pre>';
                    resultDiv.classList.add('show');
                    return;
                }}
            }}
            
            resultDiv.innerHTML = '<pre style="color: #4ec9b0;">Sending request...</pre>';
            resultDiv.classList.add('show');
            
            fetch(path, {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify(params)
            }})
            .then(response => response.json())
            .then(data => {{
                resultDiv.innerHTML = '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
            }})
            .catch(error => {{
                resultDiv.innerHTML = '<pre style="color: #f48771;">Error: ' + error.message + '</pre>';
            }});
        }}
        
        function refreshLogs() {{
            const logsContainer = document.getElementById('logs-container');
            logsContainer.innerHTML = '<div class="log-entry info"><span class="log-time">[Loading...]</span><span>Fetching logs...</span></div>';
            
            fetch('/logs')
            .then(response => response.json())
            .then(data => {{
                if (data.logs && data.logs.length > 0) {{
                    logsContainer.innerHTML = data.logs.map(log => {{
                        const level = log.level || 'info';
                        const time = log.time || '';
                        const message = log.message || '';
                        return `<div class="log-entry ${{level}}"><span class="log-time">[${{time}}]</span><span>${{message}}</span></div>`;
                    }}).join('');
                }} else {{
                    logsContainer.innerHTML = '<div class="log-entry info"><span class="log-time">[No logs]</span><span>No log entries available</span></div>';
                }}
            }})
            .catch(error => {{
                logsContainer.innerHTML = '<div class="log-entry error"><span class="log-time">[Error]</span><span>Failed to fetch logs: ' + error.message + '</span></div>';
            }});
        }}
        
        // Auto-refresh logs every 5 seconds
        setInterval(refreshLogs, 5000);
        
        // Load logs on page load
        window.onload = function() {{
            refreshLogs();
        }};
    </script>
</body>
</html>
    """
    
    return html


def get_endpoints_list(agent) -> List[Dict[str, Any]]:
    """Get list of all available endpoints for the agent."""
    endpoints = []
    
    # Add standard endpoints
    endpoints.append({
        'path': '/health',
        'method': 'GET',
        'description': 'Health check endpoint',
        'auth_required': False
    })
    
    endpoints.append({
        'path': '/',
        'method': 'GET',
        'description': 'Agent dashboard homepage',
        'auth_required': False
    })
    
    # Add custom endpoints from config
    if hasattr(agent, 'http_endpoints') and agent.http_endpoints:
        endpoints.extend(agent.http_endpoints)
    
    return endpoints

