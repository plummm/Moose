"""Log Manager for Moose Web UI.

Handles log buffering, SSE streaming, and historical log file management.
Uses file-based tailing to work across process boundaries.
"""

import os
import re
import json
import time
from collections import deque
from pathlib import Path
from queue import Queue, Empty
from threading import Lock
from typing import Dict, List, Optional, Generator
from datetime import datetime


class LogManager:
    """Manages log buffering and streaming for the web UI.
    
    Features:
    - Per-project log buffers
    - SSE streaming to subscribers via file tailing
    - Historical log file listing and reading
    """
    
    def __init__(self, max_buffer_size: int = 1000):
        """Initialize the LogManager.
        
        Args:
            max_buffer_size: Maximum number of log entries to keep in buffer per project
        """
        self._buffers: Dict[str, deque] = {}  # project_id -> deque of log entries
        self._subscribers: Dict[str, List[Queue]] = {}  # project_id -> list of subscriber queues
        self._lock = Lock()
        self._max_buffer_size = max_buffer_size
        
        # File watching state
        self._file_positions: Dict[str, int] = {}  # project_id -> last file position
        self._file_inodes: Dict[str, int] = {}  # project_id -> file inode (for rotation detection)
        
        # Track seen log entries to avoid duplicates (using hash of content)
        self._seen_hashes: Dict[str, set] = {}  # project_id -> set of seen hashes
    
    def _add_log_internal(self, project_id: str, entry: dict):
        """Add a log entry and notify all subscribers (internal use).
        
        Args:
            project_id: Project identifier
            entry: Log entry dict with keys like 'time', 'level', 'message', 'path', 'line'
        """
        with self._lock:
            # Ensure buffer exists
            if project_id not in self._buffers:
                self._buffers[project_id] = deque(maxlen=self._max_buffer_size)
            
            if project_id not in self._seen_hashes:
                self._seen_hashes[project_id] = set()
            
            # Add timestamp if not present
            if 'time' not in entry:
                entry['time'] = datetime.now().isoformat()
            
            # Create a hash for deduplication
            entry_hash = hash(f"{entry.get('time', '')}_{entry.get('message', '')}")
            
            # Skip if already seen
            if entry_hash in self._seen_hashes[project_id]:
                return
            
            self._seen_hashes[project_id].add(entry_hash)
            
            # Limit seen hashes set size
            if len(self._seen_hashes[project_id]) > self._max_buffer_size * 2:
                # Keep only recent hashes
                self._seen_hashes[project_id] = set(
                    list(self._seen_hashes[project_id])[-self._max_buffer_size:]
                )
            
            # Add to buffer
            self._buffers[project_id].append(entry)
            
            # Notify subscribers
            if project_id in self._subscribers:
                dead_queues = []
                for q in self._subscribers[project_id]:
                    try:
                        q.put_nowait(entry)
                    except Exception:
                        dead_queues.append(q)
                
                # Clean up dead queues
                for q in dead_queues:
                    self._subscribers[project_id].remove(q)
    
    def add_log(self, project_id: str, entry: dict):
        """Add a log entry (legacy interface, now a no-op for cross-process).
        
        Logs are now read from the log file instead.
        This method is kept for backward compatibility but does nothing
        since logs are tailed from the moose.log file.
        """
        # No-op: logs are read from file via file tailing
        pass
    
    def subscribe(self, project_id: str) -> Queue:
        """Subscribe to log updates for a project.
        
        Args:
            project_id: Project identifier
            
        Returns:
            Queue that will receive log entries
        """
        with self._lock:
            if project_id not in self._subscribers:
                self._subscribers[project_id] = []
            
            q = Queue()
            self._subscribers[project_id].append(q)
            return q
    
    def unsubscribe(self, project_id: str, queue: Queue):
        """Unsubscribe from log updates.
        
        Args:
            project_id: Project identifier
            queue: The queue to remove
        """
        with self._lock:
            if project_id in self._subscribers:
                if queue in self._subscribers[project_id]:
                    self._subscribers[project_id].remove(queue)
    
    def get_buffer(self, project_id: str, limit: Optional[int] = None) -> List[dict]:
        """Get buffered logs for a project.
        
        Args:
            project_id: Project identifier
            limit: Maximum number of entries to return (from end)
            
        Returns:
            List of log entries
        """
        with self._lock:
            if project_id not in self._buffers:
                return []
            
            buffer = list(self._buffers[project_id])
            if limit:
                return buffer[-limit:]
            return buffer
    
    def _get_current_log_file(self, project_id: str) -> Optional[Path]:
        """Get the current (active) moose.log file for a project.
        
        Args:
            project_id: Project identifier
            
        Returns:
            Path to moose.log file or None
        """
        log_dir = self._get_log_dir(project_id)
        if not log_dir or not log_dir.exists():
            return None
        
        log_file = log_dir / "moose.log"
        if log_file.exists():
            return log_file
        return None
    
    def _tail_file(self, project_id: str):
        """Tail the moose.log file for new entries.
        
        This method reads new lines from the log file and processes them.
        Called periodically by the SSE stream generator.
        
        Args:
            project_id: Project identifier
        """
        log_file = self._get_current_log_file(project_id)
        if not log_file:
            return
        
        try:
            stat = log_file.stat()
            current_inode = stat.st_ino
            current_size = stat.st_size
            
            # Check for file rotation (inode changed)
            if project_id in self._file_inodes:
                if self._file_inodes[project_id] != current_inode:
                    # File was rotated, reset position
                    self._file_positions[project_id] = 0
            
            self._file_inodes[project_id] = current_inode
            
            # Get last read position
            last_pos = self._file_positions.get(project_id, 0)
            
            # If file is smaller than last position, it was truncated
            if current_size < last_pos:
                last_pos = 0
            
            # No new data
            if current_size == last_pos:
                return
            
            # Read new lines
            with open(log_file, 'r', encoding='utf-8') as f:
                f.seek(last_pos)
                new_content = f.read()
                new_pos = f.tell()
            
            self._file_positions[project_id] = new_pos
            
            # Process new lines
            for line in new_content.strip().split('\n'):
                if not line.strip():
                    continue
                
                entry = self._parse_log_line(line.strip())
                if entry:
                    self._add_log_internal(project_id, entry)
        except Exception:
            pass
    
    def _load_initial_buffer(self, project_id: str, limit: int = 100):
        """Load existing log file content into buffer on first connect.
        
        Args:
            project_id: Project identifier
            limit: Maximum number of lines to load initially
        """
        if project_id in self._file_positions:
            # Already initialized
            return
        
        log_file = self._get_current_log_file(project_id)
        if not log_file:
            self._file_positions[project_id] = 0
            return
        
        try:
            # Read last N lines for initial buffer
            entries = self.read_log_file(project_id, "moose.log", limit=limit)
            for entry in entries:
                self._add_log_internal(project_id, entry)
            
            # Set position to end of file
            self._file_positions[project_id] = log_file.stat().st_size
            self._file_inodes[project_id] = log_file.stat().st_ino
        except Exception:
            self._file_positions[project_id] = 0
    
    def list_log_files(self, project_id: str, base_dir: Optional[Path] = None) -> List[str]:
        """List available log files for a project.
        
        Args:
            project_id: Project identifier
            base_dir: Base directory for projects (defaults to MOOSE_PROJECTS_DIR or cwd)
            
        Returns:
            List of log file names sorted by suffix (moose.log, moose.log.1, moose.log.2, ...)
        """
        log_dir = self._get_log_dir(project_id, base_dir)
        if not log_dir or not log_dir.exists():
            return []
        
        files = []
        pattern = re.compile(r'^moose\.log(\.\d+)?$')
        
        for f in log_dir.iterdir():
            if f.is_file() and pattern.match(f.name):
                files.append(f.name)
        
        # Sort: moose.log first, then moose.log.1, moose.log.2, etc.
        def sort_key(name):
            if name == 'moose.log':
                return (0, 0)
            match = re.search(r'\.(\d+)$', name)
            if match:
                return (1, int(match.group(1)))
            return (2, 0)
        
        return sorted(files, key=sort_key)
    
    def read_log_file(
        self, 
        project_id: str, 
        filename: str, 
        base_dir: Optional[Path] = None,
        limit: Optional[int] = None
    ) -> List[dict]:
        """Read a historical log file.
        
        Args:
            project_id: Project identifier
            filename: Log file name (e.g., 'moose.log.1')
            base_dir: Base directory for projects
            limit: Maximum number of lines to return (from end)
            
        Returns:
            List of parsed log entries
        """
        log_dir = self._get_log_dir(project_id, base_dir)
        if not log_dir:
            return []
        
        log_file = log_dir / filename
        if not log_file.exists():
            return []
        
        entries = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if limit:
                    lines = lines[-limit:]
                
                for line in lines:
                    entry = self._parse_log_line(line.strip())
                    if entry:
                        entries.append(entry)
        except Exception:
            pass
        
        return entries
    
    def _parse_log_line(self, line: str) -> Optional[dict]:
        """Parse a log line into a structured entry.
        
        Expected format: 
        2025-12-11 10:23:45 | [label] /path/to/file.py:123 | LEVEL    | message
        
        Args:
            line: Raw log line
            
        Returns:
            Parsed entry dict or None if parsing fails
        """
        if not line:
            return None
        
        # Try to parse standard format
        # Format: timestamp | [label] path:line | LEVEL | message
        pattern = r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (\[[^\]]+\]) ([^|]+) \| (\w+)\s*\| (.*)$'
        match = re.match(pattern, line)
        
        if match:
            timestamp, label, path_line, level, message = match.groups()
            # Parse path:line
            path_match = re.match(r'^(.+):(\d+)$', path_line.strip())
            path = path_match.group(1) if path_match else path_line.strip()
            line_num = int(path_match.group(2)) if path_match else 0
            
            return {
                'time': timestamp,
                'label': label,
                'path': path,
                'line': line_num,
                'level': level.strip(),
                'message': message
            }
        
        # Fallback: return raw line as message
        return {
            'time': datetime.now().isoformat(),
            'level': 'INFO',
            'message': line
        }
    
    def _get_log_dir(self, project_id: str, base_dir: Optional[Path] = None) -> Optional[Path]:
        """Get the log directory for a project.
        
        Args:
            project_id: Project identifier
            base_dir: Base directory override
            
        Returns:
            Path to log directory or None
        """
        # Check environment variable first
        env_dir = os.environ.get('MOOSE_PROJECTS_DIR')
        if env_dir:
            return Path(env_dir) / project_id / "logs"
        
        if base_dir:
            return base_dir / project_id / "logs"
        
        # Default to cwd/projects
        return Path.cwd() / "projects" / project_id / "logs"
    
    def generate_sse_stream(self, project_id: str) -> Generator[str, None, None]:
        """Generate SSE stream for a project's logs.
        
        Uses file tailing to get real-time updates from moose.log.
        
        Args:
            project_id: Project identifier
            
        Yields:
            SSE formatted strings
        """
        queue = self.subscribe(project_id)
        
        try:
            # Load initial buffer from file on first connect
            self._load_initial_buffer(project_id)
            
            # Send buffered logs
            for entry in self.get_buffer(project_id):
                yield f"data: {json.dumps(entry)}\n\n"
            
            # Stream new logs via file tailing
            last_tail_time = 0
            tail_interval = 0.5  # Check file every 500ms
            
            while True:
                try:
                    # Check for queued entries (from file tailing)
                    entry = queue.get(timeout=tail_interval)
                    yield f"data: {json.dumps(entry)}\n\n"
                except Empty:
                    # Tail the file for new entries
                    current_time = time.time()
                    if current_time - last_tail_time >= tail_interval:
                        self._tail_file(project_id)
                        last_tail_time = current_time
                    
                    # Send keepalive every 30 seconds
                    yield f": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            self.unsubscribe(project_id, queue)


# Global singleton instance
_log_manager: Optional[LogManager] = None


def get_log_manager() -> LogManager:
    """Get the global LogManager instance.
    
    Returns:
        LogManager singleton
    """
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager()
    return _log_manager
