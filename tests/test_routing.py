import itertools
import random

import networkx as nx
import pytest

from campusnet.routing import shortest_path
from campusnet.topology import build_default_topology
from helpers import make_topology


def test_returns_expected_lowest_cost_path() -> None:
    # a-b-d costs 2+2=4, a-c-d costs 1+1=2, direct a-d costs 10
    topology = make_topology([("a", "b", 2), ("b", "d", 2), ("a", "c", 1), ("c", "d", 1), ("a", "d", 10)])
    assert shortest_path(topology, "a", "d") == ["a", "c", "d"]


def test_path_is_symmetric_in_cost() -> None:
    topology = make_topology([("a", "b", 1), ("b", "c", 1)])
    assert shortest_path(topology, "c", "a") == ["c", "b", "a"]


def test_cost_not_hop_count_decides() -> None:
    topology = make_topology([("a", "d", 10), ("a", "b", 1), ("b", "c", 1), ("c", "d", 1)])
    assert shortest_path(topology, "a", "d") == ["a", "b", "c", "d"]


def test_equal_cost_paths_use_lexicographic_tie_break() -> None:
    topology = make_topology([("s", "x", 1), ("x", "t", 1), ("s", "m", 1), ("m", "t", 1)])
    assert shortest_path(topology, "s", "t") == ["s", "m", "t"]
    assert shortest_path(topology, "t", "s") == ["t", "m", "s"]


def test_tie_break_is_stable_across_calls() -> None:
    topology = make_topology([("s", "x", 1), ("x", "t", 1), ("s", "m", 1), ("m", "t", 1)])
    assert len({tuple(shortest_path(topology, "s", "t") or []) for _ in range(20)}) == 1


def test_unreachable_destination_returns_none() -> None:
    topology = make_topology([("a", "b", 1), ("c", "d", 1)])
    assert shortest_path(topology, "a", "d") is None


def test_source_equals_destination() -> None:
    topology = make_topology([("a", "b", 1)])
    assert shortest_path(topology, "a", "a") == ["a"]


def test_unknown_node_raises() -> None:
    topology = make_topology([("a", "b", 1)])
    with pytest.raises(ValueError):
        shortest_path(topology, "a", "zzz")
    with pytest.raises(ValueError):
        shortest_path(topology, "zzz", "a")


def test_zero_cost_dead_end_is_skipped() -> None:
    # s-a costs 0 but a is a dead end; the cheapest route to d is s-b-d (cost 2).
    topology = make_topology([("s", "a", 0), ("s", "b", 1), ("b", "d", 1)])
    assert shortest_path(topology, "s", "d") == ["s", "b", "d"]


def test_zero_cost_pocket_with_smaller_ids_does_not_trap_the_walk() -> None:
    # From a, the smaller neighbor b is a zero-cost dead end; c leads to d.
    topology = make_topology([("s", "a", 0), ("a", "b", 0), ("a", "c", 0), ("c", "d", 1)])
    assert shortest_path(topology, "s", "d") == ["s", "a", "c", "d"]


def test_zero_cost_cycle_does_not_cause_revisits() -> None:
    # s-a-b is a zero-cost triangle; every route to d costs 1. Simple minimum-cost
    # paths: s-a-b-d, s-a-d, s-b-d. The lexicographically smallest is s-a-b-d.
    topology = make_topology([("s", "a", 0), ("a", "b", 0), ("s", "b", 0), ("b", "d", 1), ("a", "d", 1)])
    assert shortest_path(topology, "s", "d") == ["s", "a", "b", "d"]
    assert shortest_path(topology, "d", "s") == ["d", "a", "b", "s"]


def test_all_zero_cost_graph_returns_lexicographically_smallest_simple_path() -> None:
    topology = make_topology([("a", "b", 0), ("b", "c", 0), ("a", "c", 0), ("c", "d", 0), ("b", "d", 0)])
    assert shortest_path(topology, "a", "d") == ["a", "b", "c", "d"]


def _brute_force_path(edges: list[tuple[str, str, float]], source: str, destination: str) -> list[str] | None:
    graph = nx.Graph()
    graph.add_nodes_from(n for a, b, _ in edges for n in (a, b))
    for a, b, cost in edges:
        graph.add_edge(a, b, cost=cost)
    if not nx.has_path(graph, source, destination):
        return None
    paths = [[source]] if source == destination else list(nx.all_simple_paths(graph, source, destination))
    cost_of = lambda p: sum(graph[u][v]["cost"] for u, v in zip(p, p[1:]))  # noqa: E731
    best = min(cost_of(p) for p in paths)
    return min(p for p in paths if cost_of(p) == best)


def test_matches_brute_force_on_random_graphs_with_zero_costs() -> None:
    rng = random.Random(2024)
    node_ids = ["a", "b", "c", "d", "e", "f", "g"]
    for _ in range(150):
        edges = [
            (u, v, rng.choice([0, 0, 1, 2]))
            for u, v in itertools.combinations(node_ids, 2)
            if rng.random() < 0.4
        ]
        if not edges:
            continue
        topology = make_topology(edges)
        present = sorted(topology.nodes)
        for source, destination in itertools.permutations(present, 2):
            assert shortest_path(topology, source, destination) == _brute_force_path(edges, source, destination), (
                edges,
                source,
                destination,
            )


def test_routing_does_not_modify_the_topology() -> None:
    topology = build_default_topology()
    before = {link_id: link.is_up for link_id, link in topology.links.items()}
    shortest_path(topology, "stu1", "portal")
    assert {link_id: link.is_up for link_id, link in topology.links.items()} == before
    assert topology.failed_routers == set()


def test_default_topology_primary_routes() -> None:
    topology = build_default_topology()
    assert shortest_path(topology, "stu1", "portal") == ["stu1", "sw1", "r1", "r2", "portal"]
    assert shortest_path(topology, "classroom", "files") == ["classroom", "sw2", "r1", "r2", "files"]
    assert shortest_path(topology, "stu1", "stu3") == ["stu1", "sw1", "r1", "sw2", "stu3"]
