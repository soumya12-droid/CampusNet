"""Shortest-path routing on routing_cost (Dijkstra via NetworkX).

This is a shortest-path calculation, not an implementation of a routing protocol.
"""

import math

import networkx as nx

from campusnet.topology import Topology


def _active_graph(topology: Topology) -> nx.Graph:
    """Graph of working nodes and up links; failed routers and down links are excluded."""
    graph = nx.Graph()
    graph.add_nodes_from(node_id for node_id in topology.nodes if node_id not in topology.failed_routers)
    for link in topology.links.values():
        if link.is_up and link.source in graph and link.target in graph:
            graph.add_edge(link.source, link.target, routing_cost=link.routing_cost)
    return graph


def shortest_path(topology: Topology, source_id: str, destination_id: str) -> list[str] | None:
    """Lowest-cost node path from source to destination, or None if unreachable.

    The result is a simple path (no repeated node) of minimum total routing_cost,
    for any non-negative costs including zero. Among such paths, the one whose
    node-ID sequence is lexicographically smallest is returned, so results are
    deterministic. At each step the walk takes the smallest next node from which
    the destination is still reachable over minimum-cost edges without revisiting
    a node, so it never gets stuck or backtracks. A source equal to the destination
    returns [source]. Unknown node IDs raise ValueError. The topology is not modified.
    """
    for node_id in (source_id, destination_id):
        if node_id not in topology.nodes:
            raise ValueError(f"unknown node {node_id!r}")

    graph = _active_graph(topology)
    if source_id not in graph or destination_id not in graph:
        return None
    if source_id == destination_id:
        return [source_id]

    distance = nx.single_source_dijkstra_path_length(graph, destination_id, weight="routing_cost")
    if source_id not in distance:
        return None

    path = [source_id]
    visited = {source_id}
    current = source_id
    while current != destination_id:
        for candidate in _tight_neighbors(graph, distance, current):
            if candidate in visited:
                continue
            if candidate == destination_id or _can_reach(graph, distance, candidate, destination_id, visited):
                current = candidate
                break
        else:
            raise RuntimeError("no continuation found; this violates the routing invariant")
        visited.add(current)
        path.append(current)
    return path


def _tight_neighbors(graph: nx.Graph, distance: dict[str, float], node: str) -> list[str]:
    """Neighbors reached by an edge that lies on some minimum-cost path to the destination, sorted by ID."""
    return sorted(
        neighbor
        for neighbor, edge in graph[node].items()
        if math.isclose(distance[node], edge["routing_cost"] + distance[neighbor], rel_tol=1e-9, abs_tol=1e-12)
    )


def _can_reach(graph: nx.Graph, distance: dict[str, float], start: str, goal: str, blocked: set[str]) -> bool:
    """True if goal is reachable from start over minimum-cost edges without entering blocked nodes."""
    seen = {start}
    frontier = [start]
    while frontier:
        node = frontier.pop()
        for neighbor in _tight_neighbors(graph, distance, node):
            if neighbor == goal:
                return True
            if neighbor not in seen and neighbor not in blocked:
                seen.add(neighbor)
                frontier.append(neighbor)
    return False
