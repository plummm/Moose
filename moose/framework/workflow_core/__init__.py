"""Workflow Core - LangGraph integration utilities for Moose agents."""

from typing import TypedDict, Dict, Any, Optional, Callable, List
from abc import ABC, abstractmethod

try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    END = None


class WorkflowMixin(ABC):
    """
    Mixin class that agents can inherit to add LangGraph workflow support.
    
    Provides helper methods for creating and running workflows.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize workflow mixin."""
        super().__init__(*args, **kwargs)
        self.workflow_app: Optional[Any] = None
    
    def create_workflow(
        self,
        state_class: type,
        nodes: Dict[str, Callable],
        entry_point: str = "start",
        edges: Optional[List[tuple]] = None
    ) -> Any:
        """
        Create a LangGraph workflow.
        
        Args:
            state_class: TypedDict class defining the state structure
            nodes: Dictionary mapping node names to node functions
            entry_point: Name of the entry point node
            edges: List of (from_node, to_node) tuples. If None, creates linear chain.
            
        Returns:
            Compiled LangGraph app
        """
        if not LANGGRAPH_AVAILABLE:
            raise ImportError(
                "LangGraph is required. Install with: pip install langgraph"
            )
        
        workflow = StateGraph(state_class)
        
        # Add nodes
        for node_name, node_func in nodes.items():
            workflow.add_node(node_name, node_func)
        
        # Set entry point
        workflow.set_entry_point(entry_point)
        
        # Add edges
        if edges:
            for from_node, to_node in edges:
                if to_node == "END":
                    workflow.add_edge(from_node, END)
                else:
                    workflow.add_edge(from_node, to_node)
        else:
            # Create linear chain if no edges specified
            node_names = list(nodes.keys())
            for i in range(len(node_names) - 1):
                workflow.add_edge(node_names[i], node_names[i + 1])
            # Last node goes to END
            workflow.add_edge(node_names[-1], END)
        
        # Compile and return
        return workflow.compile()
    
    def run_workflow(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run the workflow with initial state.
        
        Args:
            initial_state: Initial state dictionary
            
        Returns:
            Final state after workflow execution
        """
        if self.workflow_app is None:
            raise ValueError("Workflow not initialized. Call create_workflow() first.")
        
        result = self.workflow_app.invoke(initial_state)
        return result


def create_workflow_node(func: Callable) -> Callable:
    """
    Decorator to create a workflow node from a function.
    
    The function should accept a state dictionary and return an updated state dictionary.
    
    Args:
        func: Function to convert to a workflow node
        
    Returns:
        Workflow node function
    """
    def node_wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
        """Wrapper that ensures state is properly updated."""
        result = func(state)
        # If result is a dict, merge it into state
        if isinstance(result, dict):
            state.update(result)
            return state
        return state
    
    return node_wrapper


def create_conditional_edge(
    condition_func: Callable[[Dict[str, Any]], str]
) -> Callable:
    """
    Create a conditional edge function for LangGraph.
    
    Args:
        condition_func: Function that takes state and returns next node name
        
    Returns:
        Conditional edge function
    """
    def conditional_edge(state: Dict[str, Any]) -> str:
        """Conditional edge function."""
        return condition_func(state)
    
    return conditional_edge


__all__ = [
    'WorkflowMixin',
    'create_workflow_node',
    'create_conditional_edge',
    'LANGGRAPH_AVAILABLE',
]

