"""Financial News Analyzer - LLM-based financial news analysis."""

from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

try:
    from moose.framework.llm_core import LLMClient
    LLM_AVAILABLE = True
except ImportError:
    try:
        from framework.llm_core import LLMClient
        LLM_AVAILABLE = True
    except ImportError:
        LLM_AVAILABLE = False
        LLMClient = None


class FinancialNewsAnalyzer:
    """
    Analyzes financial news articles using LLM.
    
    Provides structured financial analysis with:
    - High-level idea
    - Covered companies
    - Sentiment analysis
    - Impact type assessment
    - Confidence rating
    - Trading insights
    """
    
    def __init__(
        self,
        model: str = "gpt-5",
        temperature: float = 0.7,
        logger=None,
        **llm_kwargs
    ):
        """
        Initialize the financial news analyzer.
        
        Args:
            model: LLM model name (e.g., "gpt-4", "claude-3-opus-20240229")
            temperature: Sampling temperature for LLM
            logger: Logger instance
            **llm_kwargs: Additional arguments for LLMClient
        """
        if not LLM_AVAILABLE:
            raise ImportError(
                "LLM support not available. Install with: "
                "pip install langchain langchain-openai langchain-anthropic langchain-google-genai"
            )
        
        self.model = model
        self.temperature = temperature
        self.logger = logger
        self.llm_client = LLMClient(
            model=model,
            temperature=temperature,
            **llm_kwargs
        )
        
        if self.logger:
            self.logger.info(f"Initialized FinancialNewsAnalyzer with model: {model}")
    
    async def analyze_article(
        self,
        url: str,
        file_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Analyze a financial news article using LLM.
        
        Args:
            url: Article URL
            file_path: Path to saved article file
            
        Returns:
            Dictionary with financial analysis information:
            {
                "url": str,
                "file_path": str,
                "title": str,
                "high_level_idea": str,
                "companies": List[str],
                "sentiment": str ("bullish", "bearish", "neutral"),
                "sentiment_rating": str (BL0-BL2, BR0-BR2, or null),
                "impact_type": str ("foundation_changing", "strategic_shift", "short_term_catalyst", "neutral"),
                "confidence": int (1-10),
                "trading_insights": str,
                "analyzed_at": str (ISO format),
                "model": str
            }
        """
        # Load content if not provided
        if file_path is None:
            raise ValueError("file_path must be provided")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to read article from {file_path}: {e}")
            return {
                "url": url,
                "file_path": str(file_path) if file_path else None,
                "error": f"Failed to read article: {e}",
                "analyzed_at": datetime.now().isoformat()
            }
        
        if not content:
            return {
                "url": url,
                "file_path": str(file_path) if file_path else None,
                "error": "Empty content",
                "analyzed_at": datetime.now().isoformat()
            }
        
        # Create prompt for financial news analysis
        system_message = """You are a financial news analyst specializing in market-impact evaluation. 
Your goal is to analyze financial news articles and provide structured insights for trading decisions.

Follow these rules:

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

1. high_level_idea: A concise 2-3 sentence summary of the core news
2. companies: Array of companies or tickers mentioned
3. sentiment: "bullish", "bearish", or "neutral"
4. sentiment_rating: One of {BL0, BL1, BL2, BR0, BR1, BR2} or null if neutral
5. impact_type: One of the categories above
6. confidence: Integer 1-10
7. trading_insights: Brief actionable notes (e.g., expected market reaction, regulatory risk, upside drivers)

Format the output strictly as JSON:
{
  "title": "",
  "high_level_idea": "",
  "companies": [],
  "sentiment": "",
  "sentiment_rating": "",
  "impact_type": "",
  "confidence": 1,
  "trading_insights": ""
}"""
        
        user_message = f"""Analyze the following financial news article for trading decision-making:

URL: {url}

Content:
{content}

Provide a comprehensive financial analysis in JSON format"""
        
        try:
            if self.logger:
                self.logger.debug(f"Analyzing article: {url}")
            
            response = await self.llm_client.send_message(
                message=user_message,
                system_message=system_message
            )
            
            # Parse response (try to extract JSON if wrapped in markdown)
            analysis_text = response.content.strip()
            
            # Try to extract JSON from markdown code blocks
            import json
            import re
            
            # Look for JSON in code blocks
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', analysis_text, re.DOTALL)
            if json_match:
                analysis_text = json_match.group(1)
            else:
                # Try to find JSON object directly
                json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
                if json_match:
                    analysis_text = json_match.group(0)
            
            try:
                analysis_data = json.loads(analysis_text)
            except json.JSONDecodeError:
                # If JSON parsing fails, create a simple analysis
                if self.logger:
                    self.logger.warning(f"Failed to parse JSON from LLM response, using raw text")
                analysis_data = {
                    "title": "Article Analysis",
                    "high_level_idea": analysis_text[:500],  # First 500 chars
                    "companies": [],
                    "sentiment": "neutral",
                    "sentiment_rating": None,
                    "impact_type": "neutral",
                    "confidence": 5,
                    "trading_insights": ""
                }
            
            result = {
                "url": url,
                "file_path": str(file_path) if file_path else None,
                "title": analysis_data.get("title", "Untitled"),
                "high_level_idea": analysis_data.get("high_level_idea", ""),
                "companies": analysis_data.get("companies", []),
                "sentiment": analysis_data.get("sentiment", "neutral"),
                "sentiment_rating": analysis_data.get("sentiment_rating"),
                "impact_type": analysis_data.get("impact_type", "neutral"),
                "confidence": analysis_data.get("confidence", 5),
                "trading_insights": analysis_data.get("trading_insights", ""),
                "analyzed_at": datetime.now().isoformat(),
                "model": self.model,
                "token_usage": response.usage if hasattr(response, 'usage') else None,
                "cost": response.cost if hasattr(response, 'cost') else None
            }
            
            if self.logger:
                self.logger.info(f"Successfully analyzed article: {url}")
            
            return result
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error analyzing article {url}: {e}")
            return {
                "url": url,
                "file_path": str(file_path) if file_path else None,
                "error": str(e),
                "analyzed_at": datetime.now().isoformat()
            }

