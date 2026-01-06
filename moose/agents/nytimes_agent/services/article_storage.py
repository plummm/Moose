"""Article storage and deduplication using URL-based index."""

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime


class ArticleStorage:
    """
    Handles article file storage and URL-based deduplication.
    
    Similar to NewsScraperCore but focused on NYTimes articles.
    """
    
    def __init__(self, data_dir: Path, logger=None):
        """
        Initialize article storage.
        
        Args:
            data_dir: Base directory for storing articles
            logger: Logger instance
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.index_file = self.data_dir / ".index.json"
        self.index: Dict[str, Dict[str, Any]] = {}
        self._load_index()
    
    def _load_index(self):
        """Load the deduplication index from file."""
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    self.index = json.load(f)
                if self.logger:
                    self.logger.info(f"Loaded index with {len(self.index)} entries")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to load index: {e}, starting with empty index")
                self.index = {}
        else:
            self.index = {}
    
    def _save_index(self):
        """Save the deduplication index to file."""
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(self.index, f, indent=2)
            if self.logger:
                self.logger.debug(f"Saved index with {len(self.index)} entries")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to save index: {e}")
    
    def _get_file_path_for_url(
        self,
        url: str,
        article_date: Optional[datetime] = None,
        *,
        extension: str = ".json",
    ) -> Path:
        """
        Get the expected file path for a URL.
        
        Args:
            url: Article URL
            article_date: Article date (defaults to current date)
            extension: File extension to use (default: .json)
            
        Returns:
            Expected file path
        """
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        
        if article_date is None:
            article_date = datetime.now()
        
        ext = str(extension or "").strip() or ".json"
        if not ext.startswith("."):
            ext = "." + ext

        date_folder = self.data_dir / str(article_date.year) / f"{article_date.month:02d}" / f"{article_date.day:02d}"
        return date_folder / f"{url_hash}{ext}"
    
    def is_url_scraped(self, url: str) -> bool:
        """
        Check if a URL has already been processed.
        
        Args:
            url: Article URL to check
            
        Returns:
            True if URL has been processed, False otherwise
        """
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        
        # Check index first (faster)
        if url_hash in self.index:
            index_entry = self.index[url_hash]
            file_path = Path(index_entry.get("file_path", ""))
            # Verify file still exists
            if file_path.exists():
                return True
            else:
                # File was deleted, remove from index
                del self.index[url_hash]
                self._save_index()
        
        # Fallback to file system check
        file_path = self._get_file_path_for_url(url, extension=".json")
        if file_path.exists():
            # Add to index for future lookups
            self.index[url_hash] = {
                "file_path": str(file_path),
                "scraped_at": datetime.now().isoformat()
            }
            self._save_index()
            return True
        
        return False
    
    def save_article_json(
        self,
        url: str,
        article_json: Dict[str, Any],
        article_date: Optional[datetime] = None,
    ) -> Optional[Path]:
        """
        Save article as JSON and update index.
        
        Args:
            url: Article URL
            article_json: Article data to save
            article_date: Article publication date (defaults to current date)
            
        Returns:
            Path to saved file, or None if save failed
        """
        if not isinstance(article_json, dict):
            return None
        
        # Ensure URL is in the article data
        article_json["url"] = url
        
        file_path = self._get_file_path_for_url(url, article_date, extension=".json")
        date_folder = file_path.parent
        date_folder.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(article_json, f, ensure_ascii=False, indent=2)

            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
            self.index[url_hash] = {
                "file_path": str(file_path),
                "scraped_at": datetime.now().isoformat(),
            }
            self._save_index()

            if self.logger:
                self.logger.info(f"Saved article JSON to: {file_path}")
            return file_path
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error saving article JSON to {file_path}: {e}")
            return None
    
    def save_index(self):
        """Manually save the index (called after processing session)."""
        self._save_index()

