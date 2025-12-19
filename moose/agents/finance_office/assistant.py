from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional


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
        return result
        
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
                "analyzed_at": datetime.now().isoformat(),
            }

        if not isinstance(payload, dict):
            return {
                "url": url,
                "file_path": str(file_path),
                "error": "Invalid article format: expected JSON object",
                "analyzed_at": datetime.now().isoformat(),
            }

        # News scraper now persists JSON-only: {"summary": "...", "raw_article": "..."}.
        content = str(payload.get("raw_article") or "").strip()
        if not content:
            return {
                "url": url,
                "file_path": str(file_path),
                "error": "Empty content: missing raw_article",
                "analyzed_at": datetime.now().isoformat(),
            }
        
        quality_score = int(payload.get("quality_score", 0))
        summary = str(payload.get("summary") or "").strip()

        # Preserve existing news contract prompt (system + user)
        system_message = f"""You are a financial news analyst specializing in market-impact evaluation.
Your goal is to analyze financial news articles and provide structured insights for trading decisions.
Your input contains the news article content, along with the analysis results from different metrics. 
Your analysis should take into account the different metrics results and provide a comprehensive analysis of the news article.

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
2. companies: Array of tickers mentioned, leave empty array if no tickers are mentioned
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
  "companies": [],
  "sentiment": "",
  "sentiment_rating": "",
  "impact_type": "",
  "confidence": 1,
  "trading_insights": ""
}}"""

        user_message = f"""Analyze the following financial news article for trading decision-making:

URL: {url}

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
            "if early results suggest meaningful follow-ups. Keep tool usage disciplined—aim for no more than 2 tool-iterations total, "
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
        elif quality_score >= 4 and quality_score < 7:
            instruction += "\n\n" + standard_guidance
            max_tool_iterations = 4
        elif quality_score >= 7 and quality_score <= 10:
            instruction += "\n\n" + maximum_guidance
            max_tool_iterations = 10
            
        try:
            if self.logger:
                self.logger.debug(f"Analyzing news: {url} [quality_score: {quality_score}]")

            state = {"max_tool_iterations": max_tool_iterations}
            team_resp = await self.team_manager.run_task(
                instruction=instruction,
                context_text=content,
                metadata={"url": url, **(metadata or {})},
                system_message=system_message,
                user_message=user_message,
                task_goal="news_analysis",
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
                    "analyzed_at": datetime.now().isoformat(),
                }

            # team_resp["result"] is team_out["final"] (envelope: ok/error/result/raw).
            final_payload = team_resp.get("result") if isinstance(team_resp.get("result"), dict) else {}
            analysis_data = final_payload.get("result") if isinstance(final_payload.get("result"), dict) else {}
            last_state = team_resp.get("last_state") if isinstance(team_resp.get("last_state"), dict) else {}

            result = {
                "url": url,
                "file_path": str(file_path),
                "title": analysis_data.get("title", "Untitled"),
                "high_level_idea": analysis_data.get("high_level_idea", ""),
                "companies": analysis_data.get("companies", []),
                "sentiment": analysis_data.get("sentiment", "neutral"),
                "sentiment_rating": analysis_data.get("sentiment_rating"),
                "impact_type": analysis_data.get("impact_type", "neutral"),
                "confidence": analysis_data.get("confidence", 5),
                "trading_insights": analysis_data.get("trading_insights", ""),
                "analyzed_at": datetime.now().isoformat(),
                # For news analysis, final output comes from team_merge; report that model when available.
                "model": self._team_merge_model(),
                # additive fields (optional)
                "routing": last_state.get("routing"),
                "subagent_reports": last_state.get("subagent_reports"),
                "evidence": last_state.get("evidence"),
                "tool_trace": last_state.get("tool_trace"),
            }
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error analyzing news {url}: {e}", exc_info=True)
            return {
                "url": url,
                "file_path": str(file_path),
                "error": str(e),
                "analyzed_at": datetime.now().isoformat(),
            }

    def save_news_analysis_result(self, analysis_result: Dict[str, Any], base_data_dir: Path) -> None:
        """
        Save an analysis result to company-specific folders.

        Expects the news endpoint envelope shape (top-level `url` and `companies`).
        """
        if "error" in analysis_result:
            return

        try:
            now = datetime.now()
            year = now.strftime("%Y")
            month = now.strftime("%m")

            url = str(analysis_result.get("url") or "").strip()
            if url:
                filename = f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"
            else:
                filename = now.strftime("%Y_%m_%d_%H_%M.json")

            companies = analysis_result.get("companies", [])

            def normalize_symbol(symbol: str) -> str:
                if not symbol:
                    return ""
                return re.sub(r"[^A-Z0-9]", "", symbol.upper())

            if not companies:
                save_folders = ["economy"]
            else:
                normalized_symbols = []
                for company in companies:
                    normalized = normalize_symbol(str(company))
                    if normalized and normalized not in normalized_symbols:
                        normalized_symbols.append(normalized)
                save_folders = normalized_symbols or ["economy"]

            for folder_name in save_folders:
                try:
                    save_dir = base_data_dir / folder_name / year / month
                    save_dir.mkdir(parents=True, exist_ok=True)
                    out_path = save_dir / filename
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(analysis_result, f, indent=2, ensure_ascii=False)
                    if self.logger:
                        self.logger.debug(f"Saved analysis result to: {out_path}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Failed to save analysis to {folder_name}: {e}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error saving analysis result: {e}")


