"""Core Web Server for Moose Framework.

Singleton Flask server that handles multiple projects with:
- Main dashboard page
- API endpoints for projects, agents, logs, and chat
- SSE streaming for real-time updates
"""

import json
import threading
import errno
import sqlite3
import os
from typing import Dict, List, Optional, Set
from pathlib import Path

try:
    from flask import Flask, Response, jsonify, request, send_from_directory
except ImportError:
    raise ImportError("Flask is required for the web UI. Install it with: pip install flask")

from .log_manager import get_log_manager
from .chat_manager import get_chat_manager
from .core_web_ui import get_dashboard_html

# Get the directory containing this file for static file serving
_THIS_DIR = Path(__file__).parent


class CoreWebServer:
    """Singleton Flask web server for Moose core.
    
    Handles multiple projects with a single server instance.
    """
    
    _instance: Optional['CoreWebServer'] = None
    _lock = threading.Lock()
    
    def __new__(cls, port: int = 5000):
        """Ensure only one instance exists (singleton pattern)."""
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instance = instance
            return cls._instance
    
    def __init__(self, port: int = 5000):
        """Initialize the web server.
        
        Args:
            port: Port to run the server on
        """
        if self._initialized:
            return
        
        self.port = port
        self._projects: Set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Create Flask app
        self.app = Flask(__name__)
        self.app.config['JSON_SORT_KEYS'] = False
        
        # Register routes
        self._register_routes()
        
        self._initialized = True
    
    def _register_routes(self):
        """Register all Flask routes."""

        @self.app.route('/')
        def dashboard():
            """Serve the main dashboard page."""
            return get_dashboard_html()

        @self.app.route('/static/<path:subpath>')
        def serve_static(subpath):
            """Serve static files (CSS, JS).

            We explicitly disable caching so UI changes take effect immediately during local dev.
            The HTML also includes cache-busting query params, but headers help avoid confusing
            mixed-version behavior.
            """
            static_dir = _THIS_DIR / 'static'
            resp = send_from_directory(str(static_dir), subpath)
            try:
                resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                resp.headers["Pragma"] = "no-cache"
                resp.headers["Expires"] = "0"
            except Exception:
                pass
            return resp
        
        @self.app.route('/api/projects')
        def list_projects():
            """
            List projects.

            This is dynamic: it merges the in-memory registered projects with a best-effort
            filesystem scan of the projects base directory so newly launched projects appear
            without restarting the web UI.
            """
            projects: Set[str] = set(self._projects or set())

            # Best-effort scan:
            # - if MOOSE_PROJECTS_DIR is set, treat it as the projects base directory
            # - else default to ./projects relative to current working directory
            base = os.getenv("MOOSE_PROJECTS_DIR")
            base_dir = Path(base) if base else (Path.cwd() / "projects")
            try:
                if base_dir.exists() and base_dir.is_dir():
                    for child in base_dir.iterdir():
                        if not child.is_dir():
                            continue
                        # Heuristic: include directory if it looks like a Moose project.
                        if (child / "logs").exists() or (child / "project_config.json").exists():
                            projects.add(child.name)
            except Exception:
                pass

            return jsonify(sorted(list(projects)))
        
        @self.app.route('/api/projects/<project_id>/agents')
        def list_agents(project_id: str):
            """List agents for a project."""
            agents = self._get_agents(project_id)
            return jsonify(agents)
        
        @self.app.route('/api/projects/<project_id>/logs/files')
        def list_log_files(project_id: str):
            """List available log files for a project."""
            log_manager = get_log_manager()
            files = log_manager.list_log_files(project_id)
            return jsonify(files)
        
        @self.app.route('/api/projects/<project_id>/logs')
        def get_logs(project_id: str):
            """Get logs for a project.
            
            Query params:
                file: Optional historical log file name
                limit: Optional max entries to return
            """
            log_manager = get_log_manager()
            file = request.args.get('file')
            limit = request.args.get('limit', type=int)
            
            if file:
                # Load historical log file
                entries = log_manager.read_log_file(project_id, file, limit=limit)
            else:
                # Get buffered logs
                entries = log_manager.get_buffer(project_id, limit=limit)
            
            return jsonify(entries)
        
        @self.app.route('/api/projects/<project_id>/logs/stream')
        def stream_logs(project_id: str):
            """SSE stream for real-time logs."""
            log_manager = get_log_manager()
            
            def generate():
                yield from log_manager.generate_sse_stream(project_id)
            
            return Response(
                generate(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'
                }
            )
        
        @self.app.route('/api/projects/<project_id>/chat/files')
        def list_chat_files(project_id: str):
            """List available chat files for a project (agent log files containing LLM JSONL entries)."""
            chat_manager = get_chat_manager()
            files = chat_manager.list_chat_files(project_id)
            return jsonify(files)
        
        @self.app.route('/api/projects/<project_id>/chat')
        def get_chat(project_id: str):
            """Get chat messages for a project.

            Query params:
                file: Optional historical agent log file name (e.g., agents/<agent>.log.<n>)
                limit: Maximum number of messages to return (default 50, for paginated mode)
                before: Only return messages before this timestamp (ISO format, for pagination)
                mode: 'paginated' for new pagination mode, otherwise legacy buffer mode
            """
            chat_manager = get_chat_manager()
            file = request.args.get('file')
            mode = request.args.get('mode', '')

            if file:
                # Load historical chat file
                messages = chat_manager.read_chat_file(project_id, file)
                return jsonify(messages)

            if mode == 'paginated':
                # New paginated mode - reads from current agent log files
                try:
                    limit = int(request.args.get('limit', '50'))
                except ValueError:
                    limit = 50
                limit = max(1, min(limit, 200))  # Clamp between 1 and 200

                before = request.args.get('before')

                result = chat_manager.get_recent_messages(
                    project_id,
                    limit=limit,
                    before=before
                )
                return jsonify(result)

            # Legacy mode: return buffered messages
            messages = chat_manager.get_buffer(project_id)
            return jsonify(messages)

        @self.app.route('/api/projects/<project_id>/chat/stream')
        def stream_chat(project_id: str):
            """SSE stream for real-time chat messages.

            Query params:
                since: Only stream messages newer than this timestamp (ISO format).
                       When provided, enables efficient live-tail mode where historical
                       messages are loaded via the paginated API and only new messages
                       come through the SSE stream.
            """
            chat_manager = get_chat_manager()
            since = request.args.get('since')

            def generate():
                yield from chat_manager.generate_sse_stream(project_id, since=since)

            return Response(
                generate(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'
                }
            )

        @self.app.route('/api/projects/<project_id>/llm/usage_summary')
        def get_llm_usage_summary(project_id: str):
            """
            Aggregate cost + token usage across agent log files for the project.

            Groups by metadata.agent_name (main agent attribution) and by day.
            """
            chat_manager = get_chat_manager()

            def _empty():
                return {
                    "project_id": project_id,
                    "totals": {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}},
                    "by_agent": {},
                    "per_day": [],
                }

            # Resolve log directory (uses ChatManager's logic for MOOSE_PROJECTS_DIR / cwd projects)
            try:
                log_dir = chat_manager._get_log_dir(project_id)  # type: ignore[attr-defined]
            except Exception:
                log_dir = None
            if not log_dir or not Path(log_dir).exists():
                return jsonify(_empty())
            log_dir = Path(log_dir)

            try:
                # e.g. ["agents/finance_office.log.40", "agents/truthsocial_agent.log.40", ...]
                files = chat_manager.list_chat_files(project_id)
            except Exception:
                files = []
            if not files:
                return jsonify(_empty())

            totals_cost = 0.0
            totals_tokens = {"input": 0, "output": 0, "total": 0}
            by_agent: Dict[str, Dict[str, object]] = {}
            per_day_map: Dict[str, Dict[str, Dict[str, object]]] = {}
            totals_by_provider: Dict[str, Dict[str, object]] = {}

            def _bump(bucket: Dict[str, object], *, cost: float, it: int, ot: int, tt: int):
                bucket["cost"] = float(bucket.get("cost", 0.0) or 0.0) + float(cost or 0.0)
                toks = bucket.get("tokens") if isinstance(bucket.get("tokens"), dict) else {"input": 0, "output": 0, "total": 0}
                toks["input"] = int(toks.get("input", 0) or 0) + int(it or 0)
                toks["output"] = int(toks.get("output", 0) or 0) + int(ot or 0)
                toks["total"] = int(toks.get("total", 0) or 0) + int(tt or 0)
                bucket["tokens"] = toks

            def _normalize_provider(raw: object, model: object) -> str:
                """
                Best-effort provider name normalization for UI grouping.

                We prefer the explicit response_metadata.model_provider field when present.
                Fallback heuristics use model name prefixes.
                """
                p = (str(raw or "").strip() or "").lower()
                m = (str(model or "").strip() or "").lower()
                if p:
                    if p in {"openai"}:
                        return "OpenAI"
                    if p in {"google_genai", "google", "gemini"}:
                        return "Gemini"
                    if p in {"anthropic"}:
                        return "Anthropic"
                    if p in {"xai", "grok"}:
                        return "xAI"
                    return str(raw or "Unknown").strip() or "Unknown"
                # Heuristic from model name
                if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
                    return "OpenAI"
                if m.startswith("gemini"):
                    return "Gemini"
                if m.startswith("claude"):
                    return "Anthropic"
                return "Unknown"

            def _extract_provider(entry: dict) -> str:
                # Most logs store provider inside message.response_metadata.model_provider
                msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
                resp_meta = msg.get("response_metadata") if isinstance(msg.get("response_metadata"), dict) else {}
                raw_provider = resp_meta.get("model_provider") or entry.get("model_provider") or entry.get("provider")
                return _normalize_provider(raw_provider, entry.get("model"))

            for filename in files:
                fp = log_dir / filename
                if not fp.exists() or not fp.is_file():
                    continue
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        for line in f:
                            line = (line or "").strip()
                            if not line:
                                continue
                            try:
                                if not line.startswith("{"):
                                    continue
                                entry = json.loads(line)
                            except Exception:
                                continue
                            if not isinstance(entry, dict):
                                continue
                            if "direction" not in entry or "timestamp" not in entry:
                                continue
                            if entry.get("direction") != "response":
                                continue

                            meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                            agent = str(meta.get("agent_name") or entry.get("agent_name") or "unknown")
                            ts = str(entry.get("timestamp") or "")
                            day = ts[:10] if len(ts) >= 10 else "unknown"
                            provider = _extract_provider(entry)

                            usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else {}
                            try:
                                it = int(usage.get("input_tokens", 0) or 0)
                                ot = int(usage.get("output_tokens", 0) or 0)
                                tt = int(usage.get("total_tokens", it + ot) or (it + ot))
                            except Exception:
                                it, ot, tt = 0, 0, 0

                            try:
                                cost = float(entry.get("cost") or 0.0)
                            except Exception:
                                cost = 0.0

                            totals_cost += cost
                            totals_tokens["input"] += it
                            totals_tokens["output"] += ot
                            totals_tokens["total"] += tt

                            # Totals by provider
                            if provider not in totals_by_provider:
                                totals_by_provider[provider] = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}}
                            _bump(totals_by_provider[provider], cost=cost, it=it, ot=ot, tt=tt)

                            if agent not in by_agent:
                                by_agent[agent] = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}, "by_provider": {}}
                            _bump(by_agent[agent], cost=cost, it=it, ot=ot, tt=tt)
                            # Agent by provider
                            bp = by_agent[agent].get("by_provider")
                            if not isinstance(bp, dict):
                                bp = {}
                                by_agent[agent]["by_provider"] = bp
                            if provider not in bp:
                                bp[provider] = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}}
                            _bump(bp[provider], cost=cost, it=it, ot=ot, tt=tt)

                            if day not in per_day_map:
                                per_day_map[day] = {}
                            if agent not in per_day_map[day]:
                                per_day_map[day][agent] = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}, "by_provider": {}}
                            _bump(per_day_map[day][agent], cost=cost, it=it, ot=ot, tt=tt)
                            # Per-day agent by provider
                            day_bp = per_day_map[day][agent].get("by_provider")
                            if not isinstance(day_bp, dict):
                                day_bp = {}
                                per_day_map[day][agent]["by_provider"] = day_bp
                            if provider not in day_bp:
                                day_bp[provider] = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}}
                            _bump(day_bp[provider], cost=cost, it=it, ot=ot, tt=tt)
                except Exception:
                    continue

            per_day = [{"date": d, "by_agent": per_day_map[d]} for d in sorted(per_day_map.keys())]
            return jsonify(
                {
                    "project_id": project_id,
                    "totals": {"cost": float(totals_cost), "tokens": totals_tokens, "by_provider": totals_by_provider},
                    "by_agent": by_agent,
                    "per_day": per_day,
                }
            )

        @self.app.route('/api/projects/<project_id>/llm/usage_by_agent/<agent_name>')
        def get_llm_usage_by_agent(project_id: str, agent_name: str):
            """
            Paginated request-level cost + token usage for one agent (agent-only attribution).

            Backed by the project's trace.db (SQLite), using spans + llm_calls tables.

            Query params:
              - limit: page size (default 20; allowed: 20, 50, 100)
              - offset: offset for pagination (default 0)
            """

            def _empty(*, limit: int, offset: int):
                return {
                    "project_id": project_id,
                    "agent_name": str(agent_name or ""),
                    "limit": int(limit),
                    "offset": int(offset),
                    "has_more": False,
                    "requests": [],
                }

            # Validate pagination inputs
            limit = request.args.get("limit", type=int) or 20
            if limit not in (20, 50, 100):
                limit = 20
            offset = request.args.get("offset", type=int) or 0
            try:
                offset = max(0, int(offset))
            except Exception:
                offset = 0

            agent = str(agent_name or "").strip()
            if not agent:
                return jsonify(_empty(limit=limit, offset=offset))

            db_path = _get_trace_db_path(project_id)
            if not db_path:
                return jsonify(_empty(limit=limit, offset=offset))

            def _parse_usage(usage_json: object) -> tuple[int, int, int]:
                if usage_json is None:
                    return (0, 0, 0)
                raw = usage_json
                if isinstance(raw, (bytes, bytearray)):
                    try:
                        raw = raw.decode("utf-8", errors="ignore")
                    except Exception:
                        raw = ""
                if isinstance(raw, str):
                    s = raw.strip()
                    if not s:
                        return (0, 0, 0)
                    try:
                        obj = json.loads(s)
                    except Exception:
                        return (0, 0, 0)
                elif isinstance(raw, dict):
                    obj = raw
                else:
                    return (0, 0, 0)

                if not isinstance(obj, dict):
                    return (0, 0, 0)
                try:
                    it = int(obj.get("input_tokens", 0) or 0)
                    ot = int(obj.get("output_tokens", 0) or 0)
                    tt = int(obj.get("total_tokens", it + ot) or (it + ot))
                    return (it, ot, tt)
                except Exception:
                    return (0, 0, 0)

            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                # Page request_ids by most recent llm call start_ts for this agent.
                page_rows = conn.execute(
                    """
                    SELECT
                      s.request_id AS request_id,
                      MIN(COALESCE(s.start_ts, 0)) AS first_ts,
                      MAX(COALESCE(s.start_ts, 0)) AS last_ts
                    FROM llm_calls lc
                    JOIN spans s ON s.span_id = lc.span_id
                    WHERE lc.agent_name = ?
                      AND s.request_id IS NOT NULL
                      AND s.request_id != ''
                    GROUP BY s.request_id
                    ORDER BY last_ts DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (agent, int(limit) + 1, int(offset)),
                ).fetchall()

                has_more = len(page_rows) > limit
                page_rows = page_rows[:limit]

                request_ids = [str(r["request_id"] or "") for r in page_rows if str(r["request_id"] or "")]
                last_ts_map = {str(r["request_id"] or ""): float(r["last_ts"] or 0.0) for r in page_rows if str(r["request_id"] or "")}
                first_ts_map = {str(r["request_id"] or ""): float(r["first_ts"] or 0.0) for r in page_rows if str(r["request_id"] or "")}

                if not request_ids:
                    return jsonify(_empty(limit=limit, offset=offset))

                placeholders = ",".join(["?"] * len(request_ids))
                call_rows = conn.execute(
                    f"""
                    SELECT
                      s.request_id AS request_id,
                      s.start_ts AS start_ts,
                      lc.model AS model,
                      lc.cost AS cost,
                      lc.usage_json AS usage_json
                    FROM llm_calls lc
                    JOIN spans s ON s.span_id = lc.span_id
                    WHERE lc.agent_name = ?
                      AND s.request_id IN ({placeholders})
                    ORDER BY s.start_ts DESC;
                    """,
                    (agent, *request_ids),
                ).fetchall()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            # Aggregate per request and per model.
            req_buckets: Dict[str, Dict[str, object]] = {}
            for rid in request_ids:
                req_buckets[rid] = {
                    "request_id": rid,
                    "first_ts": float(first_ts_map.get(rid, 0.0) or 0.0),
                    "last_ts": float(last_ts_map.get(rid, 0.0) or 0.0),
                    "cost": 0.0,
                    "tokens": {"input": 0, "output": 0, "total": 0},
                    "_by_model": {},  # internal map: model -> bucket
                }

            for row in call_rows:
                rid = str(row["request_id"] or "").strip()
                if not rid or rid not in req_buckets:
                    continue

                model = str(row["model"] or "").strip() or "unknown"
                try:
                    cost = float(row["cost"] or 0.0)
                except Exception:
                    cost = 0.0
                it, ot, tt = _parse_usage(row["usage_json"])

                bucket = req_buckets[rid]
                bucket["cost"] = float(bucket.get("cost", 0.0) or 0.0) + cost
                toks = bucket.get("tokens") if isinstance(bucket.get("tokens"), dict) else {"input": 0, "output": 0, "total": 0}
                toks["input"] = int(toks.get("input", 0) or 0) + it
                toks["output"] = int(toks.get("output", 0) or 0) + ot
                toks["total"] = int(toks.get("total", 0) or 0) + tt
                bucket["tokens"] = toks

                by_model = bucket.get("_by_model")
                if not isinstance(by_model, dict):
                    by_model = {}
                    bucket["_by_model"] = by_model
                mb = by_model.get(model)
                if not isinstance(mb, dict):
                    mb = {"model": model, "cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}}
                    by_model[model] = mb
                mb["cost"] = float(mb.get("cost", 0.0) or 0.0) + cost
                mt = mb.get("tokens") if isinstance(mb.get("tokens"), dict) else {"input": 0, "output": 0, "total": 0}
                mt["input"] = int(mt.get("input", 0) or 0) + it
                mt["output"] = int(mt.get("output", 0) or 0) + ot
                mt["total"] = int(mt.get("total", 0) or 0) + tt
                mb["tokens"] = mt

            # Format response preserving most-recent-first request order from page_rows.
            requests_out: List[Dict[str, object]] = []
            for rid in request_ids:
                b = req_buckets.get(rid) or {}
                by_model_map = b.get("_by_model") if isinstance(b.get("_by_model"), dict) else {}
                by_model_list = list(by_model_map.values()) if isinstance(by_model_map, dict) else []
                # Stable sort: highest cost first, then model name
                try:
                    by_model_list.sort(key=lambda x: (-float((x or {}).get("cost", 0.0) or 0.0), str((x or {}).get("model", ""))))
                except Exception:
                    pass
                out = {
                    "request_id": rid,
                    "first_ts": float(b.get("first_ts", 0.0) or 0.0),
                    "last_ts": float(b.get("last_ts", 0.0) or 0.0),
                    "cost": float(b.get("cost", 0.0) or 0.0),
                    "tokens": b.get("tokens") if isinstance(b.get("tokens"), dict) else {"input": 0, "output": 0, "total": 0},
                    "by_model": by_model_list,
                }
                requests_out.append(out)

            return jsonify(
                {
                    "project_id": project_id,
                    "agent_name": agent,
                    "limit": int(limit),
                    "offset": int(offset),
                    "has_more": bool(has_more),
                    "requests": requests_out,
                }
            )

        # ------------------------------------------------------------------
        # Trace DB (SQLite) endpoints
        # ------------------------------------------------------------------
        def _get_trace_db_path(project_id: str) -> Optional[Path]:
            chat_manager = get_chat_manager()
            try:
                log_dir = chat_manager._get_log_dir(project_id)  # type: ignore[attr-defined]
            except Exception:
                log_dir = None
            if not log_dir:
                return None
            db_path = Path(log_dir) / "trace.db"
            return db_path if db_path.exists() and db_path.is_file() else None

        @self.app.route('/api/projects/<project_id>/traces')
        def list_traces(project_id: str):
            """
            List recent traces for a project.

            Query params:
              - limit: max traces (default 100, max 1000)
              - q: optional substring filter on request_id
              - since: YYYY-MM-DD (inclusive, based on traces.started_at)
              - until: YYYY-MM-DD (inclusive, based on traces.started_at)
              - ingress_only: 1/0 (default 1) - only include traces that have an ingress.http span
            """
            db_path = _get_trace_db_path(project_id)
            if not db_path:
                return jsonify([])

            limit = request.args.get('limit', type=int) or 100
            limit = max(1, min(1000, int(limit)))
            q = (request.args.get('q') or "").strip()
            since = (request.args.get('since') or "").strip()
            until = (request.args.get('until') or "").strip()
            ingress_only = str(request.args.get('ingress_only') or "1").strip().lower() not in {"0", "false", "no"}

            # Basic YYYY-MM-DD validation
            def _is_date(s: str) -> bool:
                if len(s) != 10:
                    return False
                return s[4] == "-" and s[7] == "-" and s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()

            since_ok = since if _is_date(since) else ""
            until_ok = until if _is_date(until) else ""

            # Build filters over traces.started_at (ISO string). This is lexicographically sortable.
            where = []
            args: list[object] = []
            if q:
                where.append("t.request_id LIKE ?")
                args.append(f"%{q}%")
            if since_ok:
                where.append("t.started_at >= ?")
                args.append(f"{since_ok}T00:00:00")
            if until_ok:
                # Inclusive end-of-day: compare to next day at 00:00.
                try:
                    import datetime as _dt

                    y, m, d = int(until_ok[:4]), int(until_ok[5:7]), int(until_ok[8:10])
                    nxt = (_dt.date(y, m, d) + _dt.timedelta(days=1)).isoformat()
                    where.append("t.started_at < ?")
                    args.append(f"{nxt}T00:00:00")
                except Exception:
                    pass

            where_sql = " AND ".join(where) if where else "1=1"

            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                if ingress_only:
                    rows = conn.execute(
                        f"""
                        SELECT t.request_id, t.project_id, t.started_at, t.root_agent, t.root_kind, t.status
                        FROM traces t
                        WHERE ({where_sql})
                          AND EXISTS (
                            SELECT 1 FROM spans s
                            WHERE s.request_id = t.request_id AND s.kind = 'ingress.http'
                          )
                        ORDER BY t.started_at DESC
                        LIMIT ?;
                        """,
                        (*args, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT t.request_id, t.project_id, t.started_at, t.root_agent, t.root_kind, t.status
                        FROM traces t
                        WHERE ({where_sql})
                        ORDER BY t.started_at DESC
                        LIMIT ?;
                        """,
                        (*args, limit),
                    ).fetchall()
                return jsonify([dict(r) for r in rows])
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        @self.app.route('/api/projects/<project_id>/traces/<request_id>')
        def get_trace(project_id: str, request_id: str):
            """Get a trace summary and all spans for the trace."""
            db_path = _get_trace_db_path(project_id)
            if not db_path:
                return jsonify({"trace": None, "spans": []})

            rid = str(request_id or "").strip()
            if not rid:
                return jsonify({"trace": None, "spans": []})

            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                tr = conn.execute(
                    "SELECT request_id, project_id, started_at, root_agent, root_kind, status, attrs_json FROM traces WHERE request_id = ?;",
                    (rid,),
                ).fetchone()
                spans = conn.execute(
                    """
                    SELECT span_id, request_id, parent_span_id, kind, name, start_ts, end_ts, status, error, attrs_json, project_id, agent_name
                    FROM spans
                    WHERE request_id = ?
                    ORDER BY start_ts ASC;
                    """,
                    (rid,),
                ).fetchall()
                return jsonify({"trace": dict(tr) if tr else None, "spans": [dict(s) for s in spans]})
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        @self.app.route('/api/projects/<project_id>/traces/<request_id>/llm_chat')
        def get_trace_llm_chat(project_id: str, request_id: str):
            """
            Flatten all llm.call spans' messages for a trace into a single chronological stream.

            Intended for chat-bubble UI.
            """
            db_path = _get_trace_db_path(project_id)
            if not db_path:
                return jsonify([])

            rid = str(request_id or "").strip()
            if not rid:
                return jsonify([])

            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                try:
                    tc_cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(tool_calls);").fetchall()]
                except Exception:
                    tc_cols = []
                has_tool_call_id = "tool_call_id" in tc_cols

                if has_tool_call_id:
                    query = """
                        SELECT
                          s.span_id AS span_id,
                          s.kind AS span_kind,
                          s.status AS span_status,
                          s.start_ts AS span_start_ts,
                          s.end_ts AS span_end_ts,
                          s.name AS span_name,
                          s.agent_name AS agent_name,
                          p.kind AS parent_kind,
                          p.name AS parent_name,
                          m.role AS role,
                          m.idx AS idx,
                          m.content AS content,
                          m.name AS name,
                          m.tool_call_id AS tool_call_id,
                          m.tool_calls_json AS tool_calls_json,
                          tc.tool_name AS tool_call_name,
                          tc.args_json AS tool_args_json,
                          tc.result_json AS tool_result_json,
                          tc.error AS tool_error
                        FROM spans s
                        LEFT JOIN spans p ON p.span_id = s.parent_span_id
                        LEFT JOIN tool_calls tc ON tc.tool_call_id = m.tool_call_id
                        JOIN llm_messages m ON m.span_id = s.span_id
                        WHERE s.request_id = ?
                          AND s.kind = 'llm.call'
                        ORDER BY s.start_ts ASC, m.idx ASC;
                    """
                else:
                    query = """
                        SELECT
                          s.span_id AS span_id,
                          s.kind AS span_kind,
                          s.status AS span_status,
                          s.start_ts AS span_start_ts,
                          s.end_ts AS span_end_ts,
                          s.name AS span_name,
                          s.agent_name AS agent_name,
                          p.kind AS parent_kind,
                          p.name AS parent_name,
                          m.role AS role,
                          m.idx AS idx,
                          m.content AS content,
                          m.name AS name,
                          m.tool_call_id AS tool_call_id,
                          m.tool_calls_json AS tool_calls_json,
                          NULL AS tool_call_name,
                          NULL AS tool_args_json,
                          NULL AS tool_result_json,
                          NULL AS tool_error
                        FROM spans s
                        LEFT JOIN spans p ON p.span_id = s.parent_span_id
                        JOIN llm_messages m ON m.span_id = s.span_id
                        WHERE s.request_id = ?
                          AND s.kind = 'llm.call'
                        ORDER BY s.start_ts ASC, m.idx ASC;
                    """

                rows = conn.execute(query, (rid,)).fetchall()
                return jsonify([dict(r) for r in rows])
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        @self.app.route('/api/projects/<project_id>/spans/<span_id>')
        def get_span(project_id: str, span_id: str):
            """Get one span and its immediate children."""
            db_path = _get_trace_db_path(project_id)
            if not db_path:
                return jsonify({"span": None, "children": []})

            sid = str(span_id or "").strip()
            if not sid:
                return jsonify({"span": None, "children": []})

            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                sp = conn.execute(
                    """
                    SELECT span_id, request_id, parent_span_id, kind, name, start_ts, end_ts, status, error, attrs_json, project_id, agent_name
                    FROM spans
                    WHERE span_id = ?;
                    """,
                    (sid,),
                ).fetchone()
                children = conn.execute(
                    """
                    SELECT span_id, request_id, parent_span_id, kind, name, start_ts, end_ts, status
                    FROM spans
                    WHERE parent_span_id = ?
                    ORDER BY start_ts ASC;
                    """,
                    (sid,),
                ).fetchall()
                return jsonify({"span": dict(sp) if sp else None, "children": [dict(c) for c in children]})
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        @self.app.route('/api/projects/<project_id>/spans/<span_id>/llm_messages')
        def get_span_llm_messages(project_id: str, span_id: str):
            """Get LLM messages for a given llm.call span."""
            db_path = _get_trace_db_path(project_id)
            if not db_path:
                return jsonify([])

            sid = str(span_id or "").strip()
            if not sid:
                return jsonify([])

            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT role, idx, content, name, tool_call_id, tool_calls_json
                    FROM llm_messages
                    WHERE span_id = ?
                    ORDER BY idx ASC;
                    """,
                    (sid,),
                ).fetchall()
                return jsonify([dict(r) for r in rows])
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def _check_agent_health(self, url: str, timeout: float = 2.0) -> str:
        """Check agent health via HTTP /health endpoint.
        
        Args:
            url: Base URL of the agent (e.g., http://localhost:8000)
            timeout: Request timeout in seconds
            
        Returns:
            'running' if healthy, 'error' if unhealthy, 'stopped' if unreachable
        """
        import urllib.request
        import urllib.error
        
        try:
            health_url = f"{url}/health"
            req = urllib.request.Request(health_url, method='GET')
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    return 'running'
                else:
                    return 'error'
        except urllib.error.URLError:
            return 'stopped'
        except Exception:
            return 'stopped'
    
    def _get_agents(self, project_id: str) -> List[Dict]:
        """Get agent information for a project.
        
        Args:
            project_id: Project identifier
            
        Returns:
            List of agent info dicts
        """
        agents = []
        
        try:
            # Import AgentLoader and AgentRegistry
            from moose.framework.agent_core import AgentLoader, AgentRegistry, ContainerManager
            
            loader = AgentLoader()
            registry = AgentRegistry()
            
            # Discover all agents
            agent_names = loader.discover_agents()
            
            for agent_name in agent_names:
                status = 'stopped'
                url = None
                local_url = None  # For health checking
                container_name = ''
                interactive_mode = ''
                mode = 'http'
                
                # Load agent config for interactive_mode info
                try:
                    config = loader.load_agent_config(agent_name)
                    interactive_config = config.get('interactive_mode', {})
                    mode = interactive_config.get('mode', 'http')
                    
                    if mode == 'http':
                        http_config = interactive_config.get('http_server', {})
                        port = http_config.get('port', 8000)
                        interactive_mode = f"http (localhost:{port})"
                        local_url = f"http://localhost:{port}"
                        url = f"https://{agent_name}.etenal.me"
                    elif mode == 'file':
                        file_config = interactive_config.get('file', {})
                        watch_dir = file_config.get('watch_dir', '/project/agent_io')
                        interactive_mode = f"file ({watch_dir})"
                    else:
                        interactive_mode = mode
                except Exception:
                    interactive_mode = 'unknown'
                
                # Check agent status
                if mode == 'http' and local_url:
                    # For HTTP mode, check health endpoint using local URL
                    status = self._check_agent_health(local_url)
                else:
                    # For other modes, check container status
                    container_id = registry.get_container_id(project_id, agent_name)
                    if container_id:
                        container_name = container_id[:12]
                        
                        # Try to get container status
                        try:
                            manager = ContainerManager()
                            container_status = manager.get_container_status(agent_name, project_id)
                            status = container_status if container_status else 'stopped'
                        except Exception:
                            status = 'unknown'
                
                # Still get container name even for HTTP mode
                if not container_name:
                    container_id = registry.get_container_id(project_id, agent_name)
                    if container_id:
                        container_name = container_id[:12]
                
                agents.append({
                    'name': agent_name,
                    'status': status,
                    'container': container_name,
                    'interactive_mode': interactive_mode,
                    'url': url
                })
        
        except Exception as e:
            # If loader not available, return empty list
            pass
        
        return agents
    
    def add_project(self, project_id: str):
        """Register a project with the server.
        
        Args:
            project_id: Project identifier
        """
        self._projects.add(project_id)
    
    def remove_project(self, project_id: str):
        """Unregister a project from the server.
        
        Args:
            project_id: Project identifier
        """
        self._projects.discard(project_id)
    
    def start(self, blocking: bool = True):
        """Start the web server.
        
        Args:
            blocking: If True, block the calling thread. If False, run in background.
        """
        if self._running:
            return
        
        self._running = True
        
        if blocking:
            self._run_server()
        else:
            self._thread = threading.Thread(target=self._run_server, daemon=True)
            self._thread.start()
    
    def start_background(self):
        """Start the web server in a background thread."""
        self.start(blocking=False)
    
    def _run_server(self):
        """Run the Flask server."""
        try:
            # Disable Flask's default logging for cleaner output
            import logging
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.WARNING)
            
            print(f"\n🦌 Moose Web UI running at http://localhost:{self.port}\n")
            
            self.app.run(
                host='0.0.0.0',
                port=self.port,
                debug=False,
                use_reloader=False,
                threaded=True
            )
        except OSError as e:
            # Handle "Address already in use" error gracefully
            # This happens when another moose server is already running on the same port
            error_msg = str(e).lower()
            if "address already in use" in error_msg or "address is already in use" in error_msg or e.errno == errno.EADDRINUSE:
                print(f"\n⚠️  Port {self.port} is already in use (moose server may already be running)")
                print(f"   Skipping moose server launch. Existing server will auto-discover agents.\n")
            else:
                print(f"Failed to start web server: {e}")
            self._running = False
        except Exception as e:
            print(f"Failed to start web server: {e}")
            self._running = False
    
    def stop(self):
        """Stop the web server."""
        self._running = False
        # Note: Flask doesn't have a clean way to stop from another thread
        # The server will stop when the process exits
    
    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._running
    
    @classmethod
    def get_instance(cls) -> Optional['CoreWebServer']:
        """Get the singleton instance if it exists."""
        return cls._instance


# Module-level functions for convenience

_server_instance: Optional[CoreWebServer] = None


def get_or_start_core_server(port: int = 5000) -> CoreWebServer:
    """Get or create and start the core web server.
    
    Args:
        port: Port for the server
        
    Returns:
        CoreWebServer instance
    """
    global _server_instance
    
    if _server_instance is None:
        _server_instance = CoreWebServer(port)
    
    if not _server_instance.is_running:
        _server_instance.start_background()
    
    return _server_instance


def register_project(project_id: str, port: int = 5000) -> CoreWebServer:
    """Register a project and ensure the web server is running.
    
    Args:
        project_id: Project identifier
        port: Port for the server
        
    Returns:
        CoreWebServer instance
    """
    server = get_or_start_core_server(port)
    server.add_project(project_id)
    return server
