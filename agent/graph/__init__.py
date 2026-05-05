"""LangGraph supervisor + worker graph package (W2 Stage-3b).

Public surface:
  build_supervisor_graph()  — returns a CompiledStateGraph
  SupervisorState           — TypedDict for graph state

W2_ARCHITECTURE.md §3 (Multi-Agent Graph).
"""

from agent.graph.builder import build_supervisor_graph
from agent.graph.state import SupervisorState

__all__ = ["build_supervisor_graph", "SupervisorState"]
