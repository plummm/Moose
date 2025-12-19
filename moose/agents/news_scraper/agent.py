"""News Scraper Agent.

This agent scrapes news from a configured URL, extracts text content from article URLs,
and saves them to organized folders with SHA256-hashed filenames.
The scraped articles are sent to the finance_office agent for analysis.
"""

import os, sys
from pathlib import Path
from typing import Dict, Any

from moose.framework import BaseAgent
from datetime import datetime
import json
import html
from urllib.parse import quote

# Import local modules
try:
    from scraper import NewsScraperCore, NewsScraperService
except ImportError:
    # Fallback for direct execution
    from moose.agents.news_scraper.scraper import NewsScraperCore, NewsScraperService


class NewsScraper(BaseAgent):
    """
    Generic news scraper agent.
    
    Scrapes news from a configured URL, extracts text content from article URLs,
    saves them to organized folders with SHA256-hashed filenames.
    Sends scraped articles to finance_office agent for analysis.
    """
    
    name = "news_scraper"
    description = "Generic news scraper that extracts text content from web pages"
    
    def __init__(self, config_path=None, debug=False):
        """Initialize the news scraper."""
        super().__init__(config_path, debug=debug)
        
        # Load scraper configuration
        scraper_config = NewsScraperService.load_scraper_config(self.config)
        
        # Initialize data directory
        data_dir = os.getenv("SCRAPER_DATA_DIR", "/data/scraper/finviz.com")
        data_dir_path = Path(data_dir)
        data_dir_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Data directory: {data_dir_path}")
        
        # Initialize scraper core
        scraper_core = NewsScraperCore(
            data_dir=data_dir_path,
            scraper_config=scraper_config,
            logger=self.logger
        )
        
        # Get finance_office endpoint URL from config
        custom_config = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
        analyzer_config = custom_config.get("finance_office", {})
        analyzer_endpoint = analyzer_config.get("endpoint", "http://localhost:3501/get_financial_news")

        llm_config = custom_config.get("llm_config") if isinstance(custom_config.get("llm_config"), dict) else None
        
        # Initialize scraping service (no summarizer or workflow)
        self.scraper_service = NewsScraperService(
            scraper_core=scraper_core,
            analyzer_endpoint=analyzer_endpoint,
            llm_config=llm_config,
            logger=self.logger
        )

        # Optional background monitor (polls start_url every few hours)
        try:
            self.scraper_service.start_monitor()
        except Exception as e:
            self.logger.warning(f"Failed to start auto-monitor: {e}")
        
        self.logger.info(f"Initialized scraper with start_url: {scraper_config.get('start_url')}")
        self.logger.info(f"Analyzer endpoint: {analyzer_endpoint}")
    
    async def scrape(self, input_data=None) -> Any:
        """
        Main scraping method (delegates to scraper service).
        
        Args:
            input_data: Can be:
                - Dict with optional keys: start_url, max_depth
                - String: treated as start_url override
                - Empty/None: uses config defaults
                
        Returns:
            Dict with scraping results
        """
        return await self.scraper_service.scrape(input_data)

    def _get_data_dir(self) -> Path:
        return Path(os.getenv("SCRAPER_DATA_DIR", "/data/scraper/finviz.com"))

    def _today_folder(self) -> Path:
        now = datetime.now()
        return self._get_data_dir() / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"

    def status_page(self, data: Dict[str, Any]) -> Any:
        """
        HTML page: shows today's scraped article count + list of titles.
        """
        from flask import Response

        limit = 100
        try:
            if isinstance(data, dict) and data.get("limit") is not None:
                limit = int(data.get("limit"))
        except Exception:
            limit = 100
        limit = max(1, min(500, limit))

        folder = self._today_folder()
        files = []
        if folder.exists():
            files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

        total = len(files)
        items = []
        for p in files[:limit]:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                payload = {}
            title = str((payload or {}).get("title") or p.name).strip() or p.name
            rel = p.relative_to(self._get_data_dir())
            href = "/article?path=" + quote(str(rel))
            items.append(f'<li><a href="{href}">{html.escape(title)}</a></li>')

        # Analyzer queue stats (background send)
        svc = getattr(self, "scraper_service", None)
        analyzer_depth = 0
        analyzer_enqueued = 0
        analyzer_ok = 0
        analyzer_failed = 0
        try:
            analyzer_depth = int(getattr(svc, "_analyzer_queue").qsize()) if getattr(svc, "_analyzer_queue", None) is not None else 0
            analyzer_enqueued = int(getattr(svc, "analyzer_enqueued", 0) or 0)
            analyzer_ok = int(getattr(svc, "analyzer_sent_ok", 0) or 0)
            analyzer_failed = int(getattr(svc, "analyzer_sent_failed", 0) or 0)
        except Exception:
            pass

        page = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>news_scraper status</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
      .muted {{ color: #555; }}
      .card {{ border: 1px solid #e5e5e5; border-radius: 10px; padding: 16px; margin-top: 16px; }}
      a {{ text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
      ul {{ line-height: 1.7; }}
    </style>
  </head>
  <body>
    <h2>news_scraper — Today</h2>
    <div class="muted">Folder: {html.escape(str(folder))}</div>
    <div class="card">
      <div><b>Total scraped today:</b> {total}</div>
      <div class="muted">Showing latest {min(limit, total)} (use <code>?limit=...</code> up to 500)</div>
    </div>
    <div class="card">
      <h3>Analyzer posting queue</h3>
      <div><b>Queue depth:</b> {analyzer_depth}</div>
      <div><b>Enqueued:</b> {analyzer_enqueued}</div>
      <div><b>Sent OK:</b> {analyzer_ok}</div>
      <div><b>Sent failed:</b> {analyzer_failed}</div>
    </div>
    <div class="card">
      <h3>Today's scraped articles</h3>
      <ul>
        {''.join(items) if items else "<li class='muted'>No articles found for today.</li>"}
      </ul>
    </div>
  </body>
</html>"""

        return Response(page, mimetype="text/html")

    def article_page(self, data: Dict[str, Any]) -> Any:
        """
        HTML page: shows title + source URL + summary for a given saved JSON article.
        Query: /article?path=<YYYY/MM/DD/<hash>.json>
        """
        from flask import Response

        rel_path = ""
        if isinstance(data, dict):
            rel_path = str(data.get("path") or "").strip()

        base = self._get_data_dir().resolve()
        if not rel_path:
            return Response("<h3>Missing required query param: path</h3>", mimetype="text/html", status=400)

        # Validate relative path (no traversal)
        try:
            target = (base / rel_path).resolve()
        except Exception:
            return Response("<h3>Invalid path</h3>", mimetype="text/html", status=400)

        if not str(target).startswith(str(base) + os.sep):
            return Response("<h3>Invalid path (outside data dir)</h3>", mimetype="text/html", status=400)

        if not target.exists():
            return Response("<h3>Not found</h3>", mimetype="text/html", status=404)

        try:
            with open(target, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            return Response(f"<h3>Failed to read JSON: {html.escape(str(e))}</h3>", mimetype="text/html", status=500)

        if not isinstance(payload, dict):
            return Response("<h3>Invalid JSON format</h3>", mimetype="text/html", status=500)

        title = str(payload.get("title") or "Untitled").strip()
        url = str(payload.get("url") or "").strip()
        summary = str(payload.get("summary") or "").strip()

        if url:
            safe_url = html.escape(url)
            source_html = f'<a href="{safe_url}" target="_blank" rel="noreferrer">{safe_url}</a>'
        else:
            source_html = '<span class="muted">(missing url)</span>'

        page = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; max-width: 980px; }}
      .muted {{ color: #555; }}
      .card {{ border: 1px solid #e5e5e5; border-radius: 10px; padding: 16px; margin-top: 16px; }}
      a {{ text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
      pre {{ white-space: pre-wrap; word-break: break-word; }}
    </style>
  </head>
  <body>
    <div class="muted"><a href="/status">← Back to status</a></div>
    <h2>{html.escape(title)}</h2>
    <div class="card">
      <div><b>Source:</b> {source_html}</div>
      <div class="muted">File: {html.escape(str(target))}</div>
    </div>
    <div class="card">
      <h3>Summary</h3>
      <pre>{html.escape(summary) if summary else '(empty summary)'}</pre>
    </div>
  </body>
</html>"""

        return Response(page, mimetype="text/html")
    
    def process(self, input_data=None) -> Any:
        """
        Main processing method (backward compatibility).
        
        This method calls scrape() to maintain backward compatibility with existing code.
        
        Args:
            input_data: Can be:
                - Dict with optional keys: start_url, max_depth, date
                - String: treated as start_url override
                - Empty/None: uses config defaults
                
        Returns:
            Dict with scraping results
        """
        import asyncio
        return asyncio.run(self.scrape(input_data))
