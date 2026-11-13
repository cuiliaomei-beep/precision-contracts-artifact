"""
Graph partitioning strategies for distributed analysis.
Implements: Component-based, Random, SCC-aware, Communication-aware.
"""
import random
from typing import List, Dict, Set, Callable
from collections import defaultdict, deque

from ..analysis.projection import AnalysisGraph, AnalysisNode


class PartitionStrategy:
    """Base class for partitioning strategies"""

    def partition(self, graph: AnalysisGraph,
                  num_workers: int) -> List[List[AnalysisNode]]:
        """
        Partition the analysis graph into num_workers partitions.

        Returns:
            List of partitions, each partition is a list of nodes.
        """
        raise NotImplementedError


class SingleWorkerPartition(PartitionStrategy):
    """Centralized: all nodes to one worker"""

    def partition(self, graph: AnalysisGraph,
                  num_workers: int) -> List[List[AnalysisNode]]:
        partitions = [[] for _ in range(num_workers)]
        all_nodes = list(graph.nodes.values())

        # Put all nodes in first worker
        partitions[0] = all_nodes
        return partitions


class ComponentBasedPartition(PartitionStrategy):
    """
    Partition by component: each component (service) goes to one worker.
    Simulates "one service per worker" intuition.
    """

    def partition(self, graph: AnalysisGraph,
                  num_workers: int) -> List[List[AnalysisNode]]:
        # Group nodes by component
        component_nodes = defaultdict(list)
        for node in graph.nodes.values():
            component = node.event.component
            component_nodes[component].append(node)

        partitions = [[] for _ in range(num_workers)]

        # Assign components to workers round-robin
        components = sorted(component_nodes.keys())
        for i, component in enumerate(components):
            worker_idx = i % num_workers
            partitions[worker_idx].extend(component_nodes[component])

        return partitions


class RandomBalancedPartition(PartitionStrategy):
    """
    Random balanced partition: randomly distribute nodes to balance load.
    Used as a baseline to show that load balancing alone is insufficient.
    """

    def partition(self, graph: AnalysisGraph,
                  num_workers: int, seed: int = 42) -> List[List[AnalysisNode]]:
        random.seed(seed)

        partitions = [[] for _ in range(num_workers)]
        nodes = list(graph.nodes.values())
        random.shuffle(nodes)

        # Distribute nodes round-robin
        for i, node in enumerate(nodes):
            worker_idx = i % num_workers
            partitions[worker_idx].append(node)

        return partitions


class SCCAwarePartition(PartitionStrategy):
    """
    SCC (Strongly Connected Component) aware partition.
    Tries to keep nodes in the same SCC on the same worker.
    Represents program-structure-aware partitioning.
    """

    def partition(self, graph: AnalysisGraph,
                  num_workers: int) -> List[List[AnalysisNode]]:
        # Compute SCCs using Tarjan's algorithm
        sccs = self._compute_sccs(graph)

        partitions = [[] for _ in range(num_workers)]

        # Assign SCCs to workers by size (greedy)
        # Sort by size descending
        sccs_sorted = sorted(sccs, key=len, reverse=True)

        # Track load per worker
        worker_load = [0] * num_workers

        for scc in sccs_sorted:
            # Assign SCC to least loaded worker
            min_idx = worker_load.index(min(worker_load))
            for node_id in scc:
                if node_id in graph.nodes:
                    partitions[min_idx].append(graph.nodes[node_id])
            worker_load[min_idx] += len(scc)

        return partitions

    def _compute_sccs(self, graph: AnalysisGraph) -> List[List[str]]:
        """Compute strongly connected components using Tarjan's algorithm"""
        nodes = list(graph.nodes.keys())
        index_map = {node_id: i for i, node_id in enumerate(nodes)}

        index = 0
        stack = []
        indices = {}
        lowlinks = {}
        on_stack = set()
        sccs = []

        def strongconnect(node_id):
            nonlocal index
            indices[node_id] = index
            lowlinks[node_id] = index
            index += 1
            stack.append(node_id)
            on_stack.add(node_id)

            # Visit successors
            node = graph.nodes[node_id]
            for succ in graph.get_dependents(node):
                succ_id = succ.id
                if succ_id not in indices:
                    strongconnect(succ_id)
                    lowlinks[node_id] = min(lowlinks[node_id], lowlinks[succ_id])
                elif succ_id in on_stack:
                    lowlinks[node_id] = min(lowlinks[node_id], indices[succ_id])

            if lowlinks[node_id] == indices[node_id]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == node_id:
                        break
                sccs.append(scc)

        for node_id in nodes:
            if node_id not in indices:
                strongconnect(node_id)

        return sccs


class CommunicationAwarePartition(PartitionStrategy):
    """
    Communication-aware partition (the proposed method).
    Partitions based on analysis dependency graph to minimize inter-worker edges.
    Optimizes: min α·EdgeCut + β·LoadImbalance
    """

    def partition(self, graph: AnalysisGraph,
                  num_workers: int,
                  alpha: float = 1.0,
                  beta: float = 0.1) -> List[List[AnalysisNode]]:
        """
        Args:
            graph: Analysis dependency graph
            num_workers: Number of partitions
            alpha: Weight for edge cut (communication cost)
            beta: Weight for load imbalance
        """
        if num_workers == 1:
            return [[node for node in graph.nodes.values()]]

        # Start with component-based as initial partition
        partitions = self._initial_partition(graph, num_workers)

        # Iterative refinement using local search
        for iteration in range(10):
            improved = False

            for node in graph.nodes.values():
                # Find best partition for this node
                best_partition = None
                best_cost = self._compute_cost(graph, partitions, alpha, beta)

                for p_idx in range(num_workers):
                    # Find current partition
                    curr_partition = None
                    for i, partition in enumerate(partitions):
                        if node in partition:
                            curr_partition = i
                            break

                    if p_idx == curr_partition:
                        continue

                    # Try moving node to partition p_idx
                    partitions[curr_partition].remove(node)
                    partitions[p_idx].append(node)

                    new_cost = self._compute_cost(graph, partitions, alpha, beta)
                    if new_cost < best_cost:
                        best_cost = new_cost
                        best_partition = p_idx
                        improved = True
                    else:
                        # Revert
                        partitions[p_idx].remove(node)
                        partitions[curr_partition].append(node)

            if not improved:
                break

        return partitions

    def _initial_partition(self, graph: AnalysisGraph,
                          num_workers: int) -> List[List[AnalysisNode]]:
        """Initial partitioning by component"""
        strategy = ComponentBasedPartition()
        return strategy.partition(graph, num_workers)

    def _compute_cost(self, graph: AnalysisGraph,
                     partitions: List[List[AnalysisNode]],
                     alpha: float, beta: float) -> float:
        """Compute cost of a partitioning"""
        # Edge cut: edges crossing partitions
        partition_map = {}
        for p_idx, partition in enumerate(partitions):
            for node in partition:
                partition_map[node.id] = p_idx

        edge_cut = 0
        for edge in graph.edges:
            if (edge.src.id in partition_map and edge.dst.id in partition_map):
                if partition_map[edge.src.id] != partition_map[edge.dst.id]:
                    edge_cut += 1

        # Load imbalance
        sizes = [len(p) for p in partitions]
        avg_size = sum(sizes) / len(sizes) if sizes else 1
        max_size = max(sizes) if sizes else 1
        imbalance = (max_size - avg_size) / avg_size if avg_size > 0 else 0

        cost = alpha * edge_cut + beta * imbalance
        return cost

    def __repr__(self):
        return "CommunicationAwarePartition(optimization-based)"


def create_partition_strategy(strategy_name: str) -> PartitionStrategy:
    """Factory function to create partition strategies"""
    strategies = {
        "single": SingleWorkerPartition,
        "component": ComponentBasedPartition,
        "random": RandomBalancedPartition,
        "scc": SCCAwarePartition,
        "communication": CommunicationAwarePartition,
    }

    strategy_class = strategies.get(strategy_name.lower())
    if strategy_class is None:
        raise ValueError(f"Unknown partition strategy: {strategy_name}")

    return strategy_class()
