import pytest

from campusnet.routing import shortest_path
from campusnet.topology import build_default_topology, disable_link, disable_router
from helpers import make_topology


def test_disable_link_marks_it_down() -> None:
    topology = build_default_topology()
    disable_link(topology, "r1-r2")
    assert not topology.links["r1-r2"].is_up
    assert topology.links["sw1-r1"].is_up


def test_disable_unknown_link_raises() -> None:
    with pytest.raises(ValueError):
        disable_link(build_default_topology(), "nope")


def test_failed_link_is_excluded_from_routing() -> None:
    topology = make_topology([("a", "b", 1), ("b", "c", 1), ("a", "c", 5)])
    assert shortest_path(topology, "a", "c") == ["a", "b", "c"]
    disable_link(topology, "a-b")
    assert shortest_path(topology, "a", "c") == ["a", "c"]


def test_failed_link_is_not_just_given_a_high_cost() -> None:
    topology = make_topology([("a", "b", 1)])
    disable_link(topology, "a-b")
    assert shortest_path(topology, "a", "b") is None


def test_redundant_route_is_used_after_core_link_failure() -> None:
    topology = build_default_topology()
    assert shortest_path(topology, "stu1", "portal") == ["stu1", "sw1", "r1", "r2", "portal"]
    disable_link(topology, "r1-r2")
    assert shortest_path(topology, "stu1", "portal") == ["stu1", "sw1", "r2", "portal"]
    assert shortest_path(topology, "stu3", "files") == ["stu3", "sw2", "r2", "files"]


def test_destination_unreachable_when_all_routes_are_removed() -> None:
    topology = build_default_topology()
    disable_link(topology, "r1-r2")
    disable_link(topology, "sw1-r2")
    # Still reachable by a longer detour: sw1 -> r1 -> sw2 -> r2.
    assert shortest_path(topology, "stu1", "portal") == ["stu1", "sw1", "r1", "sw2", "r2", "portal"]
    disable_link(topology, "sw2-r2")
    assert shortest_path(topology, "stu1", "portal") is None
    assert shortest_path(topology, "stu1", "stu3") is not None  # the access side is still connected


def test_bridge_link_failure_disconnects_only_that_server() -> None:
    topology = build_default_topology()
    disable_link(topology, "r2-portal")
    assert shortest_path(topology, "stu1", "portal") is None
    assert shortest_path(topology, "stu1", "files") == ["stu1", "sw1", "r1", "r2", "files"]


def test_disable_router_takes_attached_links_down_and_reroutes() -> None:
    topology = build_default_topology()
    disable_router(topology, "r1")
    assert "r1" in topology.failed_routers
    assert all(not link.is_up for link in topology.links_of("r1"))
    assert shortest_path(topology, "stu1", "portal") == ["stu1", "sw1", "r2", "portal"]
    assert shortest_path(topology, "stu1", "r1") is None
    assert shortest_path(topology, "r1", "stu1") is None


def test_failing_the_router_that_serves_the_servers_isolates_them() -> None:
    topology = build_default_topology()
    disable_router(topology, "r2")
    assert shortest_path(topology, "stu1", "portal") is None
    assert shortest_path(topology, "stu1", "stu3") == ["stu1", "sw1", "r1", "sw2", "stu3"]


def test_disable_router_rejects_non_routers_and_unknown_nodes() -> None:
    topology = build_default_topology()
    with pytest.raises(ValueError):
        disable_router(topology, "sw1")
    with pytest.raises(ValueError):
        disable_router(topology, "nope")
    assert topology.failed_routers == set()


def test_failure_in_one_topology_does_not_affect_another() -> None:
    broken, healthy = build_default_topology(), build_default_topology()
    disable_link(broken, "r1-r2")
    assert shortest_path(healthy, "stu1", "portal") == ["stu1", "sw1", "r1", "r2", "portal"]
