"""LLM clients for quality scoring and ticker extraction."""

import json
import re
from typing import Dict, Any, List, Optional

try:
    from moose.framework.llm_core import LLMClient
    LLM_CLIENT_AVAILABLE = True
except Exception:
    LLM_CLIENT_AVAILABLE = False
    LLMClient = None  # type: ignore


def _extract_json(text: str) -> Optional[dict]:
    s = (text or "").strip()
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception:
        return None


def _json_decode_error(text: str) -> str:
    """Best-effort JSON error message for LLM outputs."""
    s = (text or "").strip()
    if not s:
        return "Empty output."
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s).strip()
        s = re.sub(r"\s*```$", "", s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return "No JSON object boundaries found (missing '{' or '}')."
    try:
        json.loads(s[start : end + 1])
        return "Unknown JSON error (parsed successfully in diagnostic)."
    except Exception as e:
        return f"{type(e).__name__}: {e}"


async def _repair_json_once(llm_client: Any, *, bad_output: str, error_hint: str) -> Any:
    """One-shot JSON repair retry."""
    repair_system_message = (
        "You are a JSON repair tool.\n"
        "CRITICAL OUTPUT REQUIREMENT:\n"
        "- Return ONLY a single valid JSON object (no markdown fences, no leading/trailing quotes, no commentary).\n"
        "- JSON strings MUST be valid: do not include raw double quotes (\") inside string values.\n"
        "  If you need to quote text, use \\\" ... \\\" or use Chinese quotes 「...」.\n"
        "- Use \\n for newlines inside string values.\n"
    )
    repair_user_message = (
        "Your previous output was invalid JSON and could not be parsed.\n"
        f"Parser error: {error_hint}\n\n"
        "Fix the INVALID OUTPUT below so it becomes strict valid JSON.\n"
        "Keep the same keys/structure and preserve content as much as possible.\n\n"
        "INVALID OUTPUT:\n"
        + str(bad_output or "")
        + "\n\nNow output the corrected JSON object ONLY."
    )
    return await llm_client.send_message(message=repair_user_message, system_message=repair_system_message)


class NYTimesQualityLLMClient:
    """LLM client for quality scoring NYTimes articles."""
    
    def __init__(self, *, llm_config: Dict[str, Any], logger=None):
        """
        Initialize quality scoring LLM client.
        
        Args:
            llm_config: LLM configuration dict with 'model', 'temperature', etc.
            logger: Logger instance
        """
        if not LLM_CLIENT_AVAILABLE:
            raise ImportError(
                "LLMClient is unavailable (moose.framework.llm_core). "
                "Ensure LLM dependencies are installed."
            )
        
        self.logger = logger
        self.llm_config = llm_config or {}
        
        model = str(self.llm_config.get("model") or "").strip()
        if not model:
            raise ValueError("Missing required config: model must be set in llm_config")
        
        temperature = float(self.llm_config.get("temperature", 0.3))
        kwargs = self.llm_config.get("kwargs", {}) if isinstance(self.llm_config.get("kwargs"), dict) else {}
        
        self.client = LLMClient(
            model=model,
            temperature=temperature,
            enable_multi_stage_reasoning=False,
            tools=[],
            **(kwargs or {}),
        )
    
    async def score_article_quality(
        self,
        url: str,
        title: str,
        abstract: str,
        content: Optional[str] = None,
        section: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Score article quality (1-10 scale).
        
        Args:
            url: Article URL
            title: Article title
            abstract: Article abstract/snippet
            content: Full article content (optional, for better scoring)
            section: Article section (optional, for context)
            
        Returns:
            Dict with 'quality_score' (int 1-10) and 'rationale' (str)
        """
        system_message = """You are a quality scoring engine for news articles.

Goal:
- Evaluate the quality of a news article and assign a score from 1 to 10.
- Provide a rationale explaining the score in 2-3 sentences.

Strict output format:
- Return STRICT JSON ONLY. No markdown, no code fences, no commentary.
- Return exactly this object shape (no extra keys):
  {"quality_score": <int 1-10>, "rationale": "..."}

JSON rules:
- Your output MUST be valid JSON parseable by json.loads().
- Do not include literal newlines inside JSON strings. Use \\n to represent line breaks.

Quality score (1-10):
- <1-3> (low quality): 
  - The article is not complete, or missing important information;
  - The article is badly written, confusing, or self-contradictory;
  - The article has obvious flaws, errors, or inconsistencies;
  - A pure stock prompting article with no interesting insights;
- <4-6> (standard quality): 
  - A standard news article with no obvious flaws, and no interesting insights either;
  - A stock prompting article that has somewhat interesting insights;
  - A standard technical analysis article;
- <7-10> (high quality): 
  - An insightful article with clear knowledge;
  - An insightful technical analysis article with no obvious flaws;
  - An article that discloses insider information;
  - An article about a major event or news;
  - Breaking news that is concise but important;

Important notes:
- Article length cannot be a solo factor to determine quality. Breaking news can be short and concise.
- NYTimes is generally a trustworthy source, but articles can still vary in quality and insightfulness.
- Focus on the content quality, insightfulness, and completeness of information."""

        content_text = content if content else abstract
        section_text = f"\nSection: {section}" if section else ""
        
        user_message = f"""URL: {url}
Title: {title}
Abstract: {abstract}{section_text}

{'Full Content:' if content else ''}
{content_text[:5000] if content_text else ''}

Evaluate the quality of this article and return the quality score JSON."""

        try:
            resp = await self.client.send_message(message=user_message, system_message=system_message)
            content_resp = getattr(resp, "content", "") or ""
            if not isinstance(content_resp, str):
                content_resp = str(content_resp)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"LLM quality scoring failed for {url}: {e}")
            return {"quality_score": 0, "rationale": f"Scoring failed: {str(e)}"}
        
        data = _extract_json(content_resp)
        if not isinstance(data, dict):
            # One-shot JSON repair retry
            try:
                repaired = await _repair_json_once(
                    self.client,
                    bad_output=str(content_resp),
                    error_hint=_json_decode_error(content_resp)
                )
                repaired_content = getattr(repaired, "content", "") or ""
                if not isinstance(repaired_content, str):
                    repaired_content = str(repaired_content)
                data = _extract_json(repaired_content)
            except Exception:
                data = None
        
        if not isinstance(data, dict):
            if self.logger:
                self.logger.warning(f"LLM returned non-JSON for quality scoring {url}")
            return {"quality_score": 0, "rationale": "Failed to parse LLM response"}
        
        quality_score = int(data.get("quality_score", 0))
        rationale = str(data.get("rationale", "")).strip()
        
        # Validate score range
        if quality_score < 1 or quality_score > 10:
            quality_score = 0
        
        return {
            "quality_score": quality_score,
            "rationale": rationale,
        }
    
    async def extract_tickers(
        self,
        title: str,
        abstract: str,
        content: Optional[str] = None,
    ) -> List[str]:
        """
        Extract stock tickers from article content.
        
        Args:
            title: Article title
            abstract: Article abstract
            content: Full article content (optional)
            
        Returns:
            List of ticker symbols (e.g., ["AAPL", "MSFT"])
        """
        system_message = """You are a ticker extraction tool for financial news articles.

Goal:
- Extract stock ticker symbols mentioned in the article.
- Return only valid ticker symbols (typically 1-5 characters, uppercase letters and numbers).
- Focus on tickers that are actually discussed in the article, not just mentioned in passing.

Strict output format:
- Return STRICT JSON ONLY. No markdown, no code fences, no commentary.
- Return exactly this object shape:
  {"tickers": ["AAPL", "MSFT", ...]}

JSON rules:
- Your output MUST be valid JSON parseable by json.loads().
- tickers must be a list of strings (ticker symbols).
- Return empty list [] if no tickers found."""

        content_text = content if content else abstract
        
        user_message = f"""Title: {title}
Abstract: {abstract}

{'Full Content:' if content else ''}
{content_text[:5000] if content_text else ''}

Extract all stock ticker symbols mentioned in this article. Return as JSON with tickers array."""

        try:
            resp = await self.client.send_message(message=user_message, system_message=system_message)
            content_resp = getattr(resp, "content", "") or ""
            if not isinstance(content_resp, str):
                content_resp = str(content_resp)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"LLM ticker extraction failed: {e}")
            return []
        
        data = _extract_json(content_resp)
        if not isinstance(data, dict):
            # One-shot JSON repair retry
            try:
                repaired = await _repair_json_once(
                    self.client,
                    bad_output=str(content_resp),
                    error_hint=_json_decode_error(content_resp)
                )
                repaired_content = getattr(repaired, "content", "") or ""
                if not isinstance(repaired_content, str):
                    repaired_content = str(repaired_content)
                data = _extract_json(repaired_content)
            except Exception:
                data = None
        
        if not isinstance(data, dict):
            if self.logger:
                self.logger.warning("LLM returned non-JSON for ticker extraction")
            return []
        
        tickers = data.get("tickers", [])
        if not isinstance(tickers, list):
            return []
        
        # Validate and normalize tickers
        valid_tickers = []
        for ticker in tickers:
            if isinstance(ticker, str):
                ticker_upper = ticker.strip().upper()
                # Basic validation: tickers are typically 1-5 chars, alphanumeric
                if ticker_upper and len(ticker_upper) <= 5 and ticker_upper.isalnum():
                    valid_tickers.append(ticker_upper)
        
        return valid_tickers

