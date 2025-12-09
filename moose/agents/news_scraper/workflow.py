"""LangGraph workflow for news scraper with asynchronous summarization."""

import hashlib
from typing import TypedDict, List, Dict, Any, Optional
from queue import Queue
from pathlib import Path

try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    END = None


class NewsScraperState(TypedDict):
    """State structure for the news scraper workflow."""
    # Input parameters
    start_url: Optional[str]
    max_depth: int
    
    # Scraping progress tracking
    article_urls: List[str]
    current_url_index: int  # Track which URL we're currently processing
    articles_scraped: int
    articles_skipped: int
    articles_failed: int
    saved_files: List[str]
    
    # Queue for articles to be summarized
    article_queue: Queue
    
    # Summarization results
    summaries: List[Dict[str, Any]]
    summaries_completed: int
    summaries_failed: int
    
    # Status
    status: str  # "scraping", "summarizing", "completed", "error"
    error: Optional[str]


def create_workflow(
    scraper_core,
    summarizer,
    logger
) -> Any:
    """
    Create the LangGraph workflow for news scraping and summarization.
    
    Args:
        scraper_core: NewsScraperCore instance
        summarizer: NewsSummarizer instance
        logger: Logger instance
        
    Returns:
        Compiled LangGraph app
    """
    if not LANGGRAPH_AVAILABLE:
        raise ImportError(
            "LangGraph is required. Install with: pip install langgraph"
        )
    
    def scrape_node(state: NewsScraperState) -> NewsScraperState:
        """
        Node that scrapes ONE article at a time and enqueues it for summarization.
        
        This enables asynchronous processing - after each article is scraped,
        we can route to summarize node if queue has items.
        """
        try:
            # Initialize article_urls if not already done
            article_urls = state.get("article_urls", [])
            if not article_urls:
                # First time - scrape feed to get article URLs
                start_url = state.get("start_url") or scraper_core.scraper_config.get("start_url")
                if not start_url:
                    return {
                        **state,
                        "status": "error",
                        "error": "No start_url provided or configured"
                    }
                
                logger.info(f"Scraping feed: {start_url}")
                article_urls = scraper_core.scrape_feed(start_url)
                
                if not article_urls:
                    logger.info("No articles found")
                    return {
                        **state,
                        "status": "completed",
                        "article_urls": [],
                        "current_url_index": 0,
                        "articles_scraped": 0,
                        "articles_skipped": 0,
                        "articles_failed": 0,
                        "saved_files": []
                    }
                
                # Initialize state with article URLs
                state["article_urls"] = article_urls
                state["current_url_index"] = 0
                state["articles_scraped"] = 0
                state["articles_skipped"] = 0
                state["articles_failed"] = 0
                state["saved_files"] = []
                state["article_queue"] = Queue()
            
            # Get current state
            current_index = state.get("current_url_index", 0)
            articles_scraped = state.get("articles_scraped", 0)
            articles_skipped = state.get("articles_skipped", 0)
            articles_failed = state.get("articles_failed", 0)
            saved_files = state.get("saved_files", [])
            article_queue = state.get("article_queue", Queue())
            
            # Check if we've processed all articles
            if current_index >= len(article_urls):
                # All articles processed, save index and check if we should summarize
                scraper_core.save_index()
                logger.info(
                    f"Scraping complete: {articles_scraped} scraped, "
                    f"{articles_skipped} skipped, {articles_failed} failed"
                )
                return {
                    **state,
                    "status": "summarizing" if not article_queue.empty() else "completed"
                }
            
            # Process one article
            url = article_urls[current_index]
            logger.debug(f"Processing article {current_index + 1}/{len(article_urls)}: {url}")
            
            # Check if already scraped (deduplication)
            if scraper_core.is_url_scraped(url):
                logger.debug(f"Skipping already scraped: {url}")
                articles_skipped += 1
                current_index += 1
                return {
                    **state,
                    "current_url_index": current_index,
                    "articles_skipped": articles_skipped
                }
            
            # Extract text
            text_content = scraper_core.extract_text_from_url(url)
            
            if text_content:
                # Save article
                saved_path = scraper_core.save_article(url, text_content)
                if saved_path:
                    articles_scraped += 1
                    saved_files.append(str(saved_path))
                    
                    url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
                    metadata = scraper_core.index.get(url_hash, {})
                    # Enqueue for summarization
                    article_queue.put({
                        "url": url,
                        "url_hash": url_hash,
                        "file_path": saved_path,
                        "metadata": metadata,
                    })
                    logger.debug(f"Enqueued article for summarization: {url}")
                else:
                    articles_failed += 1
            else:
                articles_failed += 1
            
            current_index += 1
            
            # Update state and route to summarize if queue has items, otherwise continue scraping
            return {
                **state,
                "current_url_index": current_index,
                "articles_scraped": articles_scraped,
                "articles_skipped": articles_skipped,
                "articles_failed": articles_failed,
                "saved_files": saved_files,
                "article_queue": article_queue,
                "status": "scraping"
            }
            
        except Exception as e:
            logger.error(f"Error in scrape node: {e}", exc_info=True)
            return {
                **state,
                "status": "error",
                "error": str(e)
            }
    
    def summarize_node(state: NewsScraperState) -> NewsScraperState:
        """
        Node that processes ONE item from queue and summarizes it.
        
        After processing one item, routes back to scrape if more articles remain,
        enabling true asynchronous processing.
        """
        try:
            article_queue = state.get("article_queue")
            if not article_queue or article_queue.empty():
                logger.debug("No articles in queue to summarize")
                # Check if scraping is complete
                article_urls = state.get("article_urls", [])
                current_index = state.get("current_url_index", 0)
                if current_index >= len(article_urls):
                    # Scraping complete and queue empty - we're done
                    return {
                        **state,
                        "status": "completed"
                    }
                else:
                    # More articles to scrape, go back to scrape node
                    return {
                        **state,
                        "status": "scraping"
                    }
            
            summaries = state.get("summaries", [])
            summaries_completed = state.get("summaries_completed", 0)
            summaries_failed = state.get("summaries_failed", 0)
            
            # Process ONE item from queue
            try:
                article_item = article_queue.get_nowait()
                url = article_item["url"]
                url_hash = article_item["url_hash"]
                file_path = Path(article_item["file_path"])
                metadata = article_item.get("metadata")
                
                logger.debug(f"Summarizing article: {url}")
                
                summary = summarizer.summarize_article(
                    url=url,
                    file_path=file_path
                )
                
                if "error" in summary:
                    summaries_failed += 1
                    logger.warning(f"Failed to summarize {url}: {summary.get('error')}")
                else:
                    summaries.append(summary)
                    summaries_completed += 1
                    logger.debug(f"Successfully summarized: {url}")
                
            except Exception as e:
                logger.error(f"Error processing queue item: {e}", exc_info=True)
                summaries_failed += 1
            
            # Check if we should continue
            article_urls = state.get("article_urls", [])
            current_index = state.get("current_url_index", 0)
            scraping_complete = current_index >= len(article_urls)
            queue_empty = article_queue.empty()
            
            if scraping_complete and queue_empty:
                # All done
                logger.info(
                    f"Summarization complete: {summaries_completed} completed, "
                    f"{summaries_failed} failed"
                )
                return {
                    **state,
                    "status": "completed",
                    "summaries": summaries,
                    "summaries_completed": summaries_completed,
                    "summaries_failed": summaries_failed
                }
            else:
                # More work to do - route back to scrape if articles remain, or continue summarizing
                if not scraping_complete:
                    # More articles to scrape
                    return {
                        **state,
                        "status": "scraping",
                        "summaries": summaries,
                        "summaries_completed": summaries_completed,
                        "summaries_failed": summaries_failed
                    }
                else:
                    # Scraping done but queue not empty - continue summarizing
                    return {
                        **state,
                        "status": "summarizing",
                        "summaries": summaries,
                        "summaries_completed": summaries_completed,
                        "summaries_failed": summaries_failed
                    }
            
        except Exception as e:
            logger.error(f"Error in summarize node: {e}", exc_info=True)
            return {
                **state,
                "status": "error",
                "error": str(e)
            }
    
    def route_after_scrape(state: NewsScraperState) -> str:
        """
        Conditional edge after scrape: route to summarize if queue has items,
        otherwise continue scraping or end.
        """
        status = state.get("status", "scraping")
        article_queue = state.get("article_queue", Queue())
        article_urls = state.get("article_urls", [])
        current_index = state.get("current_url_index", 0)
        
        if status == "error":
            return "end"
        
        if status == "completed":
            return "end"
        
        # If queue has items, route to summarize (async processing)
        if not article_queue.empty():
            return "summarize"
        
        # If more articles to scrape, continue scraping
        if current_index < len(article_urls):
            return "scrape"
        
        # Otherwise we're done
        return "end"
    
    def route_after_summarize(state: NewsScraperState) -> str:
        """
        Conditional edge after summarize: route back to scrape if more articles,
        or continue summarizing if queue has items, or end if done.
        """
        status = state.get("status", "summarizing")
        article_queue = state.get("article_queue", Queue())
        article_urls = state.get("article_urls", [])
        current_index = state.get("current_url_index", 0)
        
        if status == "error":
            return "end"
        
        if status == "completed":
            return "end"
        
        # If more articles to scrape, go back to scrape
        if current_index < len(article_urls):
            return "scrape"
        
        # If queue still has items, continue summarizing
        if not article_queue.empty():
            return "summarize"
        
        # Otherwise we're done
        return "end"
    
    # Create workflow
    workflow = StateGraph(NewsScraperState)
    
    # Add nodes
    workflow.add_node("scrape", scrape_node)
    workflow.add_node("summarize", summarize_node)
    
    # Set entry point
    workflow.set_entry_point("scrape")
    
    # Add conditional edges for asynchronous processing
    workflow.add_conditional_edges(
        "scrape",
        route_after_scrape,
        {
            "summarize": "summarize",
            "scrape": "scrape",  # Loop back to continue scraping
            "end": END
        }
    )
    
    # Add conditional edges from summarize
    workflow.add_conditional_edges(
        "summarize",
        route_after_summarize,
        {
            "scrape": "scrape",  # Go back to scrape if more articles
            "summarize": "summarize",  # Continue summarizing if queue has items
            "end": END
        }
    )
    
    # Compile and return
    app = workflow.compile()
    return app


# Export for use in agent
__all__ = ['NewsScraperState', 'create_workflow', 'LANGGRAPH_AVAILABLE']

