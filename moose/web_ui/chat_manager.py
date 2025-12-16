"""Chat Manager for Moose Web UI.

Handles chat message buffering, SSE streaming, and historical llm.log file management.
Uses file-based tailing to work across process boundaries.
"""

import os
import re
import json
import time
from collections import deque
from pathlib import Path
from queue import Queue, Empty
from threading import Lock, Thread
from typing import Dict, List, Optional, Generator, Any
from datetime import datetime


class ChatManager:
    """Manages chat message buffering and streaming for the web UI.
    
    Features:
    - Per-project message buffers
    - SSE streaming to subscribers via file tailing
    - Historical llm.log file listing and parsing
    """
    
    def __init__(self, max_buffer_size: int = 500):
        """Initialize the ChatManager.
        
        Args:
            max_buffer_size: Maximum number of messages to keep in buffer per project
        """
        self._buffers: Dict[str, deque] = {}  # project_id -> deque of messages
        self._subscribers: Dict[str, List[Queue]] = {}  # project_id -> list of subscriber queues
        self._lock = Lock()
        self._max_buffer_size = max_buffer_size
        
        # File watching state
        self._file_positions: Dict[str, int] = {}  # project_id -> last file position
        self._file_inodes: Dict[str, int] = {}  # project_id -> file inode (for rotation detection)
        self._watchers: Dict[str, Thread] = {}  # project_id -> watcher thread
        self._watching: Dict[str, bool] = {}  # project_id -> is watching flag
        
        # Track seen message content hashes to avoid duplicates
        self._seen_hashes: Dict[str, set] = {}  # project_id -> set of content hashes
    
    def _get_content_hash(self, message: dict) -> str:
        """Generate a hash based on message content for deduplication.
        
        Uses type + content to identify unique messages, ignoring
        request_id and timestamp which can vary for the same message.
        
        Args:
            message: Chat message dict
            
        Returns:
            Hash string for deduplication
        """
        msg_type = message.get('type', '')
        content = message.get('content', '')
        
        # For content that's a list (e.g., Anthropic format), convert to string
        if isinstance(content, list):
            content = json.dumps(content, sort_keys=True)
        elif isinstance(content, dict):
            content = json.dumps(content, sort_keys=True)
        
        # Include tool_call_id for tool messages to distinguish different tool results
        tool_call_id = message.get('tool_call_id', '')
        
        # Create a stable hash from type + content + tool_call_id
        hash_input = f"{msg_type}:{tool_call_id}:{content}"
        return str(hash(hash_input))
    
    def _add_message_internal(self, project_id: str, message: dict):
        """Add a chat message and notify all subscribers (internal use).
        
        Args:
            project_id: Project identifier
            message: Chat message dict
        """
        with self._lock:
            # Ensure buffer exists
            if project_id not in self._buffers:
                self._buffers[project_id] = deque(maxlen=self._max_buffer_size)
            
            if project_id not in self._seen_hashes:
                self._seen_hashes[project_id] = set()
            
            # Add message ID if not present
            if 'id' not in message:
                message['id'] = f"{project_id}_{datetime.now().timestamp()}"
            
            # Generate content hash for deduplication
            content_hash = self._get_content_hash(message)
            
            # Skip if content already seen (same type + content)
            if content_hash in self._seen_hashes[project_id]:
                return
            
            self._seen_hashes[project_id].add(content_hash)
            
            # Limit seen hashes set size
            if len(self._seen_hashes[project_id]) > self._max_buffer_size * 2:
                # Keep only recent hashes
                self._seen_hashes[project_id] = set(
                    list(self._seen_hashes[project_id])[-self._max_buffer_size:]
                )
            
            # Add to buffer
            self._buffers[project_id].append(message)
            
            # Notify subscribers
            if project_id in self._subscribers:
                for q in self._subscribers[project_id]:
                    try:
                        q.put_nowait(message)
                    except Exception as e:
                        # Queue notification failed, but continue to other subscribers
                        pass
    
    def add_message(self, project_id: str, message: dict):
        """Add a chat message (legacy interface, now a no-op for cross-process).
        
        Messages are now read from the log file instead.
        This method is kept for backward compatibility but does nothing
        since messages are tailed from the llm.log file.
        """
        # No-op: messages are read from file via file tailing
        pass
    
    def subscribe(self, project_id: str) -> Queue:
        """Subscribe to chat updates for a project.
        
        Args:
            project_id: Project identifier
            
        Returns:
            Queue that will receive chat messages
        """
        with self._lock:
            if project_id not in self._subscribers:
                self._subscribers[project_id] = []
            
            q = Queue()
            self._subscribers[project_id].append(q)
            return q
    
    def unsubscribe(self, project_id: str, queue: Queue):
        """Unsubscribe from chat updates.
        
        Args:
            project_id: Project identifier
            queue: The queue to remove
        """
        with self._lock:
            if project_id in self._subscribers:
                if queue in self._subscribers[project_id]:
                    self._subscribers[project_id].remove(queue)
    
    def get_buffer(self, project_id: str, limit: Optional[int] = None) -> List[dict]:
        """Get buffered messages for a project.
        
        Args:
            project_id: Project identifier
            limit: Maximum number of messages to return (from end)
            
        Returns:
            List of chat messages
        """
        with self._lock:
            if project_id not in self._buffers:
                return []
            
            buffer = list(self._buffers[project_id])
            if limit:
                return buffer[-limit:]
            return buffer
    
    def _get_current_log_file(self, project_id: str) -> Optional[Path]:
        """Get the current (active) llm.log file for a project.
        
        The current file is the one with the highest number (llm.log.N).
        If no numbered files exist, falls back to llm.log.
        
        Args:
            project_id: Project identifier
            
        Returns:
            Path to current llm.log file or None
        """
        log_dir = self._get_log_dir(project_id)
        if not log_dir or not log_dir.exists():
            return None
        
        current_file = None
        i = 0
        while i < 1000:
            if i == 0:
                next_file = log_dir / "llm.log"
            else:
                next_file = log_dir / f"llm.log.{i}"
            if not next_file.exists():
                if current_file is None:
                    raise FileNotFoundError(f"Log folder {log_dir} is empty")
                return current_file
            current_file = next_file
            i += 1
        
        raise FileNotFoundError(f"Log file llm.log exceeds the maximum number of 1000 files")
    
    def _tail_file(self, project_id: str):
        """Tail the llm.log file for new entries.
        
        This method reads new lines from the log file and processes them.
        Called periodically by the SSE stream generator.
        
        Args:
            project_id: Project identifier
        """
        try:
            log_file = self._get_current_log_file(project_id)
            if not log_file:
                return
        except FileNotFoundError:
            # Log file doesn't exist yet, wait for it to be created
            return
        
        try:
            stat = log_file.stat()
            current_inode = stat.st_ino
            current_size = stat.st_size
            
            # Initialize position if not set (start from beginning - new file for this run)
            if project_id not in self._file_positions:
                self._file_positions[project_id] = 0
                self._file_inodes[project_id] = current_inode
            
            # Check for file rotation (inode changed - new file created)
            if project_id in self._file_inodes:
                if self._file_inodes[project_id] != current_inode:
                    # File was rotated/new file created, start from beginning
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
                
                try:
                    entry = json.loads(line)
                    parsed_messages = self._parse_llm_entry(entry)
                    for msg in parsed_messages:
                        self._add_message_internal(project_id, msg)
                except json.JSONDecodeError:
                    # Try to extract JSON from log line
                    json_match = re.search(r'(?:LLM (?:Request|Response): )?(\{.*\})$', line)
                    if json_match:
                        try:
                            entry = json.loads(json_match.group(1))
                            parsed_messages = self._parse_llm_entry(entry)
                            for msg in parsed_messages:
                                self._add_message_internal(project_id, msg)
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass
    
    def _start_watching(self, project_id: str):
        """Start background file watcher for a project if not already watching.
        
        The watcher continuously monitors the current log file and populates the live buffer.
        Each tool run creates a new log file, so we read from the beginning.
        
        Args:
            project_id: Project identifier
        """
        with self._lock:
            if project_id in self._watchers and self._watching.get(project_id, False):
                # Already watching
                return
            
            # Initialize file position - not needed here, will be set in _tail_file
            # Each tool run creates a new log file, so we read from beginning
            pass
            
            # Start watcher thread
            self._watching[project_id] = True
            watcher = Thread(target=self._watch_file, args=(project_id,), daemon=True)
            watcher.start()
            self._watchers[project_id] = watcher
    
    def _watch_file(self, project_id: str):
        """Background thread that continuously tails the log file.
        
        Args:
            project_id: Project identifier
        """
        tail_interval = 0.5  # Check file every 500ms
        
        while self._watching.get(project_id, False):
            try:
                self._tail_file(project_id)
            except Exception:
                pass
            time.sleep(tail_interval)
    
    def _stop_watching(self, project_id: str):
        """Stop the background watcher for a project.
        
        Args:
            project_id: Project identifier
        """
        with self._lock:
            self._watching[project_id] = False
            if project_id in self._watchers:
                del self._watchers[project_id]
    
    def list_chat_files(self, project_id: str, base_dir: Optional[Path] = None) -> List[str]:
        """List available llm.log files for a project.
        
        Args:
            project_id: Project identifier
            base_dir: Base directory for projects
            
        Returns:
            List of llm.log file names sorted by suffix (llm.log, llm.log.1, llm.log.2, ...)
        """
        log_dir = self._get_log_dir(project_id, base_dir)
        if not log_dir or not log_dir.exists():
            return []
        
        files = []
        pattern = re.compile(r'^llm\.log(\.\d+)?$')
        
        for f in log_dir.iterdir():
            if f.is_file() and pattern.match(f.name):
                files.append(f.name)
        
        # Sort: llm.log first, then llm.log.1, llm.log.2, etc.
        def sort_key(name):
            if name == 'llm.log':
                return (0, 0)
            match = re.search(r'\.(\d+)$', name)
            if match:
                return (1, int(match.group(1)))
            return (2, 0)
        
        return sorted(files, key=sort_key)
    
    def read_chat_file(
        self, 
        project_id: str, 
        filename: str, 
        base_dir: Optional[Path] = None
    ) -> List[dict]:
        """Read and parse a historical llm.log file.
        
        Args:
            project_id: Project identifier
            filename: Log file name (e.g., 'llm.log.1')
            base_dir: Base directory for projects
            
        Returns:
            List of parsed chat messages
        """
        log_dir = self._get_log_dir(project_id, base_dir)
        if not log_dir:
            return []
        
        log_file = log_dir / filename
        if not log_file.exists():
            return []
        
        messages = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        entry = json.loads(line)
                        parsed_messages = self._parse_llm_entry(entry)
                        messages.extend(parsed_messages)
                    except json.JSONDecodeError:
                        # Try to extract JSON from log line
                        # Format might be: LLM Request: {...} or LLM Response: {...}
                        json_match = re.search(r'(?:LLM (?:Request|Response): )?(\{.*\})$', line)
                        if json_match:
                            try:
                                entry = json.loads(json_match.group(1))
                                parsed_messages = self._parse_llm_entry(entry)
                                messages.extend(parsed_messages)
                            except json.JSONDecodeError:
                                pass
        except Exception:
            pass
        
        return messages
    
    def _derive_display_name(self, model: str) -> str:
        """Derive a friendly display name from a model identifier.
        
        Args:
            model: Model identifier (e.g., "claude-opus-4-5-20251101", "gpt-4o")
            
        Returns:
            Friendly display name (e.g., "Claude Opus", "GPT-4o")
        """
        if not model:
            return "AI"
        
        return model
    
    def _parse_llm_entry(self, entry: dict) -> List[dict]:
        """Parse an LLM log entry into chat messages.
        
        Args:
            entry: Raw LLM log entry
            
        Returns:
            List of chat message dicts
        """
        messages = []
        timestamp = entry.get('timestamp', datetime.now().isoformat())
        direction = entry.get('direction', '')
        request_id = entry.get('request_id', '')
        
        # Extract model name for display (can be used as agent name fallback)
        model = entry.get('model', '')
        agent_name = entry.get('agent_name', '')  # Optional agent_name from log entry
        
        # New format: single 'message' field
        message = entry.get('message')
        if message:
            chat_msg = self._convert_message(message, timestamp, request_id)
            if chat_msg:
                chat_msg['direction'] = direction
                
                # Add agent name and model name for AI messages
                if chat_msg['type'] == 'ai':
                    if agent_name:
                        chat_msg['agent_name'] = agent_name
                    chat_msg['model_name'] = self._derive_display_name(model) if model else None
                
                # Add usage and cost info for responses
                if entry.get('usage'):
                    chat_msg['usage'] = entry['usage']
                if entry.get('cost'):
                    chat_msg['cost'] = entry['cost']
                
                # Add tool name for tool results
                if entry.get('tool_name'):
                    chat_msg['tool_name'] = entry['tool_name']
                
                messages.append(chat_msg)
        
        # Legacy format support: 'messages' array for request
        elif direction == 'request' and entry.get('messages'):
            for msg in entry.get('messages', []):
                chat_msg = self._convert_message(msg, timestamp, request_id)
                if chat_msg:
                    chat_msg['direction'] = direction
                    messages.append(chat_msg)
        
        # Legacy format support: 'response' field for response
        elif direction == 'response' and entry.get('response'):
            response = entry.get('response', {})
            chat_msg = self._convert_message(response, timestamp, request_id)
            if chat_msg:
                chat_msg['direction'] = direction
                if entry.get('usage'):
                    chat_msg['usage'] = entry['usage']
                if entry.get('cost'):
                    chat_msg['cost'] = entry['cost']
                messages.append(chat_msg)
        
        return messages
    
    def _convert_message(self, msg: dict, timestamp: str, request_id: str) -> Optional[dict]:
        """Convert a raw message to a chat message format.
        
        Args:
            msg: Raw message dict from LLM log
            timestamp: Timestamp string
            request_id: Request ID
            
        Returns:
            Chat message dict or None
        """
        if not msg:
            return None
        
        msg_type = msg.get('type', 'unknown')
        content = msg.get('content', '')
        
        # Map message types
        type_map = {
            'SystemMessage': 'system',
            'HumanMessage': 'human',
            'AIMessage': 'ai',
            'ToolMessage': 'tool'
        }
        
        chat_type = type_map.get(msg_type, msg_type.lower() if isinstance(msg_type, str) else 'unknown')
        
        result = {
            'id': f"{request_id}_{timestamp}_{chat_type}",
            'type': chat_type,
            'content': content,
            'timestamp': timestamp,
            'request_id': request_id
        }
        
        # Include tool calls if present (for AI messages)
        if msg.get('tool_calls'):
            result['tool_calls'] = msg['tool_calls']
        
        # Include tool_call_id if present (for Tool messages)
        if msg.get('tool_call_id'):
            result['tool_call_id'] = msg['tool_call_id']

        # Prefer tool name if present in the serialized ToolMessage itself
        # (this is more reliable than trying to infer it later).
        if chat_type == 'tool':
            tool_name = msg.get('name')
            if tool_name:
                result['tool_name'] = tool_name
        
        # Include usage metadata if present
        if msg.get('usage_metadata'):
            result['usage_metadata'] = msg['usage_metadata']
        
        return result
    
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
        """Generate SSE stream for a project's chat messages.
        
        Streams from the live buffer which is continuously populated by a background watcher.
        The current log file (highest numbered) represents the current tool run.
        On connection, sends all messages from the current file (persists on refresh).
        
        Args:
            project_id: Project identifier
            
        Yields:
            SSE formatted strings
        """
        # Start background watcher if not already running
        self._start_watching(project_id)
        
        # Subscribe to live updates
        queue = self.subscribe(project_id)
        
        try:
            # Send accumulated messages from current session (for persistence on refresh)
            for msg in self.get_buffer(project_id):
                yield f"data: {json.dumps(msg)}\n\n"
            
            # Continue streaming new messages as they arrive
            keepalive_timeout = 30  # Send keepalive every 30 seconds
            
            while True:
                try:
                    # Wait for new messages from the live buffer
                    msg = queue.get(timeout=keepalive_timeout)
                    yield f"data: {json.dumps(msg)}\n\n"
                except Empty:
                    # Send keepalive to prevent connection timeout
                    yield f": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            self.unsubscribe(project_id, queue)


# Global singleton instance
_chat_manager: Optional[ChatManager] = None


def get_chat_manager() -> ChatManager:
    """Get the global ChatManager instance.
    
    Returns:
        ChatManager singleton
    """
    global _chat_manager
    if _chat_manager is None:
        _chat_manager = ChatManager()
    return _chat_manager
