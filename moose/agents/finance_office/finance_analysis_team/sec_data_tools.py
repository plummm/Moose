"""SEC Data Tools - LangChain tools for accessing SEC data via edgartools MCP."""

import os
import shutil
from typing import Dict, Any, Optional, List

# Try to import MCP adapters
try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    MCP_ADAPTERS_AVAILABLE = True
except ImportError:
    MCP_ADAPTERS_AVAILABLE = False
    MultiServerMCPClient = None

# Try to import LangChain tools
try:
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field
    LANGCHAIN_TOOLS_AVAILABLE = True
except ImportError:
    LANGCHAIN_TOOLS_AVAILABLE = False
    StructuredTool = None
    BaseModel = None
    Field = None

# Fallback to direct edgartools if MCP not available
try:
    from edgar import Company, set_identity
    from edgar.financials import Financials
    EDGARTOOLS_AVAILABLE = True
except ImportError:
    EDGARTOOLS_AVAILABLE = False
    Company = None
    set_identity = None
    Financials = None


class SECDataTools:
    """
    SEC Data Tools - Provides LangChain tools for accessing SEC data via edgartools MCP.
    
    Wraps edgartools MCP server tools as LangChain StructuredTool instances
    that can be used by LangChain agents.
    """
    
    def __init__(
        self,
        identity: Optional[str] = None,
        use_mcp: bool = True,
        python_command: Optional[str] = None,
        logger=None
    ):
        """
        Initialize SEC Data Tools.
        
        Args:
            identity: SEC identity string (format: "Name email@example.com")
                     If None, uses EDGAR_IDENTITY environment variable
            use_mcp: Whether to use MCP server (True) or direct edgartools (False)
            python_command: Python command to use for MCP server (default: "python3" or "python")
            logger: Logger instance
        """
        self.logger = logger
        self.use_mcp = use_mcp
        
        # Set SEC identity (required by SEC)
        identity = identity or os.getenv("EDGAR_IDENTITY")
        if not identity:
            if self.logger:
                self.logger.warning(
                    "EDGAR_IDENTITY not set. Set it via environment variable or identity parameter. "
                    "Format: 'Your Name your.email@example.com'"
                )
        self.identity = identity
        
        # Initialize MCP client if using MCP
        self.mcp_client = None
        self.mcp_tools = {}
        self._tools_loaded = False
        
        if use_mcp:
            if not MCP_ADAPTERS_AVAILABLE:
                if self.logger:
                    self.logger.warning(
                        "langchain-mcp-adapters not available. Install with: pip install langchain-mcp-adapters. "
                        "Falling back to direct edgartools."
                    )
                use_mcp = False
                self.use_mcp = False
            
            if not LANGCHAIN_TOOLS_AVAILABLE:
                if self.logger:
                    self.logger.warning(
                        "LangChain tools not available. Install with: pip install langchain-core. "
                        "Falling back to direct edgartools."
                    )
                use_mcp = False
                self.use_mcp = False
        
        if use_mcp:
            try:
                # Determine python command
                if python_command is None:
                    if shutil.which("python3"):
                        python_cmd = "python3"
                    elif shutil.which("python"):
                        python_cmd = "python"
                    else:
                        raise RuntimeError("Python not found in PATH")
                else:
                    python_cmd = python_command
                
                # Create MCP client configuration
                mcp_config = {
                    "edgartools": {
                        "command": python_cmd,
                        "args": ["-m", "edgar.ai"],
                        "transport": "stdio",
                    }
                }
                
                # Add identity to environment if provided
                if identity:
                    mcp_config["edgartools"]["env"] = {
                        "EDGAR_IDENTITY": identity
                    }
                
                # Initialize MCP client
                self.mcp_client = MultiServerMCPClient(mcp_config)
                
                import asyncio
                asyncio.run(self._ensure_tools_loaded())
                
                if self.logger:
                    self.logger.info(f"Initialized MCP client for edgartools (python: {python_cmd})")
                
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to initialize MCP client: {e}. Falling back to direct edgartools.")
                use_mcp = False
                self.use_mcp = False
        
        # Fallback to direct edgartools if MCP not available
        if not use_mcp:
            if not EDGARTOOLS_AVAILABLE:
                raise ImportError(
                    "edgartools is required. Install with: pip install 'edgartools[ai]'"
                )
            
            if identity:
                try:
                    set_identity(identity)
                    if self.logger:
                        self.logger.info(f"Set SEC identity: {identity}")
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to set SEC identity: {e}")
        
        # LangChain tools will be created lazily
        self._langchain_tools = None
        
        if self.logger:
            mode = "MCP" if self.use_mcp else "direct edgartools"
            self.logger.info(f"Initialized SECDataTools (mode: {mode})")
    
    async def _ensure_tools_loaded(self):
        """Ensure MCP tools are loaded."""
        if not self.use_mcp or not self.mcp_client:
            return
        
        if not self._tools_loaded:
            try:
                tools = await self.mcp_client.get_tools()
                # Convert tools to a dictionary for easy lookup
                for tool in tools:
                    self.mcp_tools[tool.name] = tool
                self._tools_loaded = True
                if self.logger:
                    self.logger.info(f"Loaded {len(self.mcp_tools)} MCP tools: {list(self.mcp_tools.keys())}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to load MCP tools: {e}")
                raise
    
    def get_langchain_tools(self) -> List[Any]:
        """
        Get LangChain tools for use with agents.
        
        Returns:
            List of LangChain StructuredTool instances
        """
        if self._langchain_tools is not None:
            return self._langchain_tools
        
        if not LANGCHAIN_TOOLS_AVAILABLE:
            raise ImportError(
                "LangChain tools not available. Install with: pip install langchain-core"
            )
        
        tools = []
        
        if self.use_mcp and self.mcp_client and self.mcp_tools:
            mcp_tool_list = list(self.mcp_tools.keys())
            for tool_name in mcp_tool_list:
                if tool_name.startswith('edgar_'):
                    tool: StructuredTool = self.mcp_tools[tool_name]
                    tool.name = tool_name[len('edgar_'):]
                    self.mcp_tools.pop(tool_name)
                    self.mcp_tools[tool.name] = tool
                    
            tools.extend(self.mcp_tools.values())
        
        self._langchain_tools = tools
        if self.logger:
            self.logger.info(f"Created {len(tools)} LangChain tools")
        
        return tools
    
    async def close(self):
        """Close MCP client connections."""
        if self.mcp_client:
            try:
                if hasattr(self.mcp_client, 'close'):
                    await self.mcp_client.close()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Error closing MCP client: {e}")

