"""Queue Manager for Financial Report Analyzer - handles incoming file paths from news_scraper."""

import asyncio
from typing import Dict, Any, Optional
from pathlib import Path
from queue import Queue, Empty
import threading


class FilePathQueue:
    """
    Thread-safe queue for receiving file paths from news_scraper agent.
    
    The queue stores file paths that need to be analyzed.
    """
    
    def __init__(self, logger=None):
        """
        Initialize the file path queue.
        
        Args:
            logger: Logger instance
        """
        self.queue = Queue()
        self.logger = logger
        self._lock = threading.Lock()
        self._stats = {
            "total_received": 0,
            "total_processed": 0,
            "total_failed": 0
        }
    
    def enqueue(self, file_path: str, url: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Add a file path to the queue.
        
        Args:
            file_path: Path to the scraped article file
            url: Original URL of the article (optional)
            metadata: Additional metadata (optional)
            
        Returns:
            True if successfully enqueued
        """
        try:
            item = {
                "file_path": file_path,
                "url": url,
                "metadata": metadata or {}
            }
            self.queue.put(item)
            
            with self._lock:
                self._stats["total_received"] += 1
            
            if self.logger:
                self.logger.debug(f"Enqueued file path: {file_path}")
            
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error enqueueing file path {file_path}: {e}")
            return False
    
    def dequeue(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Get next file path from queue.
        
        Args:
            timeout: Timeout in seconds (None = blocking)
            
        Returns:
            Dict with file_path, url, metadata, or None if timeout
        """
        try:
            item = self.queue.get(timeout=timeout)
            return item
        except Empty:
            return None
    
    def size(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()
    
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return self.queue.empty()
    
    def mark_processed(self):
        """Mark an item as successfully processed."""
        with self._lock:
            self._stats["total_processed"] += 1
    
    def mark_failed(self):
        """Mark an item as failed."""
        with self._lock:
            self._stats["total_failed"] += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        with self._lock:
            return {
                **self._stats,
                "queue_size": self.size(),
                "pending": self._stats["total_received"] - self._stats["total_processed"] - self._stats["total_failed"]
            }

