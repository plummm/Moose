"""Earning Report Analyzer Agent.

This agent analyzes earning reports using LLM to extract key insights,
financial metrics, and trends.
"""

import os
from pathlib import Path
from typing import Dict, Any, Union
from agents import BaseAgent
from framework.llm_core import LLMClient, extract_pdf_text


class EarningReportAnalyzer(BaseAgent):
    """
    Agent for analyzing earning reports.
    
    Uses LLM to extract and analyze:
    - Key financial metrics (revenue, profit, EPS, etc.)
    - Trends and comparisons
    - Management insights
    - Risk factors
    """
    
    name = "earning_report_analyzer"
    description = "Analyzes earning reports using LLM to extract key insights, financial metrics, and trends"
    
    def __init__(self, config_path=None, debug=False):
        """Initialize the earning report analyzer."""
        super().__init__(config_path, debug=debug)
        
        # Initialize LLM client
        # Model can be configured via environment variable or defaults to gpt-4
        model = os.getenv("MOOSE_LLM_MODEL", "gpt-4")
        self.llm_client = LLMClient(model=model)
        
        # Analysis prompt template
        self.analysis_prompt = """You are a financial analyst expert. Analyze the following earning report and provide a comprehensive analysis.

Please extract and analyze:
1. Key Financial Metrics:
   - Revenue (total and by segment if available)
   - Net income/profit
   - Earnings per share (EPS)
   - Growth rates (YoY, QoQ)
   
2. Performance Trends:
   - Revenue trends
   - Profitability trends
   - Market position changes
   
3. Management Insights:
   - Key highlights from management
   - Strategic initiatives
   - Guidance or outlook
   
4. Risk Factors:
   - Potential concerns
   - Market challenges
   - Competitive pressures

Format your response as a structured JSON object with the following keys:
- summary: Brief executive summary
- financial_metrics: Object with key metrics
- trends: Array of trend observations
- management_insights: Array of key insights
- risk_factors: Array of identified risks
- overall_assessment: Overall assessment and rating

Earning Report:
{report_content}
"""
    
    def process(self, input_data: Any) -> Dict[str, Any]:
        """
        Process earning report input and return analysis.
        
        Args:
            input_data: Can be:
                - String: Direct report text
                - Dict with "report" key: Report text
                - Dict with "file_path" key: Path to report file (supports .txt, .pdf)
                - Dict with "report_path" key: Path to report file (alternative)
                - Dict with "pdf_path" key: Path to PDF file (alternative)
        
        Returns:
            Dictionary containing analysis results
        """
        # Extract file path or content from input
        file_path, report_content = self._extract_input(input_data)
        
        if not file_path and not report_content:
            return {
                "error": "No report content provided. "
                        "Provide 'report' (text), 'file_path', 'report_path', or 'pdf_path' in input."
            }
        
        # Get LLM analysis
        try:
            # Extract content from file if needed
            if file_path and not report_content:
                if file_path.suffix.lower() == '.pdf':
                    # Extract text from PDF
                    self.logger.info(f"Extracting text from PDF: {file_path}")
                    try:
                        report_content = extract_pdf_text(file_path)
                        self.logger.info(f"Extracted {len(report_content)} characters from PDF")
                    except Exception as e:
                        self.logger.error(f"Failed to extract text from PDF: {e}")
                        return {
                            "status": "error",
                            "error": f"Failed to extract text from PDF: {e}"
                        }
                else:
                    # Read text file
                    with open(file_path, 'r', encoding='utf-8') as f:
                        report_content = f.read()
            
            if not report_content:
                return {
                    "status": "error",
                    "error": "No report content available to analyze"
                }
            
            # Use text-based analysis
            prompt = self.analysis_prompt.format(report_content=report_content)
            self.logger.info("Sending report text to LLM for analysis...")
            response = self.llm_client.send_message(
                message=prompt,
                system_message="You are an expert financial analyst. Provide accurate, detailed analysis of earning reports."
            )
            
            analysis_text = response.content
            
            # Try to parse as JSON if possible, otherwise return as text
            import json
            try:
                # Try to extract JSON from the response
                # LLM might wrap JSON in markdown code blocks
                if "```json" in analysis_text:
                    json_start = analysis_text.find("```json") + 7
                    json_end = analysis_text.find("```", json_start)
                    analysis_text = analysis_text[json_start:json_end].strip()
                elif "```" in analysis_text:
                    json_start = analysis_text.find("```") + 3
                    json_end = analysis_text.find("```", json_start)
                    analysis_text = analysis_text[json_start:json_end].strip()
                
                analysis = json.loads(analysis_text)
            except (json.JSONDecodeError, ValueError):
                # If not valid JSON, return as structured text
                analysis = {
                    "raw_analysis": analysis_text,
                    "format": "text"
                }
            
            # Add metadata with token cost information
            # This will be extracted by BaseAgent._extract_token_cost_from_result
            result = {
                "status": "success",
                "analysis": analysis,
                "model": response.model,
                "usage": {
                    "input_tokens": response.usage.get("input_tokens", 0) if response.usage else 0,
                    "output_tokens": response.usage.get("output_tokens", 0) if response.usage else 0,
                    "total_tokens": response.usage.get("total_tokens", 0) if response.usage else 0
                },
                "cost": response.cost if hasattr(response, 'cost') and response.cost else 0.0
            }
            
            self.logger.info("Analysis completed successfully")
            return result
            
        except Exception as e:
            self.logger.error(f"Error analyzing report: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _extract_input(self, input_data: Any):
        """
        Extract file path or content from input.
        
        Returns:
            Tuple of (file_path, content_string)
            - file_path: Path object if file is found, None otherwise
            - content_string: Text content if found, None otherwise
        """
        # If input is a string, treat as content
        if isinstance(input_data, str):
            return None, input_data
        
        # If input is a dict, check for different keys
        if isinstance(input_data, dict):
            # Check for direct report text
            if "report" in input_data:
                return None, str(input_data["report"])
            
            # Check for file path (supports multiple key names)
            file_path = (
                input_data.get("file_path") or 
                input_data.get("report_path") or 
                input_data.get("pdf_path")
            )
            
            if file_path:
                file_path = Path(file_path)
                
                # If relative path, check in project directory or current directory
                if not file_path.is_absolute():
                    # Check /project directory (mounted project dir)
                    project_path = Path("/project") / file_path
                    if project_path.exists():
                        file_path = project_path
                    # Check current directory
                    elif (Path.cwd() / file_path).exists():
                        file_path = Path.cwd() / file_path
                
                if file_path.exists():
                    return file_path, None
                else:
                    self.logger.warning(f"Report file not found: {file_path}")
                    return None, None
        
        return None, None
    
    def _extract_report_content(self, input_data: Any) -> str:
        """
        Extract report content from various input formats.
        
        Supports:
        - Plain text strings
        - Text files (.txt)
        - PDF files (.pdf)
        
        Args:
            input_data: Input in various formats
        
        Returns:
            Report content as string
        """
        # If input is a string, use it directly
        if isinstance(input_data, str):
            return input_data
        
        # If input is a dict, check for different keys
        if isinstance(input_data, dict):
            # Check for direct report text
            if "report" in input_data:
                return str(input_data["report"])
            
            # Check for file path (supports multiple key names)
            file_path = (
                input_data.get("file_path") or 
                input_data.get("report_path") or 
                input_data.get("pdf_path")
            )
            
            if file_path:
                file_path = Path(file_path)
                
                # If relative path, check in project directory or current directory
                if not file_path.is_absolute():
                    # Check /project directory (mounted project dir)
                    project_path = Path("/project") / file_path
                    if project_path.exists():
                        file_path = project_path
                    # Check current directory
                    elif (Path.cwd() / file_path).exists():
                        file_path = Path.cwd() / file_path
                
                if file_path.exists():
                    try:
                        # Handle PDF files
                        if file_path.suffix.lower() == '.pdf':
                            return self._extract_text_from_pdf(file_path)
                        else:
                            # Handle text files
                            with open(file_path, 'r', encoding='utf-8') as f:
                                return f.read()
                    except Exception as e:
                        self.logger.error(f"Error reading file {file_path}: {e}")
                        return ""
                else:
                    self.logger.warning(f"Report file not found: {file_path}")
                    return ""
        
        return ""


if __name__ == "__main__":
    """Entry point for the agent."""
    import sys
    
    # Determine communication mode from environment or default to HTTP
    mode = os.getenv("MOOSE_AGENT_MODE", "http")
    
    # Get port from environment or config
    port = int(os.getenv("MOOSE_AGENT_PORT", "8000"))
    
    # Get debug flag from environment
    debug = os.getenv("MOOSE_AGENT_DEBUG", "false").lower() in ("true", "1", "yes", "on")
    
    # Initialize and run agent
    agent = EarningReportAnalyzer(debug=debug)
    
    if mode == "http":
        agent.run(mode="http", port=port)
    elif mode == "stdin":
        agent.run(mode="stdin")
    elif mode == "file":
        watch_dir = os.getenv("MOOSE_AGENT_WATCH_DIR", "/project/agent_io")
        agent.run(mode="file", watch_dir=watch_dir)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)

