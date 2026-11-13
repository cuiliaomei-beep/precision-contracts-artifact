"""
Distributed Program Analysis Framework

A complete implementation for verifying communication semantics in distributed programs.
Implements both the theoretical framework and practical analysis tools.
"""

__version__ = "0.1.0"

from .core.ir import Program, Event, ProgramState, EventType
from .oracle.exhaustive import ExhaustiveScheduler
from .analysis.domain import AbstractDomain, AbstractValue
from .analysis.centralized_solver import CentralizedSolver
from .analysis.projection import AnalysisGraph
from .analysis.semantic_graph import SemanticDependencyGraph
from .instantiations.rpc import UnaryRPCInstantiation
from .instantiations.rma import RMAInstantiation
from .generator.litmus_generator import LitmusGenerator
from .partition.strategies import create_partition_strategy
from .distributed.coordinator import DistributedCoordinator

__all__ = [
    "Program",
    "Event",
    "ProgramState",
    "EventType",
    "ExhaustiveScheduler",
    "AbstractDomain",
    "AbstractValue",
    "CentralizedSolver",
    "AnalysisGraph",
    "SemanticDependencyGraph",
    "UnaryRPCInstantiation",
    "RMAInstantiation",
    "LitmusGenerator",
    "create_partition_strategy",
    "DistributedCoordinator",
]
