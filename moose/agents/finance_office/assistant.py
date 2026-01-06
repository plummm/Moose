from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from moose.framework.agent_core.agent_endpoints import resolve_agent_base_url
from moose.framework.logging.http_client import traced_httpx_post


@dataclass
class FinanceOfficeAssistant:
    """
    Department-level helper that owns *ad-hoc* task wrappers (like analyzing a news file).

    The Investment Research team should stay generic; this assistant is responsible for:
    - reading inputs (files, etc.)
    - constructing task-specific prompts/contracts
    - invoking the team via the team manager's official method (run_task)
    - wrapping the output into the endpoint-specific response envelope
    """

    team_manager: Any  # ResearchLead
    logger: Any = None
    # Entire custom_config from agent config; used for metadata like model names, thresholds, etc.
    custom_config: Optional[Dict[str, Any]] = None

    def _team_merge_model(self) -> str:
        """
        For news analysis, the final JSON is produced by the team_merge node.
        If configured, report that model; otherwise fall back to team_manager.model.
        """
        cfg = self.custom_config if isinstance(self.custom_config, dict) else {}
        merge_cfg = cfg.get("team_merge_llm_config") if isinstance(cfg.get("team_merge_llm_config"), dict) else {}
        model = str(merge_cfg.get("model") or "").strip()
        if model:
            return model
        return str(getattr(self.team_manager, "model", "") or "")

    async def process_news(
        self,
        *,
        url: str,
        file_path: Path,
        news_data_dir: Path,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze a single news file and persist the result on success.

        Concurrency is handled by the caller (finance_office worker pool).
        """
        result = await self.analyze_news(url=url, file_path=file_path, metadata=metadata)
        if isinstance(result, dict) and "error" not in result:
            try:
                self.save_news_analysis_result(result, news_data_dir)
            except Exception as save_error:
                if self.logger:
                    self.logger.warning(f"Failed to save analysis result: {save_error}")
            # Best-effort: push analyzed news to telegram_stock_bot breaking news endpoint.
            try:
                await self.push_breaking_news_to_telegram(result)
            except Exception as push_error:
                if self.logger:
                    self.logger.warning(f"Failed to push breaking news to telegram_stock_bot: {push_error}")
            # Best-effort: fanout to alpaca_trader /events for auto-trading workflows.
            try:
                await self.push_breaking_news_to_alpaca_trader(result)
            except Exception as push_error:
                if self.logger:
                    self.logger.warning(f"Failed to push breaking news to alpaca_trader: {push_error}")
        return result

    async def process_trump_tweet(
        self,
        *,
        post: Dict[str, Any],
        news_data_dir: Path,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze a single Truth Social post payload and persist the result on success.

        Concurrency is handled by the caller (finance_office tweet worker pool).
        """
        result = await self.analyze_trump_tweet(post=post, metadata=metadata)
        if isinstance(result, dict) and "error" not in result:
            try:
                self.save_news_analysis_result(result, news_data_dir)
            except Exception as save_error:
                if self.logger:
                    self.logger.warning(f"Failed to save tweet analysis result: {save_error}")
            # Best-effort: push analyzed Trump post to telegram_stock_bot breaking news endpoint.
            # We can reuse the same push function because analyze_trump_tweet returns the same
            # `analyses_by_ticker` envelope shape as analyze_news.
            try:
                await self.push_breaking_news_to_telegram(result)
            except Exception as push_error:
                if self.logger:
                    self.logger.warning(f"Failed to push breaking news to telegram_stock_bot: {push_error}")
            # Best-effort: fanout to alpaca_trader /events for auto-trading workflows.
            try:
                await self.push_breaking_news_to_alpaca_trader(result)
            except Exception as push_error:
                if self.logger:
                    self.logger.warning(f"Failed to push breaking news to alpaca_trader: {push_error}")
            
        return result

    # ---------------------------------------------------------------------
    # Telegram breaking news push (Option A payload)
    # ---------------------------------------------------------------------
    def _telegram_push_cfg(self) -> Dict[str, Any]:
        cfg = self.custom_config if isinstance(self.custom_config, dict) else {}
        news_cfg = cfg.get("news_analysis") if isinstance(cfg.get("news_analysis"), dict) else {}
        tg = news_cfg.get("telegram_push") if isinstance(news_cfg.get("telegram_push"), dict) else {}
        return tg if isinstance(tg, dict) else {}

    def _telegram_push_enabled(self) -> bool:
        tg = self._telegram_push_cfg()
        if "enabled" in tg:
            try:
                return bool(tg.get("enabled"))
            except Exception:
                return False
        # Default: disabled unless explicitly enabled in config.
        return False

    def _normalize_base_url_with_port(self, *, base_url: str, port: int) -> str:
        """
        Normalize a configured base_url such that:
        - base_url in config SHOULD NOT include a port
        - if a port is present anyway, we strip it
        - we append the configured/default port
        - we preserve scheme + path/prefix
        """
        b = str(base_url or "").strip().rstrip("/")
        if not b:
            return b

        # Support host-only inputs (no scheme) by assuming http.
        has_scheme = "://" in b
        b2 = b if has_scheme else f"http://{b}"
        try:
            u = urlsplit(b2)
        except Exception:
            # Fallback: if parsing fails, do a minimal join.
            return f"{b}:{int(port)}".rstrip("/")

        scheme = u.scheme or "http"
        hostname = u.hostname or ""
        path = u.path or ""
        query = u.query or ""
        fragment = u.fragment or ""
        if not hostname:
            return f"{b}:{int(port)}".rstrip("/")

        netloc = f"{hostname}:{int(port)}"
        out = urlunsplit((scheme, netloc, path, query, fragment)).rstrip("/")
        # If original had no scheme, return host-only form with port.
        if not has_scheme:
            # Remove "http://" prefix
            if out.startswith("http://"):
                return out[len("http://") :]
        return out

    def _telegram_base_url(self) -> str:
        tg = self._telegram_push_cfg()
        base = str(tg.get("base_url") or "").strip()
        try:
            port = int(tg.get("port", 3502) or 3502)
        except Exception:
            port = 3502
        if base:
            return self._normalize_base_url_with_port(base_url=base, port=port)
        return resolve_agent_base_url(agent_name="telegram_stock_bot", project_id="telegram_stock_bot", port=port).rstrip("/")

    def _telegram_timeout_s(self) -> float:
        tg = self._telegram_push_cfg()
        try:
            return float(tg.get("timeout_s", 5.0) or 5.0)
        except Exception:
            return 5.0

    def _build_telegram_items(self, analyses_by_ticker: Dict[str, Any]) -> list[Dict[str, Any]]:
        """
        Convert analyses_by_ticker -> Option A payload items.
        Group identical content across tickers into a single item with tickers=[...].
        """
        # Include sentiment_rating in the grouping key because telegram_stock_bot filters per-chat by it.
        # Also include urgency/sentiment since those are presented in the Telegram message.
        groups: Dict[tuple[str, str, str, str, str, str, str], Dict[str, Any]] = {}

        def _urgency_from_sentiment_rating(sr: str) -> str:
            """
            Map sentiment_rating -> urgency word:
              - BL0/BR0 -> FYI
              - BL1/BR1 -> Watch
              - BL2/BR2 -> Urgent
            Neutral/unknown -> FYI
            """
            r = str(sr or "").strip().upper()
            if r in ("BL2", "BR2"):
                return "Urgent"
            if r in ("BL1", "BR1"):
                return "Watch"
            return "FYI"

        for ticker, payload in (analyses_by_ticker or {}).items():
            t = str(ticker or "").strip()
            if not t:
                continue
            p = payload if isinstance(payload, dict) else {}
            url = str(p.get("url") or "").strip()
            sentiment_rating = str(p.get("sentiment_rating") or "").strip().upper()
            sentiment = str(p.get("sentiment") or "").strip().lower()
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"
            urgency = _urgency_from_sentiment_rating(sentiment_rating)
            title = str(p.get("title") or "").strip()
            summary = str(p.get("high_level_idea") or "").strip()
            insights = str(p.get("trading_insights") or "").strip()
            conf = p.get("confidence")
            conf_s = str(conf).strip() if conf is not None else ""

            key = (title, summary, insights, conf_s, sentiment_rating, urgency, sentiment)
            g = groups.get(key)
            if not g:
                g = {
                    "tickers": set(),
                    "url": url,
                    "sentiment_rating": sentiment_rating,
                    "urgency": urgency,
                    "sentiment": sentiment,
                    "title": title,
                    "summary": summary,
                    "trading_insights": insights,
                    "confidence": conf,
                }
                groups[key] = g
            try:
                g["tickers"].add(t)
            except Exception:
                pass

        items: list[Dict[str, Any]] = []
        for g in groups.values():
            tickers = sorted({str(x).strip() for x in (g.get("tickers") or set()) if str(x).strip()})
            if not tickers:
                continue
            items.append(
                {
                    "tickers": tickers,
                    "url": g.get("url") or "",
                    "sentiment_rating": g.get("sentiment_rating") or "",
                    "urgency": g.get("urgency") or "FYI",
                    "sentiment": g.get("sentiment") or "neutral",
                    "title": g.get("title") or "",
                    "summary": g.get("summary") or "",
                    "trading_insights": g.get("trading_insights") or "",
                    "confidence": g.get("confidence"),
                }
            )
        return items

    async def push_breaking_news_to_telegram(self, analysis_result: Dict[str, Any]) -> None:
        """
        Best-effort push to telegram_stock_bot /push_breaking_news using Option A payload.
        """
        if not self._telegram_push_enabled():
            return

        analyses_by_ticker = (
            analysis_result.get("analyses_by_ticker")
            if isinstance(analysis_result.get("analyses_by_ticker"), dict)
            else {}
        )
        items = self._build_telegram_items(analyses_by_ticker)
        if not items:
            return

        base = self._telegram_base_url()
        url = f"{base}/push_breaking_news"
        timeout_s = self._telegram_timeout_s()

        self.logger.debug(f"Pushing {len(items)} breaking news to telegram_stock_bot: {url}")
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            try:
                r = await traced_httpx_post(client, url, json={"items": items})
            except httpx.TransportError as e:
                if self.logger:
                    self.logger.warning(f"Failed to reach telegram_stock_bot: {url}:{e}")
                # If local scheme is https but the server is plain HTTP, retry once.
                if url.startswith("https://localhost:"):
                    url2 = "http://" + url[len("https://") :]
                    r = await traced_httpx_post(client, url2, json={"items": items})
                else:
                    return
            try:
                r.raise_for_status()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"telegram_stock_bot push failed: {url}: {e}")
                return

    # ---------------------------------------------------------------------
    # Alpaca trader fanout (text-only /events payload)
    # ---------------------------------------------------------------------
    def _alpaca_trader_push_cfg(self) -> Dict[str, Any]:
        cfg = self.custom_config if isinstance(self.custom_config, dict) else {}
        news_cfg = cfg.get("news_analysis") if isinstance(cfg.get("news_analysis"), dict) else {}
        at = news_cfg.get("alpaca_trader_push") if isinstance(news_cfg.get("alpaca_trader_push"), dict) else {}
        return at if isinstance(at, dict) else {}

    def _alpaca_trader_push_enabled(self) -> bool:
        at = self._alpaca_trader_push_cfg()
        if "enabled" in at:
            try:
                return bool(at.get("enabled"))
            except Exception:
                return False
        return False

    def _alpaca_trader_base_url(self) -> str:
        at = self._alpaca_trader_push_cfg()
        base = str(at.get("base_url") or "").strip()
        try:
            port = int(at.get("port", 3505) or 3505)
        except Exception:
            port = 3505
        if base:
            return self._normalize_base_url_with_port(base_url=base, port=port)
        return resolve_agent_base_url(agent_name="alpaca_trader", project_id="alpaca_trader", port=port).rstrip("/")

    def _alpaca_trader_timeout_s(self) -> float:
        at = self._alpaca_trader_push_cfg()
        try:
            return float(at.get("timeout_s", 5.0) or 5.0)
        except Exception:
            return 5.0

    def _alpaca_event_text_from_item(self, item: Dict[str, Any]) -> str:
        tickers = item.get("tickers") or []
        try:
            tickers_s = ", ".join([str(t).strip().upper() for t in tickers if str(t).strip()])
        except Exception:
            tickers_s = ""
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        insights = str(item.get("trading_insights") or "").strip()
        url = str(item.get("url") or "").strip()
        sentiment_rating = str(item.get("sentiment_rating") or "").strip()
        urgency = str(item.get("urgency") or "").strip() or "FYI"
        sentiment = str(item.get("sentiment") or "").strip().lower() or "neutral"
        conf = item.get("confidence")

        parts = []
        if tickers_s:
            parts.append(f"Tickers: {tickers_s}")
        if title:
            parts.append(f"Title: {title}")
        if summary:
            parts.append(f"Summary: {summary}")
        if insights:
            parts.append(f"Trading insights: {insights}")
        if urgency:
            parts.append(f"Urgency: {urgency}")
        if sentiment:
            parts.append(f"Sentiment: {sentiment}")
        if sentiment_rating:
            parts.append(f"Sentiment rating: {sentiment_rating}")
        if conf is not None:
            parts.append(f"Confidence: {conf}")
        if url:
            parts.append(f"URL: {url}")
        return "\n".join(parts).strip()

    async def push_breaking_news_to_alpaca_trader(self, analysis_result: Dict[str, Any]) -> None:
        """
        Best-effort fanout to alpaca_trader /events.

        Payload shape (alpaca_trader expects):
          {
            "text": "...",
            "source": "finance_office.breaking_news",
            "timestamp": 1730000000.0,
            ... optional extra fields ...
          }
        """
        if not self._alpaca_trader_push_enabled():
            return

        analyses_by_ticker = (
            analysis_result.get("analyses_by_ticker")
            if isinstance(analysis_result.get("analyses_by_ticker"), dict)
            else {}
        )
        items = self._build_telegram_items(analyses_by_ticker)  # reuse the same grouped shape
        if not items:
            return

        base = self._alpaca_trader_base_url()
        url = f"{base}/events"
        timeout_s = self._alpaca_trader_timeout_s()
        ts = time.time()

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            for it in items:
                text = self._alpaca_event_text_from_item(it)
                if not text:
                    continue
                payload = {
                    "text": text,
                    "source": "finance_office.breaking_news",
                    "timestamp": ts,
                    # Extra context (alpaca_trader stores raw; safe to include)
                    "tickers": it.get("tickers") or [],
                    "meta": {
                        "url": it.get("url") or "",
                        "urgency": it.get("urgency") or "FYI",
                        "sentiment": it.get("sentiment") or "neutral",
                        "sentiment_rating": it.get("sentiment_rating") or "",
                        "title": it.get("title") or "",
                    },
                }
                try:
                    r = await traced_httpx_post(client, url, json=payload)
                except httpx.TransportError as e:
                    if self.logger:
                        self.logger.warning(f"Failed to reach alpaca_trader: {url}:{e}")
                    # If local scheme is https but the server is plain HTTP, retry once.
                    if url.startswith("https://localhost:"):
                        url2 = "http://" + url[len("https://") :]
                        r = await traced_httpx_post(client, url2, json=payload)
                    else:
                        continue
                try:
                    r.raise_for_status()
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"alpaca_trader push failed: {url}: {e}")
                    continue
        
    async def analyze_news(
        self,
        *,
        url: str,
        file_path: Path,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze a financial news article file.

        Returns the news endpoint envelope:
        {
          url, file_path,
          title, high_level_idea, companies, sentiment, sentiment_rating, impact_type, confidence, trading_insights,
          analyzed_at, model,
          routing?, subagent_reports?, evidence?, tool_trace?
        }
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to read article from {file_path}: {e}")
            return {
                "url": url,
                "file_path": str(file_path),
                "error": f"Failed to read article: {e}",
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }

        if not isinstance(payload, dict):
            return {
                "url": url,
                "file_path": str(file_path),
                "error": "Invalid article format: expected JSON object",
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }

        # News scraper now persists JSON-only: {"summary": "...", "raw_article": "..."}.
        content = str(payload.get("raw_article") or "").strip()
        if not content:
            return {
                "url": url,
                "file_path": str(file_path),
                "error": "Empty content: missing raw_article",
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }
        
        quality_score = int(payload.get("quality_score", 0))
        summary = str(payload.get("summary") or "").strip()

        # Preserve existing news contract prompt (system + user)
        system_message = f"""You are a financial news analyst specializing in market-impact evaluation.
Your goal is to analyze financial news articles and provide structured insights for trading decisions.
Your input contains the news article content and the target company, along with the analysis results from different metrics. 
Your analysis of the news should center around the target company, and take into account the different metrics results and provide a comprehensive report of how does the news affect the target company.

Analysis results should follow these rules:

**Sentiment:**
    - "bullish", "bearish", or "neutral"

**Sentiment rating:** (rating are not limited to these examples)
    - If bullish, assign a rating BL0-BL2 based on these tiers:
        BL0: A positive development likely to lift the stock only briefly; does not materially strengthen the business. Market may correct quickly.
            - A company beats earnings by a small margin. (e.g., Apple reports EPS $0.05 above expectations due to one-time seasonal demand. Guidance unchanged.)
            - A short-term partnership announcement. (e.g., Starbucks partners with a local brand for a limited holiday drink menu in China.)
            - Regulatory approval for a minor product update. (e.g., Tesla receives approval for software update rollout in Europe (no new features affecting revenue scale).)
            - Temporary macro boost. (e.g., Oil prices dip, boosting airline stocks for this quarter only.)
        BL1: A solid medium-to-long-term positive catalyst if the company executes well. Could support continued upward movement.
            - Company enters a growing adjacent market. (e.g., Nvidia announces full expansion into automotive embedded systems, but revenue impact depends on adoption.)
            - Large strategic partnership. (e.g., Microsoft partners with OpenAI to integrate ChatGPT into Office products.)
            - Long-term cost reduction initiative. (e.g., Intel announces $30B cost reduction plan over 5 years.)
            - Strong product launch with uncertain competition response (e.g., Apple announces new MacBook with M chips, but no clear competitive advantage over existing models.)
        BL2: A major positive event that reshapes the company's long-term prospects (e.g., entry into a major new profitable market).
            - Company enters a massive new market with credible advantage. (e.g., Amazon receives FDA approval for a nationwide digital pharmacy platform, with potential for significant revenue growth.)
            - Breakthrough product with proven demand. (e.g., Google announces Gemini 3.0 with 10x performance boost, likely to disrupt AI markets.)
            - Strategic acquisition that transforms capabilities. (e.g., Microsoft acquires Activision Blizzard, adding major game franchises to Xbox/Game Pass ecosystem.)
            - Major regulatory victory that opens new revenue streams. (e.g., Google won the case as a U.S. federal judge rejected the Department of Justice's attempt to break up the company by forcing it to sell off its Chrome web browser and Android operating system.)

    - If bearish, assign a rating BR0-BR2:
        BR0: A short-term headwind that does not threaten the company's fundamentals. Often creates a dip-buying opportunity.
            - Earnings miss due to one-time expense. (e.g., Microsoft misses EPS due to restructuring charges; cloud revenue growth remains strong.)
            - Product delay but no change in demand outlook. (e.g., Take-two delays the release of GTA 6 to 2026 due to quality concerns.)
            - Temporary regulatory fines. (e.g., Netflix faces $50M fine for violating the Children's Online Privacy Protection Act (COPPA) by collecting data from children under 13.)
            - Short-term macro concerns. (e.g., Rising interest rates pressure fintech valuations, but company fundamentals remain robust.)
            - Non-fanancial incident causing short-term disruption. (e.g., United Healthcare CEO got assassinated and caused a stock price drop.)
        BR1: A medium-term negative issue that signals real risk but can be fixed over time. Worth monitoring; may create long-term pressure.
            - Multiple quarters of declining revenue. (e.g., AMD reports its third consecutive quarter of client CPU revenue drops due to market share loss.)
            - A key product line underperforming. (e.g., Netflix reports its first subscriber decline in years due to competition from new streaming services.)
            - Supply chain disruption. (e.g., Nvidia price drops due toTSMC halts production at one of its factories due to a fire.)
            - Credit downgrade (but not to junk level). (e.g., Moody's downgrades Boeing due to rising debt from delayed aircraft deliveries.)
        BR2: Severe negative development that jeopardizes the company's core business model, stability, legality, solvency, or long-term viability. Major red flag.
            - Accounting fraud or financial manipulation discovered. (e.g., Enron's collapse due to fraudulent accounting practices.)
            - Key product banned by regulators. (e.g., FDA orders immediate halt of a top-selling drug due to safety issues.)
            - Large-scale data breach causing existential legal exposure. (e.g., Equifax breach exposing sensitive personal data of 145.5 million consumers.)
            - Force to sell off a major business unit. (e.g., If Apple sells off its entire Mac business to a third party, it would be a major strategic shift.)
            - Major supply chain disruption. (e.g., Nvidia price crash due to China-Taiwan war causing TSMC halts production at all of its factories.)

    - If neutral, sentiment rating should be null.

**Impact type:**
    - "foundation_changing" → equivalent to BL2 or BR2
    - "strategic_shift" → equivalent to BL1 or BR1
    - "short_term_catalyst" → equivalent to BL0 or BR0
    - "neutral" → for neutral sentiment or unclear significance

**Confidence:**
    - Integer 1-10 (1 = rumor/unverified, 10 = official and fully confirmed, 5 = medium confidence)

For each article, provide:

1. high_level_idea: A concise 4-5 sentence summary of the core news
2. company: The target company that user wants to analyze the news for
3. sentiment: "bullish", "bearish", or "neutral"
4. sentiment_rating: One of {{BL0, BL1, BL2, BR0, BR1, BR2}} or null if neutral
5. impact_type: One of the categories above
6. confidence: Integer 1-10
7. trading_insights: Brief actionable notes (e.g., expected market reaction, regulatory risk, upside drivers)

Confidence scale (1-10):
- 1-2: mostly unverified / missing key facts / tool failures
- 3-4: weak evidence; partial coverage; high uncertainty
- 5-6: moderate evidence; enough to be directionally useful
- 7-8: strong evidence from relevant tools; minor gaps only
- 9-10: very strong; primary-source confirmation where applicable (e.g., filings) and consistent across sources

Format the output strictly as JSON:
{{
  "title": "",
  "high_level_idea": "",
  "company": "",
  "sentiment": "",
  "sentiment_rating": "",
  "impact_type": "",
  "confidence": 1,
  "trading_insights": ""
}}"""

        user_message = f"""Analyze the following financial news article for trading decision-making for {{0}}:

URL: {url}

Analysis target company: {{0}}

Content:
{content}

Provide a comprehensive financial analysis in JSON format"""

        instruction = "You are a financial news analyst specializing in market-impact evaluation. I want you to generate a comprehensive financial analysis of the given financial article."

        # Append guidance to instruction
        maximum_guidance = (
            "This task requires detailed, high-granularity analysis. Gather as much verifiable information as you can, "
            "cross-check key facts across sources, and surface second-order implications. Use tools aggressively where helpful, "
            "and don’t stop at the first obvious answer. Prioritize primary sources (e.g., filings) when available, and clearly "
            "separate facts vs. inference."
        )
        standard_guidance = (
            "This task is standard priority. Apply a balanced analysis: focus on the highest-signal data first, then expand only "
            "if early results suggest meaningful follow-ups. Keep tool usage disciplined—aim for no more than 4 tool-iterations total, "
            "and in each iteration prefer 1–3 tool calls. Exceed these limits only if a result is genuinely interesting and additional "
            "calls are likely to change the conclusion."
        )
        minimal_guidance = (
            "This task is low priority. Keep analysis lightweight and cost-conscious. Use tools only if they are highly relevant and "
            "likely to resolve a key uncertainty quickly. Aim for 0–1 tool-iterations (hard cap 2). If the task can be answered without "
            "tools, do so and state assumptions/limits."
        )
        
        if quality_score < 4 and quality_score > 0:
            instruction += "\n\n" + minimal_guidance
            # Use summary as context text to save tokens
            content = summary
            max_tool_iterations = 2
            granularity = "minimal"
        elif quality_score >= 4 and quality_score < 7:
            instruction += "\n\n" + standard_guidance
            max_tool_iterations = 4
            granularity = "standard"
        elif quality_score >= 7 and quality_score <= 10:
            instruction += "\n\n" + maximum_guidance
            max_tool_iterations = 10
            granularity = "maximum"
        try:
            if self.logger:
                self.logger.debug(f"Analyzing news: {url} [quality_score: {quality_score}]")

            state = {"max_tool_iterations": max_tool_iterations, "granularity": granularity}
            # News/article inputs contain new external knowledge → opt into memory updates downstream.
            run_metadata = dict(metadata or {})
            run_metadata["url"] = url
            run_metadata["update_memory"] = True
            run_metadata["granularity"] = granularity
            team_resp = await self.team_manager.run_task(
                task_instruction=instruction,
                context_text=content,
                metadata=run_metadata,
                merge_system_message=system_message,
                merge_user_message=user_message,
                additional_states=state,
            )

            if not isinstance(team_resp, dict) or team_resp.get("status") != "success":
                err = None
                if isinstance(team_resp, dict):
                    err = team_resp.get("error")
                return {
                    "url": url,
                    "file_path": str(file_path),
                    "error": str(err or "Investment Research team failed"),
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                }

            # team_resp["result"] is team_out["final"] (envelope: ok/error/result/raw).
            final_payload = team_resp.get("result") if isinstance(team_resp.get("result"), dict) else {}
            analysis_data = final_payload.get("result") if isinstance(final_payload.get("result"), dict) else {}
            last_state = team_resp.get("last_state") if isinstance(team_resp.get("last_state"), dict) else {}

            by_ticker = analysis_data.get("by_ticker") if isinstance(analysis_data.get("by_ticker"), dict) else {}
            tickers_list = analysis_data.get("tickers") if isinstance(analysis_data.get("tickers"), list) else []
            tickers_list = [str(t).upper().strip() for t in tickers_list if str(t).strip()]

            per_ticker: Dict[str, Any] = {}
            # Each per-ticker entry mirrors the original analyze_news output (except url/file_path)
            # so it can be saved independently under /data/news/<ticker>.
            for t, payload in (by_ticker or {}).items():
                # Canonical macro key: use "economy" globally (lowercase).
                tt_raw = str(t or "").strip()
                tt = tt_raw.upper().strip() if tt_raw else "economy"
                payload_dict = payload if isinstance(payload, dict) else {}
                per_ticker[tt] = {
                    "url": url,  # Include URL for telegram push
                    "title": payload_dict.get("title", "Untitled"),
                    "high_level_idea": payload_dict.get("high_level_idea", ""),
                    # In the per-ticker design, the entry is already scoped to one ticker.
                    "companies": [tt] if tt else [],
                    "sentiment": payload_dict.get("sentiment", "neutral"),
                    "sentiment_rating": payload_dict.get("sentiment_rating"),
                    "impact_type": payload_dict.get("impact_type", "neutral"),
                    "confidence": payload_dict.get("confidence", 5),
                    "trading_insights": payload_dict.get("trading_insights", ""),
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    "model": self._team_merge_model(),
                    # additive fields (best-effort; may be graph-level, not per-run)
                    "routing": last_state.get("routing"),
                    "subagent_reports": last_state.get("subagent_reports"),
                    "evidence": last_state.get("evidence"),
                    "tool_trace": last_state.get("tool_trace"),
                }

            result = {
                "url": url,
                "file_path": str(file_path),
                "analyses_by_ticker": per_ticker,
                "tickers": tickers_list,
            }
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error analyzing news {url}: {e}", exc_info=True)
            return {
                "url": url,
                "file_path": str(file_path),
                "error": str(e),
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }

    async def analyze_trump_tweet(
        self,
        *,
        post: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze a Truth Social post payload (fileless).

        IMPORTANT: Keep the same output JSON structure as analyze_news:
        {
          url, file_path,
          analyses_by_ticker, tickers
        }
        """
        try:
            p = post if isinstance(post, dict) else {}
            content_raw = str(p.get("content") or "").strip()
            if not content_raw:
                return {
                    "url": str(p.get("post_url") or ""),
                    "file_path": "",
                    "error": "Empty content: missing content",
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                }

            post_url = str(p.get("post_url") or "").strip()
            post_id = str(p.get("post_id") or "").strip()
            user_handle = str(p.get("user_handle") or "realDonaldTrump").lstrip("@")
            timestamp = str(p.get("timestamp") or "").strip()
            media_urls = p.get("media_urls") if isinstance(p.get("media_urls"), list) else []
            market_impact = p.get("market_impact") if isinstance(p.get("market_impact"), dict) else None
            recent_posts = p.get("recent_posts") if isinstance(p.get("recent_posts"), list) else []
            analysis_instructions = str(p.get("analysis_instructions") or "").strip()

            # Build context_text directly from the post payload (include media URLs in context)
            ctx_lines = [
                "Source: Truth Social",
                "Author: @{} (current US President Donald J. Trump)".format(user_handle),
            ]
            if timestamp:
                ctx_lines.append(f"Timestamp: {timestamp}")
            if post_url:
                ctx_lines.append(f"URL: {post_url}")
            if post_id:
                ctx_lines.append(f"Post ID: {post_id}")
            if media_urls:
                ctx_lines.append("Media URLs (analyze these too):")
                for u in media_urls:
                    su = str(u).strip()
                    if su:
                        ctx_lines.append(f"- {su}")
            if market_impact:
                ctx_lines.append("Upstream market_impact (pre-filter) JSON:")
                ctx_lines.append(json.dumps(market_impact, ensure_ascii=False))

            # Include 48h historical posts (context) if provided by truthsocial_agent
            if recent_posts:
                ctx_lines.append("Recent Truth Social posts (last 48h context, newest first):")
                n = 0
                for item in recent_posts:
                    if n >= 50:
                        break
                    if not isinstance(item, dict):
                        continue
                    ts = str(item.get("timestamp") or "")
                    c = str(item.get("content") or "").strip()
                    if not c:
                        continue
                    pid = str(item.get("post_id") or "")
                    ctx_lines.append(f"- [{ts[:19]}] {c[:400]} ({pid[:12]}...)")
                    n += 1
            ctx_lines.append("")
            ctx_lines.append("Post:")
            ctx_lines.append(content_raw)
            context_text = "\n".join(ctx_lines).strip()

            # Keep the original analyze_news JSON contract unchanged.
            # Only prepend a small Trump-specific prefix.
            trump_prefix = (
                "IMPORTANT CONTEXT:\n"
                "- The input is a Truth Social post from the current US President Donald J. Trump.\n"
                "- Treat it as potentially market-moving political communication.\n"
                "- Consider immediate (minutes-hours) and near-term (days) market impact.\n\n"
            )
            
            # Add analysis instructions if provided
            if analysis_instructions:
                trump_prefix += f"ANALYSIS PRIORITY INSTRUCTIONS:\n{analysis_instructions}\n\n"

            # Reuse the same system_message text as analyze_news (verbatim), with the prefix above.
            system_message = trump_prefix + f"""You are a financial news analyst specializing in market-impact evaluation.
Your goal is to analyze financial news articles and provide structured insights for trading decisions.
Your input contains the news article content and the target company, along with the analysis results from different metrics. 
Your analysis of the news should center around the target company, and take into account the different metrics results and provide a comprehensive report of how does the news affect the target company.

Analysis results should follow these rules:

**Sentiment:**
    - "bullish", "bearish", or "neutral"

**Sentiment rating:** (rating are not limited to these examples)
    - If bullish, assign a rating BL0-BL2 based on these tiers:
        BL0: A positive development likely to lift the stock only briefly; does not materially strengthen the business. Market may correct quickly.
            - A company beats earnings by a small margin. (e.g., Apple reports EPS $0.05 above expectations due to one-time seasonal demand. Guidance unchanged.)
            - A short-term partnership announcement. (e.g., Starbucks partners with a local brand for a limited holiday drink menu in China.)
            - Regulatory approval for a minor product update. (e.g., Tesla receives approval for software update rollout in Europe (no new features affecting revenue scale).)
            - Temporary macro boost. (e.g., Oil prices dip, boosting airline stocks for this quarter only.)
        BL1: A solid medium-to-long-term positive catalyst if the company executes well. Could support continued upward movement.
            - Company enters a growing adjacent market. (e.g., Nvidia announces full expansion into automotive embedded systems, but revenue impact depends on adoption.)
            - Large strategic partnership. (e.g., Microsoft partners with OpenAI to integrate ChatGPT into Office products.)
            - Long-term cost reduction initiative. (e.g., Intel announces $30B cost reduction plan over 5 years.)
            - Strong product launch with uncertain competition response (e.g., Apple announces new MacBook with M chips, but no clear competitive advantage over existing models.)
        BL2: A major positive event that reshapes the company's long-term prospects (e.g., entry into a major new profitable market).
            - Company enters a massive new market with credible advantage. (e.g., Amazon receives FDA approval for a nationwide digital pharmacy platform, with potential for significant revenue growth.)
            - Breakthrough product with proven demand. (e.g., Google announces Gemini 3.0 with 10x performance boost, likely to disrupt AI markets.)
            - Strategic acquisition that transforms capabilities. (e.g., Microsoft acquires Activision Blizzard, adding major game franchises to Xbox/Game Pass ecosystem.)
            - Major regulatory victory that opens new revenue streams. (e.g., Google won the case as a U.S. federal judge rejected the Department of Justice's attempt to break up the company by forcing it to sell off its Chrome web browser and Android operating system.)

    - If bearish, assign a rating BR0-BR2:
        BR0: A short-term headwind that does not threaten the company's fundamentals. Often creates a dip-buying opportunity.
            - Earnings miss due to one-time expense. (e.g., Microsoft misses EPS due to restructuring charges; cloud revenue growth remains strong.)
            - Product delay but no change in demand outlook. (e.g., Take-two delays the release of GTA 6 to 2026 due to quality concerns.)
            - Temporary regulatory fines. (e.g., Netflix faces $50M fine for violating the Children's Online Privacy Protection Act (COPPA) by collecting data from children under 13.)
            - Short-term macro concerns. (e.g., Rising interest rates pressure fintech valuations, but company fundamentals remain robust.)
            - Non-fanancial incident causing short-term disruption. (e.g., United Healthcare CEO got assassinated and caused a stock price drop.)
        BR1: A medium-term negative issue that signals real risk but can be fixed over time. Worth monitoring; may create long-term pressure.
            - Multiple quarters of declining revenue. (e.g., AMD reports its third consecutive quarter of client CPU revenue drops due to market share loss.)
            - A key product line underperforming. (e.g., Netflix reports its first subscriber decline in years due to competition from new streaming services.)
            - Supply chain disruption. (e.g., Nvidia price drops due toTSMC halts production at one of its factories due to a fire.)
            - Credit downgrade (but not to junk level). (e.g., Moody's downgrades Boeing due to rising debt from delayed aircraft deliveries.)
        BR2: Severe negative development that jeopardizes the company's core business model, stability, legality, solvency, or long-term viability. Major red flag.
            - Accounting fraud or financial manipulation discovered. (e.g., Enron's collapse due to fraudulent accounting practices.)
            - Key product banned by regulators. (e.g., FDA orders immediate halt of a top-selling drug due to safety issues.)
            - Large-scale data breach causing existential legal exposure. (e.g., Equifax breach exposing sensitive personal data of 145.5 million consumers.)
            - Force to sell off a major business unit. (e.g., If Apple sells off its entire Mac business to a third party, it would be a major strategic shift.)
            - Major supply chain disruption. (e.g., Nvidia price crash due to China-Taiwan war causing TSMC halts production at all of its factories.)

    - If neutral, sentiment rating should be null.

**Impact type:**
    - "foundation_changing" → equivalent to BL2 or BR2
    - "strategic_shift" → equivalent to BL1 or BR1
    - "short_term_catalyst" → equivalent to BL0 or BR0
    - "neutral" → for neutral sentiment or unclear significance

**Confidence:**
    - Integer 1-10 (1 = rumor/unverified, 10 = official and fully confirmed, 5 = medium confidence)

For each article, provide:

1. high_level_idea: A concise 4-5 sentence summary of the core news
2. company: The target company that user wants to analyze the news for
3. sentiment: "bullish", "bearish", or "neutral"
4. sentiment_rating: One of {{BL0, BL1, BL2, BR0, BR1, BR2}} or null if neutral
5. impact_type: One of the categories above
6. confidence: Integer 1-10
7. trading_insights: Brief actionable notes (e.g., expected market reaction, regulatory risk, upside drivers)

Confidence scale (1-10):
- 1-2: mostly unverified / missing key facts / tool failures
- 3-4: weak evidence; partial coverage; high uncertainty
- 5-6: moderate evidence; enough to be directionally useful
- 7-8: strong evidence from relevant tools; minor gaps only
- 9-10: very strong; primary-source confirmation where applicable (e.g., filings) and consistent across sources

Format the output strictly as JSON:
{{
  "title": "",
  "high_level_idea": "",
  "company": "",
  "sentiment": "",
  "sentiment_rating": "",
  "impact_type": "",
  "confidence": 1,
  "trading_insights": ""
}}"""

            # Keep output JSON contract unchanged; only change wording of the “input type”.
            user_message = f"""Analyze the following Truth Social post for trading decision-making for {{0}}:

URL: {post_url}

Content:
{context_text}

Provide a comprehensive financial analysis in JSON format"""

            instruction = (
                "You are a financial news analyst specializing in market-impact evaluation. "
                "I want you to generate a comprehensive financial analysis of the given Truth Social post "
                "from the current US President Donald J. Trump."
            )
            
            # Add analysis instructions if provided
            if analysis_instructions:
                instruction += "\n\n" + analysis_instructions

            # Choose tool budget based on pre-filter impact (if present)
            impact = ""
            if isinstance(market_impact, dict):
                impact = str(market_impact.get("impact") or "").strip().lower()

            maximum_guidance = (
                "This task requires detailed, high-granularity analysis. Gather as much verifiable information as you can, "
                "cross-check key facts across sources, and surface second-order implications. Use tools aggressively where helpful, "
                "and don't stop at the first obvious answer. Prioritize primary sources (e.g., filings) when available, and clearly "
                "separate facts vs. inference."
            )
            standard_guidance = (
                "This task is standard priority. Apply a balanced analysis: focus on the highest-signal data first, then expand only "
                "if early results suggest meaningful follow-ups. Keep tool usage disciplined—aim for no more than 4 tool-iterations total, "
                "and in each iteration prefer 1–3 tool calls. Exceed these limits only if a result is genuinely interesting and additional "
                "calls are likely to change the conclusion."
            )
            minimal_guidance = (
                "This task is low priority. Keep analysis lightweight and cost-conscious. Use tools only if they are highly relevant and "
                "likely to resolve a key uncertainty quickly. Aim for 0–1 tool-iterations (hard cap 2). If the task can be answered without "
                "tools, do so and state assumptions/limits."
            )

            if impact == "high":
                instruction += "\n\n" + maximum_guidance
                max_tool_iterations = 10
                granularity = "maximum"
            elif impact == "medium":
                instruction += "\n\n" + standard_guidance
                max_tool_iterations = 4
                granularity = "standard"
            else:
                instruction += "\n\n" + minimal_guidance
                max_tool_iterations = 2
                granularity = "minimal"

            if self.logger:
                self.logger.debug(f"Analyzing Trump post: {post_url or post_id} [granularity: {granularity}]")

            state = {"max_tool_iterations": max_tool_iterations, "granularity": granularity}
            run_metadata = dict(metadata or {})
            run_metadata["url"] = post_url
            run_metadata["update_memory"] = True
            run_metadata["granularity"] = granularity
            run_metadata["kind"] = "trump_post"
            run_metadata["post_id"] = post_id
            run_metadata["user_handle"] = user_handle

            team_resp = await self.team_manager.run_task(
                task_instruction=instruction,
                context_text=context_text,
                metadata=run_metadata,
                merge_system_message=system_message,
                merge_user_message=user_message,
                additional_states=state,
            )

            if not isinstance(team_resp, dict) or team_resp.get("status") != "success":
                err = None
                if isinstance(team_resp, dict):
                    err = team_resp.get("error")
                return {
                    "url": post_url,
                    "file_path": "",
                    "error": str(err or "Investment Research team failed"),
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                }

            final_payload = team_resp.get("result") if isinstance(team_resp.get("result"), dict) else {}
            analysis_data = final_payload.get("result") if isinstance(final_payload.get("result"), dict) else {}
            last_state = team_resp.get("last_state") if isinstance(team_resp.get("last_state"), dict) else {}

            by_ticker = analysis_data.get("by_ticker") if isinstance(analysis_data.get("by_ticker"), dict) else {}
            tickers_list = analysis_data.get("tickers") if isinstance(analysis_data.get("tickers"), list) else []
            tickers_list = [str(t).upper().strip() for t in tickers_list if str(t).strip()]

            per_ticker: Dict[str, Any] = {}
            for t, payload in (by_ticker or {}).items():
                tt_raw = str(t or "").strip()
                tt = tt_raw.upper().strip() if tt_raw else "economy"
                payload_dict = payload if isinstance(payload, dict) else {}
                per_ticker[tt] = {
                    "url": post_url,  # Include URL for telegram push
                    "title": payload_dict.get("title", "Untitled"),
                    "high_level_idea": payload_dict.get("high_level_idea", ""),
                    "companies": [tt] if tt else [],
                    "sentiment": payload_dict.get("sentiment", "neutral"),
                    "sentiment_rating": payload_dict.get("sentiment_rating"),
                    "impact_type": payload_dict.get("impact_type", "neutral"),
                    "confidence": payload_dict.get("confidence", 5),
                    "trading_insights": payload_dict.get("trading_insights", ""),
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    "model": self._team_merge_model(),
                    "routing": last_state.get("routing"),
                    "subagent_reports": last_state.get("subagent_reports"),
                    "evidence": last_state.get("evidence"),
                    "tool_trace": last_state.get("tool_trace"),
                }

            return {
                "url": post_url,
                "file_path": "",
                "analyses_by_ticker": per_ticker,
                "tickers": tickers_list,
            }
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error analyzing Trump post {post.get('post_url')}: {e}", exc_info=True)
            return {
                "url": str((post or {}).get("post_url") or ""),
                "file_path": "",
                "error": str(e),
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }

    def save_news_analysis_result(self, analysis_result: Dict[str, Any], base_data_dir: Path) -> None:
        """
        Save an analysis result to company-specific folders.

        Expects the news endpoint envelope shape:
        - top-level `url` and `file_path`
        - `analyses_by_ticker` mapping ticker -> analysis payload (without url/file_path)
        """
        if "error" in analysis_result:
            return

        try:
            # Use UTC to ensure /data/news/<ticker>/<year>/<month> is stable across hosts/timezones.
            now = datetime.now(timezone.utc)
            year = now.strftime("%Y")
            month = now.strftime("%m")

            url = str(analysis_result.get("url") or "").strip()
            if url:
                filename = f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"
            else:
                filename = now.strftime("%Y_%m_%d_%H_%M.json")

            analyses_by_ticker = (
                analysis_result.get("analyses_by_ticker")
                if isinstance(analysis_result.get("analyses_by_ticker"), dict)
                else {}
            )

            def normalize_symbol(symbol: str) -> str:
                if not symbol:
                    return ""
                # Keep economy folder lower-case for stability (`/data/news/economy`).
                if str(symbol).strip().lower() == "economy":
                    return "economy"
                return re.sub(r"[^A-Z0-9]", "", symbol.upper())

            # Persist one file per ticker entry under /data/news/<ticker>/...
            for ticker_key, payload in (analyses_by_ticker or {}).items():
                folder_name = normalize_symbol(str(ticker_key))
                if not folder_name:
                    folder_name = "economy"
                try:
                    save_dir = base_data_dir / folder_name / year / month
                    save_dir.mkdir(parents=True, exist_ok=True)
                    out_path = save_dir / filename
                    with open(out_path, "w", encoding="utf-8") as f:
                        per_record = {
                            "url": analysis_result.get("url"),
                            "file_path": analysis_result.get("file_path"),
                            **(payload if isinstance(payload, dict) else {}),
                        }
                        json.dump(per_record, f, indent=2, ensure_ascii=False)
                    if self.logger:
                        self.logger.debug(f"Saved analysis result to: {out_path}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Failed to save analysis to {folder_name}: {e}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error saving analysis result: {e}")


