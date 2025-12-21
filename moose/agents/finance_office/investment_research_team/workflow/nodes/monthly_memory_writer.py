from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from langchain_core.tools import StructuredTool
    LANGCHAIN_TOOLS_AVAILABLE = True
except Exception:  # pragma: no cover
    StructuredTool = None  # type: ignore
    LANGCHAIN_TOOLS_AVAILABLE = False

from moose.framework.llm_core import LLMClient
from moose.framework.llm_core.models import Message, MessageRole

from .utils import extract_json
from .base import BaseNode


class MonthlyMemoryWriterNode(BaseNode):
    """
    Node name: `write_monthly_memory`
    Config key: `custom.memory_summarizer_llm_config` (resolved via get_node_llm_config(\"memory_summarizer\"))

    Runs only for neutral analysis; maintains:
    `/data/news/<ticker>/<year>/<month>/memory.json` (UTC bucket)

    Memory schema:
    {
      "sentiment": "bullish|bearish|neutral",
      "trading_insights": "...",
      "parameters": {"sentiment_number": x, "memory_weight": x},
      "memory_list": [{"memory_title": "...", "file_path": "...", "confidence": x}, ...]
    }
    """

    def __init__(self, *, analyzer: Any, logger: Any):
        super().__init__(analyzer=analyzer, logger=logger)
        self.node_name = "write_monthly_memory"
        # Note: config key is `memory_summarizer` (node name differs from config key intentionally)
        self.agent_client = self._build_agent_client(node_name="memory_summarizer", tools=[])

    @staticmethod
    def compute_memory_parameter(
        existing_params: Optional[Dict[str, Any]],
        analyses: Union[Dict[str, Any], List[Dict[str, Any]], Any],
        operation: str = "add",
    ) -> Dict[str, float]:
        """
        Compute updated monthly memory parameters from prior parameters and analysis records.

        Aggregates analysis `sentiment` and `confidence` into:
        - `sentiment_number`: signed score trending bullish (>0), bearish (<0), neutral (=0)
        - `memory_weight`: accumulated weight representing how strong the month's memory is

        Args:
            existing_params: Optional previous `parameters` dict (e.g., from memory.json).
                Expected keys (best-effort): `sentiment_number`, `memory_weight` (float-like).
            analyses: Analysis dicts. Expected keys (best-effort):
                - `sentiment`: "bullish" | "bearish" | "neutral"
                - `confidence`: numeric (1–10). Non-positive values are ignored.
            operation: "add" to add contributions (default), "subtract" to remove contributions.

        Returns:
            Dict[str, float]: {"sentiment_number": float, "memory_weight": float}
        """
        allowed_sentiments = {"bullish", "bearish", "neutral"}
        existing_params = existing_params if isinstance(existing_params, dict) else {}
        try:
            sentiment_number = float(existing_params.get("sentiment_number") or 0.0)
        except Exception:
            sentiment_number = 0.0
        try:
            memory_weight = float(existing_params.get("memory_weight") or 0.0)
        except Exception:
            memory_weight = 0.0

        if isinstance(analyses, dict):
            items: List[Dict[str, Any]] = [analyses]
        elif isinstance(analyses, list):
            items = [a for a in analyses if isinstance(a, dict)]
        else:
            items = []

        # Determine if we're adding or subtracting
        is_subtract = str(operation).strip().lower() == "subtract"
        multiplier = -1.0 if is_subtract else 1.0

        for a in items:
            sentiment = str(a.get("sentiment") or "").strip().lower()
            if sentiment not in allowed_sentiments:
                sentiment = "neutral"
            try:
                confidence = float(a.get("confidence") or 0.0)
            except Exception:
                confidence = 0.0
            confidence = max(0.0, confidence)
            # Keep missing/invalid confidence from contributing to the memory.
            if confidence <= 0.0:
                continue
            conf2 = confidence * confidence
            
            # When subtracting, we need to compute bonus before updating memory_weight
            if is_subtract:
                bonus = (memory_weight / conf2) * (0.1 / 0.5) if conf2 > 0 else 0.0
                memory_weight = memory_weight - (conf2 * 0.1)
            else:
                memory_weight = memory_weight + (conf2 * 0.1)
                bonus = (memory_weight / conf2) * (0.1 / 0.5) if conf2 > 0 else 0.0
            
            # Prevent negative memory_weight
            memory_weight = max(0.0, memory_weight)
            
            if sentiment == "bullish":
                sentiment_number = sentiment_number + multiplier * (confidence + bonus)
            elif sentiment == "bearish":
                sentiment_number = sentiment_number - multiplier * (confidence + bonus)
            else:
                sentiment_number = sentiment_number + multiplier * bonus

        return {"sentiment_number": float(sentiment_number), "memory_weight": float(memory_weight)}

    @staticmethod
    def manage_memory_list(
        existing_list: Optional[List[Dict[str, Any]]],
        analyses: Union[Dict[str, Any], List[Dict[str, Any]], Any],
        ticker: Optional[str] = None,
        max_memories: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Build/update a list of memory entries, maintaining up to max_memories entries.

        Args:
            existing_list: Optional previous `memory_list` (e.g., from memory.json).
                List of dicts with keys: `memory_title`, `file_path`, `confidence`.
            analyses: Analysis dicts. Expected keys (best-effort):
                - `url`: str URL of the news article (used to compute analysis file path)
                - `file_path`: str fallback path if URL not available
                - `title` or `memory_title`: str title of the memory
                - `confidence`: numeric (1–10)
            ticker: Optional ticker symbol (e.g., "AAPL"). If provided, computes analysis file paths from URLs.

        Returns:
            List[Dict[str, Any]]: List of memory dicts (deduplicated by file_path, newest first).
        """
        # Start with existing list
        result: List[Dict[str, Any]] = []
        seen_paths: set = set()
        
        if isinstance(existing_list, list):
            for item in existing_list:
                if not isinstance(item, dict):
                    continue
                fp = item.get("file_path")
                if not isinstance(fp, str) or not fp.strip():
                    continue
                if fp in seen_paths:
                    continue
                
                memory_entry = {
                    "memory_title": str(item.get("memory_title") or ""),
                    "file_path": fp,
                    "confidence": float(item.get("confidence") or 0.0),
                }
                result.append(memory_entry)
                seen_paths.add(fp)
        
        # Add new analyses
        if isinstance(analyses, dict):
            items: List[Dict[str, Any]] = [analyses]
        elif isinstance(analyses, list):
            items = [a for a in analyses if isinstance(a, dict)]
        else:
            items = []
        
        # Compute directory path for analysis files if ticker is provided
        dir_path: Optional[Path] = None
        if ticker:
            base_news_dir = os.getenv("NEWS_RESULT_DIR", "/data/news") or "/data/news"
            now = datetime.now(timezone.utc)
            year = now.strftime("%Y")
            month = now.strftime("%m")
            dir_path = Path(base_news_dir) / ticker / year / month
        
        for a in items:
            # Compute the analysis file path from URL hash if available
            url = str(a.get("url") or "").strip()
            analysis_file_path: Optional[str] = None
            
            if url and dir_path:
                # Use URL hash to compute the analysis result file path
                filename = f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"
                analysis_file_path = str(dir_path / filename)
            
            if not analysis_file_path or analysis_file_path in seen_paths:
                continue
            
            # Extract title (prefer memory_title, fallback to title)
            title = str(a.get("memory_title") or a.get("title") or "")
            try:
                confidence = float(a.get("confidence") or 0.0)
            except Exception:
                confidence = 0.0
            
            memory_entry = {
                "memory_title": title,
                "file_path": analysis_file_path,
                "confidence": confidence,
            }
            result.append(memory_entry)
            seen_paths.add(analysis_file_path)
        
        # Limit to max_memories (keep newest, which are at the end after appending new analyses)
        if len(result) > max_memories:
            result = result[-max_memories:]
        
        return result

    @staticmethod
    def remove_memories_from_list(
        existing_list: Optional[List[Dict[str, Any]]],
        file_paths_to_remove: Union[str, List[str]],
    ) -> List[Dict[str, Any]]:
        """
        Remove specific memories from the list by file_path.

        Args:
            existing_list: Current memory_list.
            file_paths_to_remove: Single file_path string or list of file_paths to remove.

        Returns:
            List[Dict[str, Any]]: Updated memory list with specified paths removed.
        """
        if not isinstance(existing_list, list):
            return []
        
        # Normalize to list
        if isinstance(file_paths_to_remove, str):
            paths_to_remove = {file_paths_to_remove}
        elif isinstance(file_paths_to_remove, list):
            paths_to_remove = {str(p) for p in file_paths_to_remove if isinstance(p, str)}
        else:
            paths_to_remove = set()
        
        # Filter out memories with matching file_paths
        result = []
        for mem in existing_list:
            if not isinstance(mem, dict):
                continue
            fp = mem.get("file_path")
            if not isinstance(fp, str) or not fp.strip():
                continue
            if fp not in paths_to_remove:
                result.append(mem)
        
        return result

    @staticmethod
    def compute_memory_weight_ratio(*, existing_memory_weight: float, latest_confidence: float) -> float:
        """
        Compute the relative weight of existing memory vs the latest analysis.

        Definition:
            latest_weight_unit = (latest_confidence^2) * 0.1
            ratio = existing_memory_weight / latest_weight_unit

        Args:
            existing_memory_weight: Existing memory.json.parameters.memory_weight (0 if no existing memory).
            latest_confidence: latest_merge_result.confidence (0 if missing).

        Returns:
            float: ratio (capped to 1e9). Returns 0 when existing_memory_weight <= 0.
        """
        try:
            ew = float(existing_memory_weight or 0.0)
        except Exception:
            ew = 0.0
        if ew <= 0.0:
            return 0.0
        try:
            lc = float(latest_confidence or 0.0)
        except Exception:
            lc = 0.0
        conf2 = max(0.0, lc) * max(0.0, lc)
        latest_weight_unit = conf2 * 0.1
        if latest_weight_unit <= 0.0:
            return 1e9
        try:
            r = ew / latest_weight_unit
        except Exception:
            r = 1e9
        if r < 0.0:
            return 0.0
        return float(min(r, 1e9))

    @staticmethod
    def derive_sentiment_tool(*, sentiment_number: float) -> str:
        """
        Convert a signed sentiment score into a discrete sentiment label.

        Args:
            sentiment_number: Signed sentiment score. Negative => bearish, positive => bullish, zero => neutral.

        Returns:
            str: One of \"bullish\", \"bearish\", or \"neutral\".
        """
        try:
            sn = float(sentiment_number or 0.0)
        except Exception:
            sn = 0.0
        if sn < 0:
            return "bearish"
        if sn > 0:
            return "bullish"
        return "neutral"

    async def llm_trading_insights(
        self,
        *,
        ticker: str,
        year: str,
        month: str,
        latest_merge_result: Dict[str, Any],
        existing_memory: Optional[Dict[str, Any]],
        monthly_articles: Optional[List[Dict[str, Any]]],
    ) -> str:
        """
        Legacy (non-tool) LLM call to generate only the `trading_insights` string.

        Args:
            ticker: Target ticker symbol.
            year: UTC year bucket (YYYY).
            month: UTC month bucket (MM).
            latest_merge_result: Latest analysis JSON (from team_merge result).
            existing_memory: Existing monthly memory.json contents if present.
            monthly_articles: List of month analysis JSON objects if bootstrapping.

        Returns:
            str: Trading insights string (truncated to <=1200 chars).
        """
        system_message = """You are a summarizer for monthly news/article analysis results for a single stock ticker.

Goal:
- Produce brief actionable monthly trading insights for the ticker, grounded in the provided article analysis JSON outputs.

Mandatory rules:
- Return STRICT JSON only (no markdown, no extra keys).
- You MUST return exactly:
  {"trading_insights": "..."}
"""

        messages: List[Message] = []
        intro = {
            "ticker": ticker,
            "year": year,
            "month": month,
            "mode": "update" if isinstance(existing_memory, dict) else "bootstrap",
            "note": "JSON docs follow as separate messages.",
        }
        messages.append(Message(role=MessageRole.USER, content=f"Task setup:\n{json.dumps(intro, ensure_ascii=False, indent=2)}"))

        final_request = 'Return STRICT JSON only: {"trading_insights":"..."}'
        # Iterative bootstrap/update: for each monthly article, treat it like a "latest_merge_result"
        # and feed the generated JSON back as the next-round memory (so the next article only sees the new memory).
        memory_msg_idx: Optional[int] = None
        latest_msg_idx: Optional[int] = None

        current_memory: Optional[Dict[str, Any]] = existing_memory if isinstance(existing_memory, dict) else None
        if current_memory is not None:
            messages.append(Message(role=MessageRole.USER, content=json.dumps({"memory": current_memory}, ensure_ascii=False)))
            memory_msg_idx = len(messages) - 1

        if current_memory is None:
            for a in (monthly_articles or []):
                # Replace any previous latest_merge_result message
                if latest_msg_idx is not None and latest_msg_idx < len(messages):
                    messages.pop(latest_msg_idx)
                    latest_msg_idx = None

                messages.append(Message(role=MessageRole.USER, content=json.dumps({"latest_merge_result": a}, ensure_ascii=False)))
                latest_msg_idx = len(messages) - 1

                # IMPORTANT: message must be a string for LLMClient.send_message
                resp = await self.agent_client.send_message(message=final_request, messages=messages, system_message=system_message)
                out = extract_json(getattr(resp, "content", "") or "")
                if not isinstance(out, dict):
                    continue

                # Pop old memory + latest_merge_result, keep only new memory for next round
                if latest_msg_idx is not None and latest_msg_idx < len(messages):
                    messages.pop(latest_msg_idx)
                    latest_msg_idx = None
                if memory_msg_idx is not None and memory_msg_idx < len(messages):
                    messages.pop(memory_msg_idx)
                    memory_msg_idx = None

                current_memory = out
                messages.append(Message(role=MessageRole.USER, content=json.dumps({"memory": current_memory}, ensure_ascii=False)))
                memory_msg_idx = len(messages) - 1

        # Final trading_insights pass for the newest analysis
        if latest_msg_idx is not None and latest_msg_idx < len(messages):
            messages.pop(latest_msg_idx)
        messages.append(Message(role=MessageRole.USER, content=json.dumps({"latest_merge_result": latest_merge_result}, ensure_ascii=False)))
        latest_msg_idx = len(messages) - 1

        last_err: Optional[str] = None
        for _ in range(3):
            resp = await self.agent_client.send_message(message=final_request, messages=messages, system_message=system_message)
            out = extract_json(getattr(resp, "content", "") or "")
            if isinstance(out, dict):
                ti = str(out.get("trading_insights") or "").strip()
                if ti:
                    return ti[:1200] + ("…" if len(ti) > 1200 else "")
            last_err = "memory_summarizer returned invalid JSON or empty trading_insights."
        raise RuntimeError(last_err or "memory_summarizer failed")

    async def llm_monthly_memory_summary(
        self,
        *,
        ticker: str,
        year: str,
        month: str,
        latest_merge_result: Dict[str, Any],
        existing_memory: Optional[Dict[str, Any]],
        monthly_articles: Optional[List[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """
        Tool-calling summarizer: returns full monthly memory payload:
        {sentiment, trading_insights, memory_weight_ratio, parameters, memory_list}

        Returns None if tool calling is unavailable or the model output fails validation.
        """
        allowed_sentiments = {"bullish", "bearish", "neutral"}
        if not LANGCHAIN_TOOLS_AVAILABLE or StructuredTool is None:
            return None

        compute_params_tool = StructuredTool.from_function(  # type: ignore[union-attr]
            func=MonthlyMemoryWriterNode.compute_memory_parameter,
            name="compute_memory_parameter",
            description="Compute updated memory parameters from existing_params and a list of analyses. Returns {sentiment_number, memory_weight}.",
        )
        # Create partial function with ticker pre-filled to compute analysis file paths from URLs
        from functools import partial
        manage_list_tool = StructuredTool.from_function(  # type: ignore[union-attr]
            func=MonthlyMemoryWriterNode.manage_memory_list,
            name="manage_memory_list",
            description="Build/update memory_list from existing_list and analyses (max 30). Computes analysis file paths from URLs. Returns list of dicts with memory_title, file_path, confidence.",
        )
        ratio_tool = StructuredTool.from_function(  # type: ignore[union-attr]
            func=MonthlyMemoryWriterNode.compute_memory_weight_ratio,
            name="compute_memory_weight_ratio",
            description="Compute ratio = existing_memory_weight / (latest_confidence^2 * 0.1). Returns float; 0 means no existing memory weight.",
        )
        derive_sentiment_struct_tool = StructuredTool.from_function(  # type: ignore[union-attr]
            func=MonthlyMemoryWriterNode.derive_sentiment_tool,
            name="derive_sentiment",
            description="Derive sentiment from sentiment_number: <0 bearish, >0 bullish, ==0 neutral.",
        )

        cfg = self._node_cfg("memory_summarizer")
        tool_client = LLMClient(
            model=str(cfg.get("model") or "").strip(),
            temperature=float(cfg.get("temperature", 0.7)),
            tools=[ratio_tool, compute_params_tool, manage_list_tool, derive_sentiment_struct_tool],
            enable_multi_stage_reasoning=True,
            max_tool_iterations=6,
            agent_name=self.main_agent_name,
            **(cfg.get("kwargs") or {}),
        )

        system_message = """You are a monthly memory summarizer for a single ticker.

You have tools available and you MUST use them (do not compute these values manually).

Required workflow (follow exactly):
1) Call tool `compute_memory_weight_ratio` with:
   - existing_memory_weight = memory.parameters.memory_weight
   - latest_confidence = latest_merge_result.confidence
   Put the returned number into output key: `memory_weight_ratio`.

2) Use `memory_weight_ratio` to write `trading_insights` that combines:
   - existing monthly memory (if provided)
   - latest_merge_result (newest analysis)
   - monthly_articles (if provided)
   Interpretation: 
   The memory_weight_ratio represents the weight of existing memory compared to the latest merge result.
   - ratio < 1: latest result dominates, e.g., ratio = 0.5 means latest result influences 2x more than existing memory on generating the trading insights in new memory.
   - ratio == 1: equal, e.g., ratio = 1 means latest result and existing memory influence equally on generating the trading insights in new memory.
   - ratio > 1: existing dominates, e.g., ratio = 5 means existing memory influences 5x more than latest result on generating the trading insights in new memory.

3) Call tool `compute_memory_parameter` with:
   - existing_params = memory.parameters
   - analyses = latest_merge_result
   Put result into output key: `parameters`.

4) Call tool `manage_memory_list` with:
   - existing_list = memory.memory_list
   - analyses = latest_merge_result
   - ticker = current ticker
   Put result into output key: `memory_list`.

5) Call tool `derive_sentiment` with:
   - sentiment_number = output.parameters.sentiment_number
   Put result into output key: `sentiment`.

When you are done calling tools, respond with <FINAL_ANSWER> followed by STRICT JSON (no markdown).
Return exactly this schema:
{
  "sentiment": "bullish|bearish|neutral",
  "trading_insights": "...",
  "memory_weight_ratio": 0.0,
  "parameters": {"sentiment_number": 0.0, "memory_weight": 0.0},
  "memory_list": [{"memory_title": "...", "file_path": "...", "confidence": 0.0}, ...]
}
"""
        messages: List[Message] = []
        intro = {
            "ticker": ticker,
            "year": year,
            "month": month,
            "mode": "update" if isinstance(existing_memory, dict) else "bootstrap",
            "note": "JSON docs follow as separate messages.",
        }
        messages.append(Message(role=MessageRole.USER, content=f"Task setup:\n{json.dumps(intro, ensure_ascii=False, indent=2)}"))

        # Always keep a single "memory" doc in the message list so the LLM can extract tool args from it.
        current_memory: Dict[str, Any]
        if isinstance(existing_memory, dict):
            current_memory = dict(existing_memory)
        else:
            # Bootstrap memory shape for prompt/tool-arg extraction.
            current_memory = {
                "parameters": {"sentiment_number": 0.0, "memory_weight": 0.0},
                "memory_list": [],
            }

        memory_msg_idx: Optional[int] = None
        latest_msg_idx: Optional[int] = None
        messages.append(Message(role=MessageRole.USER, content=json.dumps({"memory": current_memory}, ensure_ascii=False)))
        memory_msg_idx = len(messages) - 1

        final_request = "Follow the workflow: call tools, then return with <FINAL_ANSWER> followed by the STRICT JSON."

        # Iteratively update memory for each monthly article: old memory + article -> new memory.
        for a in (monthly_articles or []):
            # Remove prior latest_merge_result (if any)
            if latest_msg_idx is not None and latest_msg_idx < len(messages):
                messages.pop(latest_msg_idx)
                latest_msg_idx = None

            messages.append(Message(role=MessageRole.USER, content=json.dumps({"latest_merge_result": a}, ensure_ascii=False)))
            latest_msg_idx = len(messages) - 1

            resp = await tool_client.send_message(message=final_request, messages=messages, system_message=system_message)
            out = extract_json(getattr(resp, "content", "") or "")
            if not isinstance(out, dict):
                continue

            # Pop old memory + latest_merge_result, keep only the new memory for next round
            if latest_msg_idx is not None and latest_msg_idx < len(messages):
                messages.pop(latest_msg_idx)
                latest_msg_idx = None
            if memory_msg_idx is not None and memory_msg_idx < len(messages):
                messages.pop(memory_msg_idx)
                memory_msg_idx = None

            current_memory = out
            messages.append(Message(role=MessageRole.USER, content=json.dumps({"memory": current_memory}, ensure_ascii=False)))
            memory_msg_idx = len(messages) - 1

        # Final pass for the newest analysis
        if latest_msg_idx is not None and latest_msg_idx < len(messages):
            messages.pop(latest_msg_idx)
        messages.append(Message(role=MessageRole.USER, content=json.dumps({"latest_merge_result": latest_merge_result}, ensure_ascii=False)))
        latest_msg_idx = len(messages) - 1

        last_err: Optional[str] = None
        for _ in range(2):
            resp = await tool_client.send_message(message=final_request, messages=messages, system_message=system_message)
            out = extract_json(getattr(resp, "content", "") or "")
            if isinstance(out, dict):
                ti = str(out.get("trading_insights") or "").strip()
                params = out.get("parameters") if isinstance(out.get("parameters"), dict) else None
                mem_list = out.get("memory_list") if isinstance(out.get("memory_list"), list) else None
                sentiment = str(out.get("sentiment") or "").strip().lower()
                if ti and params and mem_list is not None and sentiment in allowed_sentiments:
                    return out
            last_err = "memory_summarizer (tool mode) returned invalid JSON or missing required keys."
        try:
            if self.logger:
                self.logger.warning(last_err or "memory_summarizer tool mode failed")
        except Exception:
            pass
        return None

    @staticmethod
    def extract_memory_metadata(memory_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract metadata from memory files for LLM analysis.

        Args:
            memory_list: List of memory entries with file_path.

        Returns:
            List[Dict]: Metadata for each memory including title, confidence, sentiment_rating, trading_insights, date.
        """
        metadata: List[Dict[str, Any]] = []
        for mem in memory_list:
            if not isinstance(mem, dict):
                continue
            
            fp = mem.get("file_path")
            if not isinstance(fp, str) or not fp.strip():
                continue
            
            # Try to read the file to extract additional metadata
            file_path = Path(fp)
            file_data: Dict[str, Any] = {}
            if file_path.exists() and file_path.is_file():
                try:
                    file_data = json.loads(file_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            
            # Extract metadata
            entry = {
                "memory_title": mem.get("memory_title") or file_data.get("title") or "",
                "file_path": fp,
                "confidence": mem.get("confidence") or file_data.get("confidence") or 0.0,
                "sentiment_rating": file_data.get("sentiment_rating") or "",
                "trading_insights": file_data.get("trading_insights") or "",
                "sentiment": file_data.get("sentiment") or "",
                "date": file_data.get("date") or file_data.get("published_at") or "",
            }
            metadata.append(entry)
        
        return metadata

    async def llm_memory_drop_manager(
        self,
        *,
        ticker: str,
        year: str,
        month: str,
        existing_memory: Dict[str, Any],
        memory_metadata_list: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        LLM-based memory drop manager: decides which memory to drop when capacity is reached.

        Args:
            ticker: Target ticker symbol.
            year: UTC year bucket (YYYY).
            month: UTC month bucket (MM).
            existing_memory: Current memory.json with 30 memories.
            memory_metadata_list: List of metadata extracted from each memory file.
                Each entry: {memory_title, file_path, confidence, sentiment_rating, trading_insights, date}

        Returns:
            Optional[Dict[str, Any]]: Updated memory with 29 entries, or None on failure.
                {sentiment, trading_insights, parameters, memory_list}
        """
        allowed_sentiments = {"bullish", "bearish", "neutral"}
        if not LANGCHAIN_TOOLS_AVAILABLE or StructuredTool is None:
            return None

        compute_params_tool = StructuredTool.from_function(  # type: ignore[union-attr]
            func=MonthlyMemoryWriterNode.compute_memory_parameter,
            name="compute_memory_parameter",
            description="Compute memory parameters. operation='add' adds analysis contributions, operation='subtract' removes them. Returns {sentiment_number, memory_weight}.",
        )
        remove_memories_tool = StructuredTool.from_function(  # type: ignore[union-attr]
            func=MonthlyMemoryWriterNode.remove_memories_from_list,
            name="remove_memories_from_list",
            description="Remove memories from memory_list by file_path. Pass existing_list and file_paths_to_remove (single path or list of paths). Returns updated list.",
        )
        derive_sentiment_struct_tool = StructuredTool.from_function(  # type: ignore[union-attr]
            func=MonthlyMemoryWriterNode.derive_sentiment_tool,
            name="derive_sentiment",
            description="Derive sentiment from sentiment_number: <0 bearish, >0 bullish, ==0 neutral.",
        )

        cfg = self._node_cfg("memory_summarizer")
        tool_client = LLMClient(
            model=str(cfg.get("model") or "").strip(),
            temperature=float(cfg.get("temperature", 0.7)),
            tools=[compute_params_tool, remove_memories_tool, derive_sentiment_struct_tool],
            enable_multi_stage_reasoning=True,
            max_tool_iterations=8,
            agent_name=self.main_agent_name,
            **(cfg.get("kwargs") or {}),
        )

        system_message = """You are a monthly memory manager for stock ticker analysis.

Scenario: The memory_list has reached capacity (30 memories). You must:
1. Identify ONE or MORE (up to five) memory to drop based on lowest impact
2. Generate updated trading_insights for the reduced memory

Dropping Criteria (prioritize in order):
1. News date (older news has declining market relevance - prefer dropping older news)
2. Sentiment rating impact:
   - BL0/BR0: Minimal market impact (drop first)
   - BL1/BR1: Moderate market impact
   - BL2/BR2: Significant market impact (preserve)
3. Confidence level (lower confidence = less reliable, prefer dropping)
4. Trading insights quality (generic insights vs. specific actionable information - prefer dropping generic)

Goal: Maintain the most impactful and relevant memories that provide actionable trading intelligence.

Required workflow (follow exactly):
1) Analyze all 30 memories from the memory_metadata_list provided in the user message.
2) Identify the ONE or MORE (up to five) memory(ies) with lowest impact based on dropping criteria.
3) Call tool `remove_memories_from_list` with:
   - existing_list = memory.memory_list
   - file_paths_to_remove = list of file paths to drop (e.g., ["path1.json", "path2.json"])
   Put result into output key: `memory_list`.

4) For each memory you're dropping, call tool `compute_memory_parameter` with operation="subtract":
   - existing_params = memory.parameters (or result from previous subtract call)
   - analyses = the memory to be dropped (must include sentiment, confidence)
   - operation = "subtract"
   After all subtractions, put final result into output key: `parameters`.

5) Write updated `trading_insights` that reflects the remaining memories.
   Consider the overall market sentiment and key insights from retained memories.

6) Call tool `derive_sentiment` with:
   - sentiment_number = output.parameters.sentiment_number
   Put result into output key: `sentiment`.

When you are done calling tools, respond with <FINAL_ANSWER> followed by STRICT JSON (no markdown).
Return exactly this schema:
{
  "dropped_memory_paths": ["path1.json", "path2.json", ...],
  "reason": "Brief explanation of why these memories were dropped",
  "trading_insights": "Updated insights reflecting remaining memories",
  "parameters": {"sentiment_number": 0.0, "memory_weight": 0.0},
  "memory_list": [...],
  "sentiment": "bullish|bearish|neutral"
}
"""
        messages: List[Message] = []
        intro = {
            "ticker": ticker,
            "year": year,
            "month": month,
            "note": "Memory list is at capacity (30 memories). Need to drop one memory.",
        }
        messages.append(Message(role=MessageRole.USER, content=f"Task setup:\n{json.dumps(intro, ensure_ascii=False, indent=2)}"))

        # Add current memory
        messages.append(Message(role=MessageRole.USER, content=json.dumps({"memory": existing_memory}, ensure_ascii=False)))

        # Add memory metadata for LLM to analyze
        messages.append(Message(
            role=MessageRole.USER,
            content=f"Memory metadata (all 30 memories):\n{json.dumps(memory_metadata_list, ensure_ascii=False, indent=2)}"
        ))

        final_request = "Analyze the memories, identify which one to drop, call the tools, then return with <FINAL_ANSWER> followed by the STRICT JSON."

        last_err: Optional[str] = None
        for _ in range(2):
            resp = await tool_client.send_message(message=final_request, messages=messages, system_message=system_message)
            out = extract_json(getattr(resp, "content", "") or "")
            if isinstance(out, dict):
                dropped_paths = out.get("dropped_memory_paths")
                # Handle both single path (string) and multiple paths (list)
                if isinstance(dropped_paths, str):
                    dropped_paths = [dropped_paths]
                reason = str(out.get("reason") or "").strip()
                ti = str(out.get("trading_insights") or "").strip()
                params = out.get("parameters") if isinstance(out.get("parameters"), dict) else None
                mem_list = out.get("memory_list") if isinstance(out.get("memory_list"), list) else None
                sentiment = str(out.get("sentiment") or "").strip().lower()
                
                if dropped_paths and reason and ti and params and mem_list and sentiment in allowed_sentiments:
                    try:
                        if self.logger:
                            paths_str = ", ".join(dropped_paths) if isinstance(dropped_paths, list) else str(dropped_paths)
                            self.logger.info(f"Memory drop for {ticker}: dropped {paths_str}, reason: {reason}")
                    except Exception:
                        pass
                    return out
            last_err = "memory_drop_manager returned invalid JSON or missing required keys."
        
        try:
            if self.logger:
                self.logger.warning(last_err or "memory_drop_manager failed")
        except Exception:
            pass
        return None

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        routing = state.get("routing", {}) if isinstance(state.get("routing"), dict) else {}
        if not bool(routing.get("neutral_analysis")):
            return state

        current_ticker = str(state.get("current_ticker") or "").upper().strip()
        if not current_ticker:
            return state

        final = state.get("final") if isinstance(state.get("final"), dict) else {}
        latest_merge_result = final.get("result") if isinstance(final.get("result"), dict) else {}
        latest_merge_result['url'] = state['metadata']['url']

        base_news_dir = os.getenv("NEWS_RESULT_DIR", "/data/news") or "/data/news"
        now = datetime.now(timezone.utc)
        year = now.strftime("%Y")
        month = now.strftime("%m")

        written: Dict[str, Any] = (
            dict(state.get("monthly_memory_written") or {})
            if isinstance(state.get("monthly_memory_written"), dict)
            else {}
        )

        t = current_ticker
        dir_path = Path(base_news_dir) / t / year / month
        dir_path.mkdir(parents=True, exist_ok=True)
        mem_path = dir_path / "memory.json"

        existing_memory: Optional[Dict[str, Any]] = None
        if mem_path.exists() and mem_path.is_file():
            try:
                ex = json.loads(mem_path.read_text(encoding="utf-8"))
                existing_memory = ex if isinstance(ex, dict) else None
            except Exception:
                existing_memory = None

        monthly_articles: Optional[List[Dict[str, Any]]] = None
        if existing_memory is None:
            items: List[Dict[str, Any]] = []
            try:
                files = [p for p in dir_path.glob("*.json") if p.name != "memory.json"]
                files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                for fp in files[:50]:
                    try:
                        d = json.loads(fp.read_text(encoding="utf-8"))
                        if not isinstance(d, dict):
                            continue
                        if not isinstance(d.get("file_path"), str) or not str(d.get("file_path") or "").strip():
                            d["file_path"] = str(fp)
                        items.append(d)
                    except Exception:
                        continue
            except Exception:
                items = []
            monthly_articles = items

        # Check if memory_list is at capacity (30 memories) and needs dropping
        if isinstance(existing_memory, dict):
            mem_list = existing_memory.get("memory_list")
            if isinstance(mem_list, list) and len(mem_list) >= 30:
                try:
                    if self.logger:
                        self.logger.info(f"Memory list at capacity ({len(mem_list)} memories) for {t}, initiating drop manager")
                except Exception:
                    pass
                
                # Extract metadata from all memories
                memory_metadata = MonthlyMemoryWriterNode.extract_memory_metadata(mem_list)
                
                # Call drop manager to reduce to 29 memories
                try:
                    updated_memory = await self.llm_memory_drop_manager(
                        ticker=t,
                        year=year,
                        month=month,
                        existing_memory=existing_memory,
                        memory_metadata_list=memory_metadata,
                    )
                    if isinstance(updated_memory, dict):
                        # Use the updated memory (with reduced entries) for the next step
                        existing_memory = updated_memory
                        try:
                            if self.logger:
                                dropped_paths = updated_memory.get("dropped_memory_paths", [])
                                if isinstance(dropped_paths, list):
                                    paths_str = ", ".join(dropped_paths)
                                else:
                                    paths_str = str(dropped_paths)
                                self.logger.info(f"Successfully dropped memory(ies): {paths_str}")
                        except Exception:
                            pass
                    else:
                        try:
                            if self.logger:
                                self.logger.warning(f"Memory drop manager failed for {t}, proceeding with full list")
                        except Exception:
                            pass
                except Exception as e:
                    try:
                        if self.logger:
                            self.logger.error(f"Memory drop manager error for {t}: {e}")
                    except Exception:
                        pass

        try:
            # Prefer tool-calling summarizer (full output). Fall back to local compute + legacy trading_insights LLM.
            summary = await self.llm_monthly_memory_summary(
                ticker=t,
                year=year,
                month=month,
                latest_merge_result=latest_merge_result,
                existing_memory=existing_memory,
                monthly_articles=monthly_articles
            )
            if not isinstance(summary, dict):
                existing_params = existing_memory.get("parameters") if isinstance(existing_memory, dict) else None
                existing_list = existing_memory.get("memory_list") if isinstance(existing_memory, dict) else None
                analyses_for_tools: List[Dict[str, Any]] = []
                if isinstance(existing_memory, dict):
                    if isinstance(latest_merge_result, dict):
                        analyses_for_tools = [latest_merge_result]
                else:
                    analyses_for_tools = list(monthly_articles or [])
                    if isinstance(latest_merge_result, dict):
                        analyses_for_tools.append(latest_merge_result)

                params = MonthlyMemoryWriterNode.compute_memory_parameter(existing_params, analyses_for_tools)
                mem_list = MonthlyMemoryWriterNode.manage_memory_list(existing_list, analyses_for_tools, ticker=t)
                sentiment = MonthlyMemoryWriterNode.derive_sentiment_tool(
                    sentiment_number=float((params or {}).get("sentiment_number") or 0.0)
                )
                trading_insights = await self.llm_trading_insights(
                    ticker=t,
                    year=year,
                    month=month,
                    latest_merge_result=latest_merge_result,
                    existing_memory=existing_memory,
                    monthly_articles=monthly_articles,
                )
                ratio_val = MonthlyMemoryWriterNode.compute_memory_weight_ratio(
                    existing_memory_weight=float((existing_params or {}).get("memory_weight") or 0.0)
                    if isinstance(existing_params, dict)
                    else 0.0,
                    latest_confidence=float((latest_merge_result or {}).get("confidence") or 0.0)
                    if isinstance(latest_merge_result, dict)
                    else 0.0,
                )
                summary = {
                    "sentiment": sentiment,
                    "trading_insights": trading_insights,
                    "memory_weight_ratio": float(ratio_val),
                    "parameters": params,
                    "memory_list": mem_list,
                }
        except Exception as e:
            try:
                self.logger.error(f"write_monthly_memory failed for {t}: {e}")
            except Exception:
                pass
            return {**state, "monthly_memory_written": written}

        tmp_path = dir_path / "memory.json.tmp"
        try:
            tmp_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(mem_path)
            written[t] = summary
        except Exception as e:
            try:
                self.logger.error(f"Failed to write memory.json for {t}: {e}")
            except Exception:
                pass

        return {**state, "monthly_memory_written": written}