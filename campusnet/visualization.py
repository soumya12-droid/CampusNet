"""Plotly figures for the dashboard: network topology and result charts.

Figures are built from existing results and metrics; nothing here simulates or recalculates a metric.
Colors are restrained and never the only cue: node types differ by marker shape and label,
routes and failures by line style as well as color, and charts label their categories directly.
"""

import networkx as nx
import plotly.graph_objects as go

from campusnet import theme
from campusnet.metrics import Metrics
from campusnet.topology import Topology

INK = theme.TEXT
MUTED = theme.TEXT_MUTED
GRID = theme.BORDER
ACCENT = theme.ORANGE
DANGER = theme.RED
RUN_COLORS = theme.SERIES
FONT = theme.FONT
LABEL_BACKING = theme.rgba(theme.BG_APP, 0.88)  # keeps labels readable where they cross a line

# Hand-placed layout for the default campus topology: clients | switches | routers | servers.
DEFAULT_POSITIONS: dict[str, tuple[float, float]] = {
    "stu1": (0.0, 5.0), "stu2": (0.0, 3.8), "classroom": (0.0, 2.0), "stu3": (0.0, 0.8), "stu4": (0.0, -0.4),
    "sw1": (1.9, 4.4), "sw2": (1.9, 0.8),
    "r1": (3.8, 2.6), "r2": (6.0, 2.6),
    "portal": (8.0, 3.6), "files": (8.0, 1.6),
}

# node_type -> (legend name, marker symbol, color, label (xanchor, yanchor), label offset in px)
_NODE_STYLE = {
    "student_device": ("Client", "circle", theme.CLIENT, ("right", "middle"), (-16, 0)),
    "switch": ("Switch", "diamond", theme.SWITCH, ("center", "bottom"), (0, 20)),
    "router": ("Router", "square", theme.ROUTER, ("center", "top"), (0, -20)),
    "server": ("Server", "hexagon", theme.SERVER, ("left", "middle"), (16, 0)),
}

_DROP_REASON_COLORS = theme.DROP_REASON_COLORS


def _positions(topology: Topology) -> dict[str, tuple[float, float]]:
    if set(topology.nodes) <= set(DEFAULT_POSITIONS):
        return {n: DEFAULT_POSITIONS[n] for n in topology.nodes}
    graph = nx.Graph([(l.source, l.target) for l in topology.links.values()])
    graph.add_nodes_from(topology.nodes)
    layout = nx.spring_layout(graph, seed=1)
    return {n: (float(x) * 4 + 4, float(y) * 2.5 + 2.5) for n, (x, y) in layout.items()}


def _style(fig: go.Figure, height: int, x_title: str | None = None, y_title: str | None = None) -> go.Figure:
    fig.update_layout(
        height=height, template="plotly_dark", font=dict(family=FONT, size=13, color=INK),
        margin=dict(l=56, r=16, t=28, b=48), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=12, color=INK)),
        hoverlabel=dict(font=dict(family=FONT, size=12, color=INK), bgcolor=theme.ELEVATED, bordercolor=theme.BORDER),
    )
    axis = dict(gridcolor=GRID, linecolor=GRID, zerolinecolor=GRID, tickfont=dict(color=MUTED), title_font=dict(color=INK))
    fig.update_xaxes(title_text=x_title, **axis)
    fig.update_yaxes(title_text=y_title, **axis)
    return fig


def empty_figure(message: str, height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font=dict(size=14, color=MUTED), x=0.5, y=0.5, xref="paper", yref="paper")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _style(fig, height)


# --- topology ---


def _segments(pairs: list[tuple[tuple[float, float], tuple[float, float]]]) -> tuple[list, list]:
    xs: list[float | None] = []
    ys: list[float | None] = []
    for (x0, y0), (x1, y1) in pairs:
        xs += [x0, x1, None]
        ys += [y0, y1, None]
    return xs, ys


def _along(p: tuple[float, float], q: tuple[float, float], fraction: float) -> tuple[float, float]:
    return p[0] + (q[0] - p[0]) * fraction, p[1] + (q[1] - p[1]) * fraction


# Where things sit along a directed link, as a fraction of its length from the sending node.
QUEUE_BADGE_AT = 0.24
SERIALIZING_AT = 0.10
PROPAGATION_START, PROPAGATION_END = 0.18, 0.92

LEFT_MARGIN = 1.6  # room for the longest client label ("classroom") left of the first column

FOCUS_REGIONS: dict[str, tuple[float, float] | None] = {  # name -> x range (None: the whole network)
    "Whole network": None,
    "Clients and access switches": (-1.6, 3.3),
    "Core routers": (1.3, 7.3),
    "Servers": (3.2, 9.0),
}

QUEUE_BUSY_COLOR = theme.AMBER_DIM
QUEUE_FULL_COLOR = theme.AMBER
HIGHLIGHT_COLORS = {"Selected event": theme.AMBER, "Inspected item": INK}


def link_position(state, pos: dict, links: dict) -> tuple[float, float] | None:
    """Where a replayed packet sits on the picture, from its reconstructed PacketState (None if off the network)."""
    kind = state.kind
    if kind in ("waiting", "transmitting", "propagating") and state.link_id and state.sender:
        link = links[state.link_id]
        src = pos[state.sender]
        dst = pos[link.target if state.sender == link.source else link.source]
        if kind == "waiting":
            return _along(src, dst, QUEUE_BADGE_AT)
        if kind == "transmitting":
            return _along(src, dst, SERIALIZING_AT)
        return _along(src, dst, PROPAGATION_START + (PROPAGATION_END - PROPAGATION_START) * (state.fraction or 0.0))
    if kind in ("at_source", "delivered") and state.node in pos:
        return pos[state.node]
    if kind == "dropped":
        if state.link_id and state.link_id in links:
            l = links[state.link_id]
            return _along(pos[l.source], pos[l.target], 0.5)
        if state.node in pos:
            return pos[state.node]
    return None


def topology_figure(
    topology: Topology,
    routes: list[tuple[str, list[str]]] | None = None,
    height: int = 540,
    *,
    snapshot=None,
    failed_links: set | None = None,
    failed_routers: set | None = None,
    focus: str = "Whole network",
    highlights: list[tuple[str, str, str]] | None = None,
    followed=None,
) -> go.Figure:
    """The network picture.

    topology      supplies devices, link properties and (by default) which links and routers are down.
    routes        (legend label, node path) pairs; the first is drawn thick with direction arrows, the second dotted.
    snapshot      a replay Snapshot: shows queue fill on links, queue sizes, and packets currently on links.
    failed_links / failed_routers   override the failure state (to show the network at a moment of a run).
    focus         one of FOCUS_REGIONS: crop to part of the network without changing the layout.
    highlights    (kind "node"|"link", id, label) rings, label in HIGHLIGHT_COLORS.
    followed      (packet id, PacketState) of a packet being followed: drawn as a distinct marker.
    Node markers and link midpoints carry customdata (the node or link id) so clicks can select them.
    """
    pos = _positions(topology)
    routes = routes or []
    links = list(topology.links.values())
    link_by_id = topology.links
    if failed_links is None:
        failed_links = {l.link_id for l in links if not l.is_up}
    if failed_routers is None:
        failed_routers = set(topology.failed_routers)
    route_edges = [{frozenset(pair) for pair in zip(path, path[1:])} for _, path in routes]
    on_route = set().union(*route_edges) if route_edges else set()
    fig = go.Figure()

    fill: dict[str, float] = {}  # worst queue fill of either direction, from the snapshot
    if snapshot is not None:
        for (link_id, _), port in snapshot.ports.items():
            if port.capacity > 0:
                fill[link_id] = max(fill.get(link_id, 0.0), len(port.waiting) / port.capacity)

    def line(name: str, chosen: list, **style: object) -> None:
        if chosen:
            xs, ys = _segments([(pos[l.source], pos[l.target]) for l in chosen])
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=name, hoverinfo="skip", line=style))  # type: ignore[arg-type]

    up = [l for l in links if l.link_id not in failed_links]
    plain = [l for l in up if frozenset((l.source, l.target)) not in on_route]
    line("Link up", [l for l in plain if fill.get(l.link_id, 0) < 0.5], color=theme.LINK, width=2)
    line("Queue at least 50% full", [l for l in plain if 0.5 <= fill.get(l.link_id, 0) < 0.9], color=QUEUE_BUSY_COLOR, width=4.5)
    line("Queue at least 90% full", [l for l in plain if fill.get(l.link_id, 0) >= 0.9], color=QUEUE_FULL_COLOR, width=5)
    line("Link failed", [l for l in links if l.link_id in failed_links], color=DANGER, width=2.5, dash="dash")
    for index, ((label, _), edges) in enumerate(zip(routes[:2], route_edges)):
        chosen = [l for l in up if frozenset((l.source, l.target)) in edges]
        line(label, chosen, color=RUN_COLORS[index] if index else ACCENT, width=6 if index == 0 else 3,
             **({} if index == 0 else {"dash": "dot"}))
    if routes:  # direction of travel along the primary route
        path = routes[0][1]
        for u, v in zip(path, path[1:]):
            if frozenset((u, v)) in {frozenset((l.source, l.target)) for l in up}:
                (x0, y0), (x1, y1) = _along(pos[u], pos[v], 0.62), _along(pos[u], pos[v], 0.82)
                fig.add_annotation(x=x1, y=y1, ax=x0, ay=y0, xref="x", yref="y", axref="x", ayref="y", showarrow=True,
                                   arrowhead=2, arrowsize=1.4, arrowwidth=2, arrowcolor=ACCENT, text="")

    # invisible markers at link midpoints carry hover text and make links clickable
    mids = [_along(pos[l.source], pos[l.target], 0.5) for l in links]
    hover = []
    for l in links:
        state = "failed" if l.link_id in failed_links else "up"
        text = (f"<b>{l.link_id}</b> ({state})<br>{l.bandwidth_bps / 1e6:g} Mbps, {l.propagation_delay_s * 1000:g} ms propagation"
                f"<br>routing cost {l.routing_cost:g}, queue capacity {l.queue_capacity_packets} packets")
        if snapshot is not None:
            for sender in (l.source, l.target):
                port = snapshot.ports.get((l.link_id, sender))
                other = l.target if sender == l.source else l.source
                if port:
                    text += (f"<br>{sender} → {other}: {len(port.waiting)} waiting"
                             f"{', 1 transmitting' if port.transmitting is not None else ''}"
                             f"{', ' + str(len(port.propagating)) + ' propagating' if port.propagating else ''}")
        hover.append(text)
    fig.add_trace(go.Scatter(
        x=[m[0] for m in mids], y=[m[1] for m in mids], mode="markers", marker=dict(size=18, opacity=0),
        hovertext=hover, hoverinfo="text", showlegend=False, customdata=[l.link_id for l in links], name="links",
        selected=dict(marker=dict(opacity=0)), unselected=dict(marker=dict(opacity=0)),
    ))
    for link, (x, y) in zip(links, mids):
        failed = link.link_id in failed_links
        fig.add_annotation(x=x, y=y, text=f"{link.bandwidth_bps / 1e6:g} Mbps" + (" (failed)" if failed else ""),
                           showarrow=False, font=dict(size=10, color=DANGER if failed else MUTED),
                           bgcolor=LABEL_BACKING, borderpad=1, yshift=9)  # above the line; queue badges sit below it

    if snapshot is not None:  # queue sizes next to the sending end of a busy link
        for (link_id, sender), port in snapshot.ports.items():
            if not port.waiting:
                continue
            link = link_by_id[link_id]
            other = link.target if sender == link.source else link.source
            x, y = _along(pos[sender], pos[other], QUEUE_BADGE_AT)
            ratio = len(port.waiting) / port.capacity if port.capacity else 1.0
            color = QUEUE_FULL_COLOR if ratio >= 0.9 else QUEUE_BUSY_COLOR if ratio >= 0.5 else MUTED
            fig.add_annotation(x=x, y=y, text=f"{len(port.waiting)}/{port.capacity}", showarrow=False, yshift=-10,
                               font=dict(size=10, color=color), bgcolor=theme.PANEL, bordercolor=color, borderwidth=1, borderpad=1)
        dots_x, dots_y, dots_text = [], [], []
        for (link_id, sender), port in snapshot.ports.items():
            link = link_by_id[link_id]
            other = link.target if sender == link.source else link.source
            if port.transmitting is not None:
                x, y = _along(pos[sender], pos[other], SERIALIZING_AT)
                dots_x.append(x), dots_y.append(y), dots_text.append(f"Packet {port.transmitting} transmitting on {link_id} ({sender} → {other})")
            for pid, fraction in port.propagating:
                x, y = _along(pos[sender], pos[other], PROPAGATION_START + (PROPAGATION_END - PROPAGATION_START) * fraction)
                dots_x.append(x), dots_y.append(y), dots_text.append(f"Packet {pid} propagating on {link_id} ({sender} → {other})")
        if dots_x:
            fig.add_trace(go.Scatter(x=dots_x, y=dots_y, mode="markers", name="Packet on link", hovertext=dots_text, hoverinfo="text",
                                     marker=dict(size=8, color=INK, line=dict(width=1.5, color=theme.BG_APP))))

    for node_type, (name, symbol, color, anchor, shift) in _NODE_STYLE.items():
        for failed in (False, True):
            nodes = [n for n in topology.nodes.values() if n.node_type == node_type and (n.node_id in failed_routers) == failed]
            if not nodes:
                continue
            fig.add_trace(go.Scatter(
                x=[pos[n.node_id][0] for n in nodes], y=[pos[n.node_id][1] for n in nodes], mode="markers",
                name=f"{name} (failed)" if failed else name, customdata=[n.node_id for n in nodes],
                marker=dict(symbol=symbol, size=26 if node_type in ("router", "switch") else 22,
                            color=theme.BG_APP if failed else color,
                            line=dict(width=3 if failed else 1.5, color=DANGER if failed else theme.BG_APP)),
                hovertext=[f"<b>{n.name}</b> ({n.node_id})<br>{n.node_type.replace('_', ' ')}"
                           + (f"<br>{n.ip_address}" if n.ip_address else "") for n in nodes],
                hoverinfo="text", selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=1)),
            ))
            # Labels are annotations, not trace text, so a dark backing keeps them readable over route lines.
            for n in nodes:
                fig.add_annotation(
                    x=pos[n.node_id][0], y=pos[n.node_id][1], text=n.node_id, showarrow=False,
                    xanchor=anchor[0], yanchor=anchor[1], xshift=shift[0], yshift=shift[1],
                    font=dict(size=12, color=DANGER if failed else INK), bgcolor=LABEL_BACKING, borderpad=1,
                )

    for label in HIGHLIGHT_COLORS:
        rings = [(kind, target) for kind, target, lab in (highlights or []) if lab == label]
        points = []
        for kind, target in rings:
            if kind == "node" and target in pos:
                points.append(pos[target])
            elif kind == "link" and target in link_by_id:
                l = link_by_id[target]
                points.append(_along(pos[l.source], pos[l.target], 0.5))
        if points:
            fig.add_trace(go.Scatter(x=[p[0] for p in points], y=[p[1] for p in points], mode="markers", name=label, hoverinfo="skip",
                                     marker=dict(symbol="circle-open", size=34, color=HIGHLIGHT_COLORS[label],
                                                 line=dict(width=3, color=HIGHLIGHT_COLORS[label]))))
    if followed is not None:
        packet_id, state = followed
        place = link_position(state, pos, link_by_id)
        if place is not None:
            tail = {"delivered": "delivered", "dropped": "dropped here"}.get(state.kind, state.kind.replace("_", " "))
            name = f"Dropped packet {packet_id}" if state.kind == "dropped" else f"Followed packet {packet_id}"
            symbol, color = {"dropped": ("x", DANGER), "delivered": ("circle", theme.GREEN)}.get(state.kind, ("star", ACCENT))
            fig.add_trace(go.Scatter(x=[place[0]], y=[place[1]], mode="markers", name=name,
                                     hovertext=[f"Packet {packet_id}: {tail}"], hoverinfo="text",
                                     marker=dict(symbol=symbol, size=15, color=color, line=dict(width=1.5, color=theme.BG_APP))))

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x_range = FOCUS_REGIONS.get(focus) or (min(xs) - LEFT_MARGIN, max(xs) + 1.0)
    fig.update_xaxes(visible=False, range=list(x_range), fixedrange=True)
    fig.update_yaxes(visible=False, range=[min(ys) - 0.7, max(ys) + 0.7], fixedrange=True, scaleanchor=None)
    _style(fig, height)
    fig.update_xaxes(title_text=None)
    fig.update_yaxes(title_text=None)
    fig.update_layout(margin=dict(l=8, r=8, t=36, b=8), legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      clickmode="event+select")  # axes are fixed, so the default drag mode cannot pan or zoom
    return fig




# --- result charts ---


def _color(index: int) -> str:
    return RUN_COLORS[index % len(RUN_COLORS)]


def _dash(index: int) -> str:
    """Line style per series, so warm colours that sit close together stay distinguishable."""
    return ["solid", "dash", "dot", "dashdot", "longdash"][index % 5]


def throughput_chart(series_by_run: dict[str, list[tuple[float, float]]], interval_s: float, duration_s: float) -> go.Figure:
    """Delivered throughput per time bin. Each series is (bin_start_s, bps) as from metrics.throughput_over_time."""
    if not series_by_run:
        return empty_figure("Run a simulation to see throughput over time.")
    fig = go.Figure()
    for index, (label, series) in enumerate(series_by_run.items()):
        xs = [start for start, _ in series] + [duration_s]
        ys = [bps / 1e6 for _, bps in series] + [series[-1][1] / 1e6]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=label,
                                 line=dict(color=_color(index), width=2, shape="hv", dash=_dash(index)),
                                 hovertemplate=f"{label}<br>t = %{{x:.2f}} s<br>%{{y:.2f}} Mbps<extra></extra>"))
    _style(fig, 340, f"Simulation time (s), {interval_s * 1000:g} ms bins", "Delivered throughput (Mbps)")
    fig.update_yaxes(rangemode="tozero")
    fig.update_xaxes(range=[0, duration_s])
    return fig


MAX_QUEUE_POINTS = 1500
QUEUE_BINS = 400


def peak_per_bin(series: list[tuple[float, int]], duration_s: float, bins: int = QUEUE_BINS) -> list[tuple[float, int]]:
    """Reduce a step series to the largest value seen in each of `bins` equal time bins.

    Keeps every peak (so the maximum is exact) while removing the thousands of tiny oscillations a busy
    queue produces, which would otherwise draw as a solid band. The value in force at a bin's start counts.
    """
    width = duration_s / bins
    peaks: list[int] = []
    current = 0
    index = 0
    for b in range(bins):
        end = (b + 1) * width
        peak = current  # the value in force at the start of the bin
        while index < len(series) and (series[index][0] < end or b == bins - 1):  # the last bin includes the end
            current = series[index][1]
            peak = max(peak, current)
            index += 1
        peaks.append(peak)
    return [(b * width, peaks[b]) for b in range(bins)] + [(duration_s, peaks[-1])]


def queue_chart(series_by_run: dict[str, list[tuple[float, int]]], duration_s: float, link_label: str) -> go.Figure:
    if not series_by_run:
        return empty_figure("Run a simulation to see queue occupancy.")
    fig = go.Figure()
    reduced = False
    for index, (label, series) in enumerate(series_by_run.items()):
        if len(series) > MAX_QUEUE_POINTS:
            series, reduced = peak_per_bin(series, duration_s), True
        fig.add_trace(go.Scatter(x=[t for t, _ in series], y=[n for _, n in series], mode="lines", name=label,
                                 line=dict(color=_color(index), width=1.8, shape="hv", dash=_dash(index)),
                                 hovertemplate=f"{label}<br>t = %{{x:.3f}} s<br>%{{y}} packets waiting<extra></extra>"))
    suffix = f", peak per {duration_s / QUEUE_BINS * 1000:g} ms" if reduced else ""
    _style(fig, 340, "Simulation time (s)", f"Packets waiting ({link_label}){suffix}")
    fig.update_yaxes(rangemode="tozero")
    fig.update_xaxes(range=[0, duration_s])
    return fig


def delay_by_class_chart(metrics_by_run: dict[str, Metrics]) -> go.Figure:
    """Average end-to-end delay (ms) of delivered packets, grouped by traffic class."""
    classes = ["realtime", "interactive", "bulk"]
    fig = go.Figure()
    any_data = False
    for index, (label, metrics) in enumerate(metrics_by_run.items()):
        values = [None if metrics.by_class[c].avg_delay_s is None else metrics.by_class[c].avg_delay_s * 1000 for c in classes]
        any_data = any_data or any(v is not None for v in values)
        fig.add_trace(go.Bar(x=[c.capitalize() for c in classes], y=values, name=label, marker_color=_color(index),
                             text=[None if v is None else f"{v:.1f}" for v in values], textposition="outside",
                             textfont=dict(color=INK), marker_line_width=0,
                             hovertemplate=f"{label}<br>%{{x}}: %{{y:.2f}} ms<extra></extra>"))
    if not any_data:
        return empty_figure("No packets were delivered, so there is no delay to show.")
    _style(fig, 340, "Traffic class", "Average end-to-end delay (ms)")
    fig.update_layout(barmode="group")
    fig.update_yaxes(rangemode="tozero")
    return fig


def outcomes_chart(metrics_by_run: dict[str, Metrics]) -> go.Figure:
    """Share of generated packets that were delivered, unresolved, or dropped (split by drop reason)."""
    runs = {label: m for label, m in metrics_by_run.items() if m.overall.generated > 0}
    if not runs:
        return empty_figure("No packets were generated.")
    reasons = sorted({r for m in runs.values() for r in m.overall.drop_reasons})
    parts: list[tuple[str, str, object]] = [("Delivered", theme.GREEN, lambda m: m.overall.delivered),
                                            ("Unresolved at end", theme.UNRESOLVED, lambda m: m.overall.unresolved)]
    parts += [(f"Dropped: {r}", _DROP_REASON_COLORS.get(r, DANGER), (lambda rr: lambda m: m.overall.drop_reasons.get(rr, 0))(r))
              for r in reasons]
    fig = go.Figure()
    labels = list(runs)
    for name, color, count in parts:
        counts = [count(runs[label]) for label in labels]  # type: ignore[operator]
        shares = [100 * c / runs[label].overall.generated for c, label in zip(counts, labels)]
        fig.add_trace(go.Bar(y=labels, x=shares, name=name, orientation="h", marker_color=color,
                             customdata=counts, hovertemplate=f"%{{y}}<br>{name}: %{{x:.1f}}% (%{{customdata}} packets)<extra></extra>"))
    _style(fig, max(220, 70 + 56 * len(labels)), "Share of generated packets (%)", None)
    fig.update_layout(barmode="stack", legend=dict(traceorder="normal"))
    fig.update_xaxes(range=[0, 100])
    fig.update_yaxes(autorange="reversed")
    return fig
