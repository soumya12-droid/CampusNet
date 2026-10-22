import colorsys
import json
import re
import tomllib
from pathlib import Path

import pytest

from campusnet import theme
from campusnet.experiments import run_preset
from campusnet.metrics import calculate_metrics, throughput_over_time
from campusnet.presentation import queue_length_series
from campusnet.replay import PacketState
from campusnet.routing import shortest_path
from campusnet.topology import build_default_topology, disable_link
from campusnet.visualization import (
    delay_by_class_chart,
    empty_figure,
    outcomes_chart,
    queue_chart,
    throughput_chart,
    topology_figure,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def luminance(color: str) -> float:
    return theme.contrast_ratio(color, "#000000") / 21 - 0.05 / 21  # monotonic in relative luminance


# --- palette and Streamlit configuration ---


def test_streamlit_theme_matches_the_palette() -> None:
    t, side = CONFIG["theme"], CONFIG["theme"]["sidebar"]
    assert t["base"] == "dark"
    assert t["primaryColor"] == theme.ORANGE and t["backgroundColor"] == theme.BG_APP
    assert t["secondaryBackgroundColor"] == theme.PANEL and t["textColor"] == theme.TEXT and t["borderColor"] == theme.BORDER
    assert side["backgroundColor"] == theme.BG_SIDEBAR and side["secondaryBackgroundColor"] == theme.SURFACE_SIDEBAR
    assert side["textColor"] == theme.TEXT and side["borderColor"] == theme.BORDER
    assert t["codeTextColor"] == theme.TEXT and t["codeBackgroundColor"] == theme.ELEVATED  # no default green code text
    assert CONFIG["browser"]["gatherUsageStats"] is False


def test_surfaces_get_lighter_from_sidebar_to_elevated() -> None:
    order = [theme.BG_SIDEBAR, theme.SURFACE_SIDEBAR, theme.BG_APP, theme.PANEL, theme.ELEVATED]
    levels = [luminance(c) for c in order]
    assert levels == sorted(levels) and len(set(levels)) == len(levels)
    assert theme.contrast_ratio(theme.BORDER, theme.BG_APP) < 2  # borders stay subtle, not bright lines


def test_the_specified_palette_values() -> None:
    assert (theme.BG_APP, theme.BG_SIDEBAR, theme.SURFACE_SIDEBAR, theme.PANEL, theme.ELEVATED, theme.BORDER) == (
        "#111315", "#090A0B", "#101214", "#191C1F", "#202428", "#30353A")
    assert (theme.TEXT, theme.TEXT_MUTED, theme.ORANGE, theme.AMBER, theme.GREEN, theme.RED, theme.LINK) == (
        "#ECE9E2", "#A3A7AA", "#F29A38", "#F2C14E", "#83B889", "#D96C61", "#777F87")


SURFACES = [theme.BG_SIDEBAR, theme.SURFACE_SIDEBAR, theme.BG_APP, theme.PANEL, theme.ELEVATED]


@pytest.mark.parametrize("surface", SURFACES)
def test_text_is_readable_on_every_surface(surface: str) -> None:
    assert theme.contrast_ratio(theme.TEXT, surface) >= 10
    assert theme.contrast_ratio(theme.TEXT_MUTED, surface) >= 6  # muted text stays well above the 4.5 minimum


@pytest.mark.parametrize("surface", [theme.BG_APP, theme.PANEL, theme.ELEVATED])
@pytest.mark.parametrize("color", [theme.ORANGE, theme.AMBER, theme.GREEN, theme.RED])
def test_accent_and_status_colors_are_readable_as_text(color: str, surface: str) -> None:
    assert theme.contrast_ratio(color, surface) >= 4.5  # WCAG AA for normal text


def test_dark_text_on_the_primary_button_is_readable() -> None:
    for fill in (theme.ORANGE, theme.AMBER):  # normal and hover fills
        assert theme.contrast_ratio(theme.TEXT_ON_ACCENT, fill) >= 8


def test_links_and_graphics_are_visible_but_restrained() -> None:
    assert theme.contrast_ratio(theme.LINK, theme.BG_APP) >= 3  # a graphical object, not text
    assert theme.contrast_ratio(theme.LINK, theme.BG_APP) < theme.contrast_ratio(theme.ORANGE, theme.BG_APP)  # route outshines it


@pytest.mark.parametrize("color", [theme.ORANGE, theme.AMBER, theme.GREEN, theme.RED, theme.SWITCH, *theme.SERIES])
def test_no_neon_colors(color: str) -> None:
    h, s, v = colorsys.rgb_to_hsv(*(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    assert not (s > 0.8 and v > 0.85)  # strongly saturated and bright together is what reads as neon
    assert v <= 0.96 and color.upper() not in ("#FFFFFF", "#00FFFF", "#FF00FF", "#00FF00")


def test_orange_and_amber_are_the_identity_green_and_red_are_semantic() -> None:
    assert theme.SERIES[0] == theme.ORANGE and theme.SERIES[1] == theme.AMBER
    assert theme.GREEN not in theme.SERIES and theme.RED not in theme.SERIES  # compared runs never look like success or failure
    assert theme.SERVER == theme.GREEN


# --- CSS ---


def test_css_is_filled_in_flat_and_free_of_effects() -> None:
    css = theme.css()
    assert "@" not in css  # every token replaced
    for banned in ("gradient", "box-shadow", "text-shadow", "blur(", "backdrop", "@keyframes", "animation", "drop-shadow"):
        assert banned not in css, banned
    assert theme.ORANGE in css and theme.BG_APP not in css.replace(theme.PANEL, "")  # surfaces come from config.toml
    assert theme.TEXT_ON_ACCENT in css  # primary buttons use dark text
    radii = [int(r) for r in re.findall(r"border-radius:\s*(\d+)px", css)]
    assert radii and max(radii) <= 4  # restrained corners


def test_css_only_uses_palette_colors() -> None:
    css = theme.css()
    palette = {v.lower() for k, v in vars(theme).items() if isinstance(v, str) and HEX.fullmatch(v)}
    assert {c.lower() for c in HEX.findall(css)} <= palette
    assert not re.search(r"#[0-9a-fA-F]{3}\b(?!\w)", css)  # no shorthand colours either


def test_orange_is_limited_to_the_title_and_interaction_states() -> None:
    css = theme.css()
    orange_rules = [rule for rule in css.split("}") if theme.ORANGE in rule or theme.rgba(theme.ORANGE, 0.14) in rule]
    selectors = " ".join(rule.split("{")[0] for rule in orange_rules)
    assert ".cn-title" in selectors and "primary" in selectors and "segmented_control" in selectors
    assert ".cn-section" not in selectors and "h1" not in selectors  # headings are not all orange


def test_status_buttons_use_semantic_colors() -> None:
    css = theme.css()
    assert re.search(r"\.st-key-jump_Failures button\s*\{[^}]*color:\s*" + theme.RED, css)
    assert re.search(r"\.st-key-jump_Deliveries button\s*\{[^}]*color:\s*" + theme.GREEN, css)


# --- no hard-coded colours outside the theme ---


@pytest.mark.parametrize("name", ["app.py", "campusnet/visualization.py", "campusnet/presentation.py", "campusnet/insights.py"])
def test_ui_code_has_no_hard_coded_colors(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")
    assert not HEX.findall(source), f"{name} hard-codes a colour; use campusnet/theme.py"
    assert "plotly_white" not in source and not re.search(r"""["'](white|black)["']""", source)


# --- charts ---


def plot_json(fig) -> str:
    payload = fig.to_plotly_json()
    payload["layout"] = {k: v for k, v in payload["layout"].items() if k != "template"}
    return json.dumps(payload, default=str)


@pytest.fixture(scope="module")
def preset_b():
    return run_preset("B", duration_s=1.0)


@pytest.fixture(scope="module")
def charts(preset_b):
    runs = preset_b.runs
    return {
        "throughput": throughput_chart({r.label: throughput_over_time(r.result, 0.1) for r in runs}, 0.1, 1.0),
        "queue": queue_chart({r.label: queue_length_series(r.result, "r1-r2", "r1") for r in runs}, 1.0, "r1 to r2"),
        "delay": delay_by_class_chart({r.label: r.metrics for r in runs}),
        "outcomes": outcomes_chart({r.label: r.metrics for r in runs}),
        "empty": empty_figure("Nothing to show"),
        "topology": topology_figure(build_default_topology()),
    }


def test_every_chart_is_transparent_dark_styled(charts: dict) -> None:
    for name, fig in charts.items():
        assert fig.layout.paper_bgcolor == "rgba(0,0,0,0)" and fig.layout.plot_bgcolor == "rgba(0,0,0,0)", name
        assert fig.layout.font.color == theme.TEXT, name
        assert fig.layout.hoverlabel.bgcolor == theme.ELEVATED and fig.layout.hoverlabel.font.color == theme.TEXT, name
        assert fig.layout.legend.font.color == theme.TEXT, name


def test_no_light_backgrounds_or_white_remain_in_any_figure(charts: dict) -> None:
    for name, fig in charts.items():
        text = plot_json(fig).lower()
        assert "white" not in text and "#fff" not in text and "255,255,255" not in text.replace(" ", ""), name


def test_chart_axes_are_muted_with_readable_text(charts: dict) -> None:
    for name in ("throughput", "queue", "delay", "outcomes"):
        fig = charts[name]
        for axis in (fig.layout.xaxis, fig.layout.yaxis):
            assert axis.gridcolor == theme.BORDER and axis.tickfont.color == theme.TEXT_MUTED and axis.title.font.color == theme.TEXT
        assert fig.layout.xaxis.title.text and fig.layout.yaxis.title.text or name == "outcomes"  # axes keep their labels


def test_compared_runs_use_the_series_colors_consistently(charts: dict) -> None:
    for name in ("throughput", "queue"):
        assert [t.line.color for t in charts[name].data] == theme.SERIES[:2]
    assert [t.marker.color for t in charts["delay"].data] == theme.SERIES[:2]  # the same run has the same colour everywhere


def test_line_series_differ_by_style_as_well_as_colour(charts: dict) -> None:
    for name in ("throughput", "queue"):
        dashes = [t.line.dash for t in charts[name].data]
        assert len(set(dashes)) == len(dashes)


def test_outcomes_use_green_for_delivery_and_red_for_drops(charts: dict) -> None:
    colors = {t.name: t.marker.color for t in charts["outcomes"].data}
    assert colors["Delivered"] == theme.GREEN and colors["Unresolved at end"] == theme.UNRESOLVED
    assert colors["Dropped: queue_full"] == theme.RED


def test_drop_reasons_stay_in_the_red_family_and_are_distinct() -> None:
    colors = list(theme.DROP_REASON_COLORS.values())
    assert len(set(colors)) == len(colors)
    for c in colors:
        r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        assert r > g and r > b  # red dominates


# --- topology semantics ---


def trace(fig, name):
    return next(t for t in fig.data if t.name == name)


def test_topology_colors_follow_the_theme() -> None:
    topology = build_default_topology()
    path = shortest_path(topology, "stu1", "portal")
    disable_link(topology, "sw2-r2")
    fig = topology_figure(topology, routes=[("Route in use", path)])
    assert trace(fig, "Link up").line.color == theme.LINK
    assert trace(fig, "Link failed").line.color == theme.RED and trace(fig, "Link failed").line.dash == "dash"
    assert trace(fig, "Route in use").line.color == theme.ORANGE
    assert trace(fig, "Server").marker.color == theme.GREEN and trace(fig, "Switch").marker.color == theme.SWITCH
    assert trace(fig, "Client").marker.color == theme.CLIENT and trace(fig, "Router").marker.color == theme.ROUTER
    symbols = {n: trace(fig, n).marker.symbol for n in ("Client", "Switch", "Router", "Server")}
    assert len(set(symbols.values())) == 4  # shape still tells the types apart without colour
    assert not any("white" in str(a.bgcolor).lower() for a in fig.layout.annotations)


def test_failed_router_is_hollow_and_red() -> None:
    topology = build_default_topology()
    from campusnet.topology import disable_router

    disable_router(topology, "r1")
    marker = trace(topology_figure(topology), "Router (failed)").marker
    assert marker.color == theme.BG_APP and marker.line.color == theme.RED


def test_queue_fill_is_amber_and_route_is_the_only_orange() -> None:
    result = run_preset("A", duration_s=1.0).runs[1].result
    from campusnet.replay import ReplayIndex

    replay = ReplayIndex(result)
    snap = next(replay.snapshot(i) for i in range(0, len(replay), 25)
                if any(p.capacity and len(p.waiting) / p.capacity >= 0.9 for p in replay.snapshot(i).ports.values()))
    fig = topology_figure(result.topology, snapshot=snap)
    assert trace(fig, "Queue at least 90% full").line.color == theme.AMBER
    assert theme.ORANGE not in {t.line.color for t in fig.data if t.mode == "lines"}  # no route drawn: nothing orange


def test_highlights_and_packet_markers() -> None:
    topology = build_default_topology()
    fig = topology_figure(topology, highlights=[("link", "r1-r2", "Selected event"), ("node", "r1", "Inspected item")],
                          followed=(1, PacketState("dropped", "r1-r2", "r1", "r1", 0.0)))
    assert trace(fig, "Selected event").marker.line.color == theme.AMBER and trace(fig, "Inspected item").marker.line.color == theme.TEXT
    assert trace(fig, "Dropped packet 1").marker.color == theme.RED and trace(fig, "Dropped packet 1").marker.symbol == "x"
    delivered = topology_figure(topology, followed=(2, PacketState("delivered", node="portal", since_s=0.0)))
    assert trace(delivered, "Followed packet 2").marker.color == theme.GREEN
    moving = topology_figure(topology, followed=(3, PacketState("waiting", "r1-r2", "r1", "r1", 0.0)))
    assert trace(moving, "Followed packet 3").marker.color == theme.ORANGE
