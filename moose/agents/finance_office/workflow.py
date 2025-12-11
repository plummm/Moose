"""LangGraph workflow for financial report analyzer with asynchronous processing."""

import asyncio
from typing import TypedDict, List, Dict, Any, Optional
from pathlib import Path

try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    END = None


class FinancialAnalyzerState(TypedDict):
    """State structure for the financial analyzer workflow."""
    # Queue item being processed
    current_item: Optional[Dict[str, Any]]  # {file_path, url, metadata}
    
    # Analysis results
    analyses: List[Dict[str, Any]]
    analyses_completed: int
    analyses_failed: int
    
    # Status
    status: str  # "processing", "waiting", "completed", "error"
    error: Optional[str]
    
    # Queue and analyzer instances (passed for node access)
    queue_manager: Any
    analyzer: Any
    logger: Any


def create_workflow(
    queue_manager,
    analyzer,
    logger,
    max_concurrent_analyses: int = 20,
    news_data_dir: Optional[Path] = None
) -> Any:
    """
    Create the LangGraph workflow for financial report analysis.
    
    Args:
        queue_manager: FilePathQueue instance
        analyzer: FinanceResearcher instance
        logger: Logger instance
        max_concurrent_analyses: Maximum number of concurrent LLM analysis calls (default: 20)
        news_data_dir: Base directory for saving analysis results (default: None)
        
    Returns:
        Compiled LangGraph app
    """
    if not LANGGRAPH_AVAILABLE:
        raise ImportError(
            "LangGraph is required. Install with: pip install langgraph"
        )
    
    # Create semaphore to limit concurrent LLM calls
    analysis_semaphore = asyncio.Semaphore(max_concurrent_analyses)
    
    async def analyze_node(state: FinancialAnalyzerState) -> FinancialAnalyzerState:
        """
        Node that processes ONE item from queue and analyzes it.
        
        After processing one item, routes back to check queue for more items,
        enabling continuous processing.
        """
        try:
            queue_manager = state["queue_manager"]
            analyzer = state["analyzer"]
            logger = state["logger"]
            
            logger.info("Analyze node started")
            # Get next item from queue
            current_item = state.get("current_item")
            
            if current_item is None:
                current_item = queue_manager.dequeue()
                if current_item is None:
                    # No items in queue, check if we should wait or end
                    if queue_manager.is_empty():
                        logger.debug("Queue is empty, waiting for more items")
                        return {
                            **state,
                            "status": "waiting"
                        }
            
            if current_item is None:
                return {
                    **state,
                    "status": "waiting"
                }
            
            # Process the item
            file_path = Path(current_item["file_path"])
            url = current_item.get("url", "")
            metadata = current_item.get("metadata", {})
            
            analyses = state.get("analyses", [])
            analyses_completed = state.get("analyses_completed", 0)
            analyses_failed = state.get("analyses_failed", 0)
            
            logger.debug(f"Analyzing article: {file_path}")
            
            # Analyze with semaphore limit
            async with analysis_semaphore:
                try:
                    analysis = await analyzer.analyze_article(
                        url=url,
                        file_path=file_path
                    )
                    
                    if "error" in analysis:
                        analyses_failed += 1
                        logger.warning(f"Failed to analyze {file_path}: {analysis.get('error')}")
                        queue_manager.mark_failed()
                    else:
                        analyses.append(analysis)
                        analyses_completed += 1
                        logger.debug(f"Successfully analyzed: {file_path}")
                        queue_manager.mark_processed()
                        
                        if news_data_dir:
                            try:
                                analyzer.save_analysis_result(analysis, news_data_dir)
                            except Exception as save_error:
                                # Log but don't fail the analysis
                                logger.warning(f"Failed to save analysis result: {save_error}")
                        
                except Exception as e:
                    logger.error(f"Error analyzing {file_path}: {e}")
                    analyses_failed += 1
                    queue_manager.mark_failed()
            
            # Clear current item and check for more
            return {
                **state,
                "current_item": None,
                "analyses": analyses,
                "analyses_completed": analyses_completed,
                "analyses_failed": analyses_failed,
                "status": "processing"
            }
            
        except Exception as e:
            logger.error(f"Error in analyze node: {e}")
            return {
                **state,
                "status": "error",
                "error": str(e)
            }
    
    def route_after_analyze(state: FinancialAnalyzerState) -> str:
        """
        Conditional edge after analyze: route back to analyze if queue has items,
        otherwise wait or end.
        """
        status = state.get("status", "processing")
        queue_manager = state.get("queue_manager")
        
        if status == "error":
            return "end"
        
        if status == "completed":
            return "end"
        
        # Check if queue has items
        if queue_manager and not queue_manager.is_empty():
            return "analyze"
        
        # Queue is empty, wait for more items
        return "analyze"  # Keep checking queue
    
    # Create workflow
    workflow = StateGraph(FinancialAnalyzerState)
    
    # Add nodes
    workflow.add_node("analyze", analyze_node)
    
    # Set entry point
    workflow.set_entry_point("analyze")
    
    # Add conditional edge for continuous processing
    workflow.add_conditional_edges(
        "analyze",
        route_after_analyze,
        {
            "analyze": "analyze",  # Loop back to continue processing
            "end": END
        }
    )
    
    # Compile and return
    app = workflow.compile()
    return app


# Export for use in agent
__all__ = ['FinancialAnalyzerState', 'create_workflow', 'LANGGRAPH_AVAILABLE']

