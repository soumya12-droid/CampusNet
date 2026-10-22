"""CampusNet dashboard: an interactive network lab. Layout and callbacks only.

Simulation, metrics, replay and text generation live in campusnet/. The workspace replays a finished
run from its recorded events; it does not re-run or animate anything on its own.
"""

import streamlit as st

from campusnet import help_text, theme
from campusnet.config import ExperimentConfig, FailureEvent, validate_config
from campusnet.experiments import (
    BOTTLENECK,
    BOTTLENECK_LINK_ID,
    PRESETS,
    ExperimentResult,
    ExperimentRun,
    base_config,
    run_preset,
)
from campusnet.insights import link_details, node_details, packet_details, preset_observations, run_observations
from campusnet.metrics import calculate_metrics, throughput_over_time
from campusnet.presentation import (
    EVENT_DESCRIPTIONS,
    POLICY_LABELS,
    PRESET_INFO,
    class_delivery_view,
    class_queueing_view,
    class_table,
    config_summary,
    config_table,
    event_log_frame,
    events_window,
    experiment_export,
    failure_breakdown,
    filter_event_log,
    flow_routes_table,
    fmt_mbps,
    fmt_ms,
    fmt_pct,
    journey_table,
    link_metrics_table,
    offered_load_bps,
    outcomes_table,
    performance_table,
    queue_length_series,
    route_summary,
    routes_table,
    run_class_table,
    to_csv_bytes,
    topology_tables,
    workload_table,
)
from campusnet.replay import NAV_FILTERS, ReplayIndex
from campusnet.routing import shortest_path
from campusnet.simulator import run_simulation
from campusnet.topology import build_default_topology
from campusnet.visualization import (
    FOCUS_REGIONS,
    delay_by_class_chart,
    outcomes_chart,
    queue_chart,
    throughput_chart,
    topology_figure,
)

st.set_page_config(page_title="CampusNet", layout="wide", initial_sidebar_state="expanded")

st.markdown(f"<style>{theme.css()}</style>", unsafe_allow_html=True)  # palette and CSS: campusnet/theme.py

DEFAULTS = {
    "duration": 2.0, "intensity": 1.0, "seed": 42, "bottleneck": 10.0, "capacity": 50, "policy": "fifo",
    "failure_kind": "None", "fail_link": "r1-r2", "fail_router": "r2", "fail_time": 1.0,
}
WORKSPACE_DEFAULTS = {
    "evt_idx": -1, "tl_time": 0.0, "view_flow": "All traffic", "view_focus": "Whole network", "step_filter": "Any event",
    "inspect_item": "None", "followed_packet": None, "packet_choice": 1, "plot_gen": 0, "evt_gen": 0, "evt_window_ids": [],
    "route_from": "stu1", "route_to": "portal",
}
GUIDE_DEFAULTS = {"guide_open": True}  # a preference: Reset does not bring a dismissed guide back
ANALYSIS_VIEWS = ["Summary", "Traffic & QoS", "Performance", "Failure & routing", "Experiments", "Event log"]
INSPECTOR_PARTS = ["Event", "Packet", "Route", "Inspect"]
CHART_CONFIG = {"displayModeBar": False}
MAX_LOG_ROWS = 2000
ALL_TRAFFIC = "All traffic"


# --- state ---


def init_state() -> None:
    for key, value in {**DEFAULTS, **WORKSPACE_DEFAULTS, **GUIDE_DEFAULTS}.items():
        st.session_state.setdefault(key, value)
    st.session_state.setdefault("custom", None)  # {"config", "result", "metrics"} of the last custom run
    st.session_state.setdefault("presets", {})  # preset id -> ExperimentResult
    st.session_state.setdefault("frames", {})  # event-log DataFrames, keyed by source label
    st.session_state.setdefault("replays", {})  # label -> (SimulationResult, ReplayIndex)
    st.session_state.setdefault("view_source", None)


def reset_all() -> None:
    for key, value in {**DEFAULTS, **WORKSPACE_DEFAULTS}.items():
        st.session_state[key] = value
    st.session_state["custom"] = None
    st.session_state["presets"] = {}
    st.session_state["frames"] = {}
    st.session_state["replays"] = {}
    st.session_state["view_source"] = None


def section(title: str) -> None:
    st.markdown(f'<div class="cn-section">{title}</div>', unsafe_allow_html=True)


def show_table(frame, formats: dict[str, str] | None = None, **kwargs) -> None:
    """Show a DataFrame with compact numeric columns. `formats` overrides the number format of named columns."""
    formats = formats or {}

    def width(column: str) -> str:
        return "medium" if len(column) > 14 else "small"  # long headers get room instead of being clipped

    config = {c: st.column_config.NumberColumn(format=formats.get(c, "%.2f"), width=width(c), help=help_text.COLUMN_HELP.get(c))
              for c in frame.columns if frame[c].dtype.kind == "f"}
    config.update({c: st.column_config.NumberColumn(format=formats.get(c), width=width(c), help=help_text.COLUMN_HELP.get(c))
                   for c in frame.columns if frame[c].dtype.kind in "iu"})
    st.dataframe(frame, hide_index=True, column_config=config, **kwargs)


def show_chart(fig, key: str, **kwargs):
    return st.plotly_chart(fig, theme=None, config=CHART_CONFIG, key=key, **kwargs)


def empty_state(message: str) -> None:
    st.info(message)


# --- runs ---


def custom_run_object() -> ExperimentRun | None:
    custom = st.session_state["custom"]
    if custom is None:
        return None
    return ExperimentRun("Custom run", {}, custom["config"], custom["result"], custom["metrics"])


def run_sources() -> dict[str, ExperimentRun]:
    """Every finished run that can be viewed: the custom run and each run of every preset that was run."""
    sources: dict[str, ExperimentRun] = {}
    custom = custom_run_object()
    if custom:
        sources["Custom run"] = custom
    for preset_id, experiment in st.session_state["presets"].items():
        for run in experiment.runs:
            sources[f"Preset {preset_id}: {run.label}"] = run
    return sources


def viewed() -> tuple[str, ExperimentRun, ReplayIndex] | None:
    sources = run_sources()
    label = st.session_state["view_source"]
    if label not in sources:
        label = next(iter(sources), None)
        st.session_state["view_source"] = label
    if label is None:
        return None
    run = sources[label]
    cached = st.session_state["replays"].get(label)
    if cached is None or cached[0] is not run.result:
        cached = (run.result, ReplayIndex(run.result))
        st.session_state["replays"][label] = cached
    return label, run, cached[1]


def go_to(index: int) -> None:
    current = viewed()
    if current is None:
        return
    replay = current[2]
    index = max(-1, min(index, len(replay) - 1))
    st.session_state["evt_idx"] = index
    st.session_state["tl_time"] = replay.records[index].timestamp_s if index >= 0 else 0.0


def show_run(label: str) -> None:
    """Point the workspace at a run (at its end state, where congestion and failures are visible)."""
    st.session_state["view_source"] = label
    st.session_state["followed_packet"] = None
    st.session_state["view_flow"] = ALL_TRAFFIC
    st.session_state["plot_gen"] += 1
    st.session_state["evt_gen"] += 1
    current = viewed()
    if current:
        go_to(len(current[2]) - 1)


# --- sidebar: custom run ---


def build_config() -> ExperimentConfig:
    s = st.session_state
    config = base_config(duration_s=float(s["duration"]), seed=int(s["seed"]), intensity=float(s["intensity"]))
    config.queue_policy = s["policy"]
    config.queue_capacity_packets = int(s["capacity"])
    config.bandwidth_overrides = {BOTTLENECK_LINK_ID: float(s["bottleneck"]) * 1e6}
    if s["failure_kind"] == "Link":
        config.failure_events = [FailureEvent(float(s["fail_time"]), "link", s["fail_link"])]
    elif s["failure_kind"] == "Router":
        config.failure_events = [FailureEvent(float(s["fail_time"]), "router", s["fail_router"])]
    return config


def sidebar() -> bool:
    """Draw the custom-run controls. Returns True when the user pressed Run."""
    topology = build_default_topology()
    with st.sidebar:
        st.markdown('<div class="cn-title" style="font-size:1.05rem">Custom run</div>', unsafe_allow_html=True)
        st.caption("Configure a simulation here. Press Run, then explore it in the network workspace.")

        section("Workload")
        h = help_text.CONTROL_HELP
        st.number_input("Duration (s)", min_value=0.5, max_value=20.0, step=0.5, key="duration", help=h["duration"])
        st.slider("Traffic intensity (x reference load)", 0.1, 2.0, step=0.1, key="intensity", help=h["intensity"])
        st.number_input("Random seed", min_value=0, max_value=10_000, step=1, key="seed", help=h["seed"])

        section("Network")
        st.number_input("Bottleneck r1-r2 bandwidth (Mbps)", min_value=1.0, max_value=100.0, step=1.0, key="bottleneck", help=h["bottleneck"])
        st.number_input("Queue capacity (packets per link)", min_value=0, max_value=1000, step=5, key="capacity", help=h["capacity"])
        st.radio("Queue policy", list(POLICY_LABELS), format_func=POLICY_LABELS.get, key="policy", help=h["policy"])

        section("Failure")
        st.selectbox("Inject a failure", ["None", "Link", "Router"], key="failure_kind", help=h["failure_kind"])
        kind = st.session_state["failure_kind"]
        if kind == "Link":
            st.selectbox("Link", sorted(topology.links), key="fail_link")
        elif kind == "Router":
            st.selectbox("Router", sorted(n.node_id for n in topology.nodes.values() if n.node_type == "router"), key="fail_router")
        if kind != "None":
            duration = float(st.session_state["duration"])
            st.session_state["fail_time"] = min(float(st.session_state["fail_time"]), duration)
            st.number_input("Failure time (s)", min_value=0.0, max_value=duration, step=0.1, key="fail_time", help=h["fail_time"])
            st.caption(help_text.FAILURE_NOTE)
            st.caption(help_text.PATH_NOTE)

        config = build_config()
        problems = validate_config(config)
        st.caption(
            f"Offered load {offered_load_bps(config) / 1e6:.1f} Mbps vs bottleneck "
            f"{float(st.session_state['bottleneck']):g} Mbps"
        )
        for problem in problems:
            st.error(problem)
        st.write("")
        run = st.button("Run simulation", type="primary", disabled=bool(problems), width="stretch")
        st.button("Reset", on_click=reset_all, width="stretch")
    return run


def execute_custom_run() -> None:
    config = build_config()
    try:
        with st.spinner("Simulating..."):
            result = run_simulation(config)
            metrics = calculate_metrics(result)
    except ValueError as error:
        st.sidebar.error(f"Could not run: {error}")
        return
    st.session_state["custom"] = {"config": config, "result": result, "metrics": metrics}
    st.session_state["frames"].pop("Custom run", None)
    show_run("Custom run")
    st.toast(f"Run complete: {len(result.records):,} events recorded. Use the timeline to replay them.")


def request_action(action: tuple) -> None:
    """Record an action (("custom",) or ("preset", id)) for main() to run before any widgets are drawn.

    Running it from a button handler would change widget state after the workspace has been created.
    """
    st.session_state["pending_action"] = action


def execute_preset(preset_id: str) -> None:
    with st.spinner(f"Running preset {preset_id}..."):
        st.session_state["presets"][preset_id] = run_preset(preset_id)
        for label in [k for k in st.session_state["frames"] if k.startswith(f"Preset {preset_id}")]:
            st.session_state["frames"].pop(label)
    first = st.session_state["presets"][preset_id].runs[-1]
    show_run(f"Preset {preset_id}: {first.label}")
    st.toast(f"Preset {preset_id} complete. Its last run is on the network workspace.")


def header() -> None:
    left, right = st.columns([3, 2])
    left.markdown(
        '<div class="cn-title">CampusNet</div>'
        '<div class="cn-sub">An interactive lab for a simulated campus network: routing, queues, congestion and failures</div>',
        unsafe_allow_html=True,
    )
    current = viewed()
    status = "No run yet" if current is None else f"Viewing {current[0]} · {len(current[2]):,} recorded events"
    right.markdown(f'<div class="cn-status">{status}</div>', unsafe_allow_html=True)
    if not st.session_state["guide_open"]:
        right.button("Show guide", on_click=set_guide, args=(True,), key="show_guide")


def set_guide(open_: bool) -> None:
    st.session_state["guide_open"] = open_


def guide() -> None:
    """The compact, dismissible first-use guide: the five-step workflow and the scope of the simulator."""
    if not st.session_state["guide_open"]:
        return
    with st.container(border=True):
        top = st.columns([4, 1], vertical_alignment="center")
        top[0].markdown("**Start here** · five steps")
        top[1].button("Hide guide", on_click=set_guide, args=(False,), key="hide_guide", width="stretch")
        for col, (n, (title, text)) in zip(st.columns(5, gap="small"), enumerate(help_text.GUIDE_STEPS, 1)):
            col.markdown(f"**{n}. {title}**")
            col.caption(text)
        st.caption(help_text.SCOPE)


# --- network workspace ---


MONO_FIELDS = {"IPv4", "Subnet", "Path (fixed at creation)", "Created", "From → to"}  # addresses, paths and times read as code


def fact(name: str, value: str) -> None:
    """One labelled value in the inspector; technical values are set in the monospace font."""
    st.markdown(f"**{name}:** " + (f"`{value}`" if name in MONO_FIELDS and value not in ("none", "") else value))


def parse_pick(pick: str) -> tuple[str, str] | None:
    if pick == "None" or ": " not in pick:
        return None
    kind, ident = pick.split(": ", 1)
    return ("node" if kind == "Device" else "link", ident)


def inspected() -> tuple[str, str] | None:
    return parse_pick(st.session_state.get("inspect_item", "None"))


def sync_inspect() -> None:
    st.session_state["inspect_item"] = st.session_state["inspect_widget"]


def inspect_selector(options: list[str]) -> None:
    """The Inspect selector. The inspected item lives in `inspect_item`, which persists whether or not this
    widget is drawn (Streamlit drops the state of a widget that was not drawn in the previous run)."""
    st.session_state["inspect_widget"] = st.session_state.get("inspect_item", "None")
    st.selectbox("Device or link", options, key="inspect_widget", on_change=sync_inspect)


def apply_plot_click(topology) -> None:
    """Turn a click on the topology figure into the inspected item, then reset the figure's selection."""
    key = f"topo_plot_{st.session_state['plot_gen']}"
    state = st.session_state.get(key)
    points = state["selection"]["points"] if state and "selection" in state else []
    for point in points:
        ident = point.get("customdata")
        ident = ident[0] if isinstance(ident, (list, tuple)) and ident else ident
        if ident in topology.nodes:
            st.session_state["inspect_item"] = f"Device: {ident}"
        elif ident in topology.links:
            st.session_state["inspect_item"] = f"Link: {ident}"
        else:
            continue
        st.session_state["plot_gen"] += 1  # a fresh figure widget, so this selection is not applied again
        st.session_state["inspector_view"] = "Inspect"
        break


def apply_event_table_click() -> None:
    key = f"evt_tbl_{st.session_state['evt_gen']}"
    state = st.session_state.get(key)
    rows = state["selection"]["rows"] if state and "selection" in state else []
    ids = st.session_state["evt_window_ids"]
    if rows and rows[0] < len(ids):
        go_to(ids[rows[0]])
        st.session_state["evt_gen"] += 1


def on_slider() -> None:
    current = viewed()
    if current:
        st.session_state["evt_idx"] = current[2].index_at_time(float(st.session_state["tl_time"]))


def on_step(direction: int) -> None:
    current = viewed()
    if current:
        idx = st.session_state["evt_idx"]
        target = current[2].step(idx, st.session_state["step_filter"], direction)
        if target is None and direction < 0 and idx == 0 and st.session_state["step_filter"] == "Any event":
            target = -1  # stepping back from the first event returns to the idle network before the run
        if target is not None:
            go_to(target)


def on_jump(filter_name: str) -> None:
    current = viewed()
    if current:
        target = current[2].first(filter_name)
        if target is not None:
            go_to(target)


def sync_packet() -> None:
    st.session_state["packet_choice"] = st.session_state["packet_widget"]


def on_follow(packet_id) -> None:
    st.session_state["followed_packet"] = packet_id
    if packet_id is not None:
        st.session_state["packet_choice"] = packet_id  # persistent; the number box below is only a view of it
        st.session_state["inspector_view"] = "Packet"


def flow_options(replay: ReplayIndex) -> list[str]:
    return [ALL_TRAFFIC] + [f.flow_id for f in replay.result.config.flows]


@st.fragment
def workspace() -> None:
    current = viewed()
    initial = build_default_topology()
    apply_plot_click(initial)
    if current is None:
        workspace_empty(initial)
        return
    label, run, replay = current
    apply_event_table_click()
    result = run.result
    flows = flow_options(replay)
    if st.session_state.get("view_flow") not in flows:
        st.session_state["view_flow"] = ALL_TRAFFIC
    uses = replay.flow_routes()

    c1, c2, c3, c4 = st.columns([2.2, 1.6, 1.6, 1.4], vertical_alignment="bottom")
    sources = list(run_sources())
    c1.selectbox("Run on screen", sources, key="view_source", on_change=lambda: show_run(st.session_state["view_source"]))
    c2.selectbox("Follow a flow", flows, key="view_flow",
                 format_func=lambda f: f if f == ALL_TRAFFIC or len(uses.get(f, [])) < 2 else f"{f} (route changed)")
    c3.selectbox("Focus", list(FOCUS_REGIONS), key="view_focus")
    c4.caption("Click a device or link to inspect it.")

    T = float(result.duration_s)
    st.session_state["tl_time"] = min(max(float(st.session_state["tl_time"]), 0.0), T)
    idx = st.session_state["evt_idx"]
    time_s = st.session_state["tl_time"]
    snap = replay.snapshot(idx, time_s)
    record = replay.records[idx] if idx >= 0 else None

    # routes and markers drawn on the picture: only what the recorded run established
    routes: list[tuple[str, list[str]]] = []
    followed = None
    packet_id = st.session_state["followed_packet"]
    flow_note = ""
    if packet_id is None and record is not None and record.event_type == "dropped":
        followed = (record.packet_id, replay.packet_state(record.packet_id, idx, time_s))  # mark where the drop happened
    if packet_id is not None and packet_id in replay.packets:
        path = replay.packets[packet_id].path
        followed = (packet_id, replay.packet_state(packet_id, idx, time_s))
        if path:
            routes = [(f"Path of packet {packet_id}", list(path))]
    elif st.session_state["view_flow"] != ALL_TRAFFIC:
        now = replay.route_at(st.session_state["view_flow"], idx)
        if now is None:
            flow_note = "No packet of this flow has been created yet at this point in the run."
        elif now == ():
            flow_note = "Packets of this flow have no route at this point (no path existed when they were created)."
        else:
            routes = [("Route in use", list(now))]
            others = [list(u.path) for u in uses[st.session_state["view_flow"]] if u.path and u.path != now]
            if others:
                routes.append(("Other route used in this run", others[0]))
    highlights = []
    if record is not None and record.location:
        kind = "link" if record.location in result.topology.links else "node"
        highlights.append((kind, record.location, "Selected event"))
    picked = inspected()
    if picked:
        highlights.append((picked[0], picked[1], "Inspected item"))

    main, side = st.columns([7.2, 3.6], gap="medium")
    with main:
        fig = topology_figure(result.topology, routes, 540, snapshot=snap, failed_links=snap.failed_links,
                              failed_routers=snap.failed_routers, focus=st.session_state["view_focus"],
                              highlights=highlights, followed=followed)
        show_chart(fig, f"topo_plot_{st.session_state['plot_gen']}", on_select="rerun", selection_mode="points")
        if flow_note:
            st.caption(flow_note)
        with st.expander("How to read this network"):
            st.markdown(help_text.network_legend(), unsafe_allow_html=True)
        timeline(replay, snap, T)
    with side:
        inspector(label, run, replay, snap, idx, time_s, uses)


def timeline(replay: ReplayIndex, snap, duration_s: float) -> None:
    st.markdown('<div class="cn-step">Timeline: this run has finished, and the controls replay its recorded events in order.</div>',
                unsafe_allow_html=True)
    with st.expander("How the timeline works"):
        st.markdown(help_text.TIMELINE_HELP)
    b1, b2, b3, b4, b5 = st.columns([1, 1, 1, 1, 2.4], vertical_alignment="bottom")
    b1.button("Start", on_click=go_to, args=(-1,), width="stretch")
    b2.button("Previous", on_click=on_step, args=(-1,), width="stretch")
    b3.button("Next", on_click=on_step, args=(1,), width="stretch")
    b4.button("End", on_click=go_to, args=(len(replay) - 1,), width="stretch")
    b5.selectbox("Step through", list(NAV_FILTERS), key="step_filter", help="Previous and Next move between events of this kind.")
    st.slider("Simulation time (s)", 0.0, float(duration_s), key="tl_time", step=0.0001, format="%.4f", on_change=on_slider)
    waiting = sum(len(p.waiting) for p in snap.ports.values())
    idx = snap.index
    st.caption(
        f"Event {idx + 1:,} of {len(replay):,} · t = `{snap.time_s:.4f} s` · "
        f"{snap.in_network:,} packet{'' if snap.in_network == 1 else 's'} in the network "
        f"({waiting:,} waiting in queues) · {snap.delivered:,} delivered · {snap.dropped:,} dropped so far"
    )
    j = st.columns(4)
    for col, (text, name) in zip(j, [("First drop", "Drops"), ("Failure", "Failures"), ("First delivery", "Deliveries"), ("First transmission", "Transmissions")]):
        col.button(text, on_click=on_jump, args=(name,), width="stretch", disabled=replay.first(name) is None, key=f"jump_{name}")
    window = events_window(replay, idx)
    st.session_state["evt_window_ids"] = list(window["#"])
    st.dataframe(window, hide_index=True, height=40 + 35 * len(window), key=f"evt_tbl_{st.session_state['evt_gen']}", on_select="rerun",
                 selection_mode="single-row",
                 column_config={"Time (s)": st.column_config.NumberColumn(format="%.4f"), "Now": st.column_config.TextColumn(width="small"),
                                "#": st.column_config.NumberColumn(width="small"), "Packet": st.column_config.NumberColumn(width="small")})


def inspector(label: str, run: ExperimentRun, replay: ReplayIndex, snap, idx: int, time_s: float, uses: dict) -> None:
    result = run.result
    record = replay.records[idx] if idx >= 0 else None
    with st.container(border=True):
        part = st.segmented_control("Inspector", INSPECTOR_PARTS, key="inspector_view", default="Event", label_visibility="collapsed") or "Event"
        st.caption(help_text.INSPECTOR_HELP[part])
        if part == "Event":
            if record is None:
                st.markdown("**Before the first event.** The network is idle. Press Next, or move the slider, to replay the run.")
            else:
                st.markdown(f"**{record.event_type.replace('_', ' ').capitalize()}** at `{record.timestamp_s:.4f} s`")
                st.markdown(replay.explain(idx))
                if record.event_type == "dropped":
                    st.caption(help_text.DROP_NOTE)
                if record.packet_id is not None:
                    st.button(f"Follow packet {record.packet_id}", on_click=on_follow, args=(record.packet_id,), key="follow_event")
        elif part == "Packet":
            followed = st.session_state["followed_packet"]
            last = max(1, len(result.packets))
            # The choice lives in `packet_choice`, which persists whether or not this widget is drawn (Streamlit drops the
            # state of an undrawn widget); a shorter run may have fewer packets, so it is clamped.
            st.session_state["packet_choice"] = min(max(int(st.session_state.get("packet_choice", followed or 1)), 1), last)
            st.session_state["packet_widget"] = st.session_state["packet_choice"]
            st.number_input("Packet ID", min_value=1, max_value=last, step=1, key="packet_widget", on_change=sync_packet)
            pid = int(st.session_state["packet_choice"])
            if pid in replay.packets:
                st.button("Follow on the network", on_click=on_follow, args=(pid,), key="follow_packet")
                if followed is not None:
                    st.button("Stop following", on_click=on_follow, args=(None,), key="unfollow")
                for name, value in packet_details(replay, pid):
                    fact(name, value)
                state = replay.packet_state(pid, idx, time_s)
                st.caption(f"At this moment: {state.kind.replace('_', ' ')}" + (f" on {state.link_id}" if state.link_id else ""))
                st.button("Go to its first event", on_click=go_to, args=(replay.journey(pid)[0],), key="packet_first")
                with st.expander("Every event of this packet"):
                    show_table(journey_table(replay, pid)[["#", "Time (s)", "Event", "Where", "What happened"]], formats={"Time (s)": "%.4f"})
            else:
                st.caption("No packet with that ID in this run.")
        elif part == "Route":
            route_tab(replay, uses, idx)
        else:
            options = ["None"] + [f"Device: {n}" for n in result.topology.nodes] + [f"Link: {l}" for l in result.topology.links]
            inspect_selector(options)
            picked = inspected()
            if picked is None:
                st.caption("Choose a device or link, or click one on the network.")
            else:
                rows = (node_details(result.topology, picked[1], snap) if picked[0] == "node"
                        else link_details(result.topology, picked[1], snap, run.metrics))
                for name, value in rows:
                    fact(name, value)


def route_tab(replay: ReplayIndex, uses: dict, idx: int) -> None:
    flow = st.session_state["view_flow"]
    st.caption(help_text.PATH_NOTE)
    if flow == ALL_TRAFFIC:
        changed = [f for f, u in uses.items() if len(u) > 1]
        st.markdown("Choose **Follow a flow** above to draw the route its packets used.")
        if changed:
            st.markdown("**Routes changed during this run:** " + ", ".join(changed))
        else:
            st.caption("Every flow used a single path in this run.")
        return
    table = flow_routes_table(replay)
    mine = table[table["Flow"] == flow]
    now = replay.route_at(flow, idx)
    st.markdown(f"**{flow}**")
    if now:
        info = route_summary(replay.result.topology, list(now))
        st.markdown("Route at this point: `" + " → ".join(now) + "`")
        st.caption(f"{info['hops']:.0f} hops, routing cost {info['cost']:g}, slowest link {info['slowest_link_mbps']:g} Mbps")
    st.caption("Paths its packets actually took in this run:")
    show_table(mine[["Path", "Packets", "Delivered", "Dropped"]])


def workspace_empty(initial) -> None:
    c1, c2 = st.columns([7.2, 3.6], gap="medium")
    with c1:
        pick = inspected()
        routes = []
        frm, to = st.session_state.get("route_from", "stu1"), st.session_state.get("route_to", "portal")
        path = shortest_path(initial, frm, to) if frm != to else None
        if path:
            routes = [("Lowest-cost path", path)]
        fig = topology_figure(initial, routes, 520, focus=st.session_state.get("view_focus", "Whole network"),
                              highlights=[(pick[0], pick[1], "Inspected item")] if pick else [])
        show_chart(fig, f"topo_plot_{st.session_state['plot_gen']}", on_select="rerun", selection_mode="points")
        st.caption("This is the starter network before any simulation. Link labels show bandwidth; hover a device or link for details.")
        with st.expander("How to read this network"):
            st.markdown(help_text.network_legend(), unsafe_allow_html=True)
    with c2:
        with st.container(border=True):
            st.markdown("**First action**")
            st.caption("Configure a run in the sidebar and press Run simulation, or start with the default.")
            if st.button("Run the default congested scenario", type="primary", width="stretch"):
                request_action(("custom",))
                st.rerun()  # a full rerun, so main() runs the request before any widgets are drawn
            st.caption("The default offers more traffic than the 10 Mbps r1-r2 link can carry, so its queue fills and packets are dropped.")
            st.markdown("**Explore the network**")
            nodes = list(initial.nodes)
            st.selectbox("Route from", nodes, key="route_from")
            st.selectbox("Route to", nodes, key="route_to")
            if path:
                info = route_summary(initial, path)
                st.caption(" → ".join(path) + f" · cost {info['cost']:g}, slowest link {info['slowest_link_mbps']:g} Mbps")
            options = ["None"] + [f"Device: {n}" for n in initial.nodes] + [f"Link: {l}" for l in initial.links]
            inspect_selector(options)
            pick = inspected()
            if pick:
                for name, value in (node_details(initial, pick[1]) if pick[0] == "node" else link_details(initial, pick[1])):
                    fact(name, value)


# --- analysis views ---


def analysis(view: str, current) -> None:
    if view == "Experiments":
        experiments_view()
        return
    if view == "Event log":
        event_log_view()
        return
    if current is None:
        empty_state("Run a simulation to see its analysis here.")
        return
    _, run, replay = current
    {"Summary": summary_view, "Traffic & QoS": traffic_view, "Performance": performance_view,
     "Failure & routing": failure_view}[view](run, replay)


def metric_guide() -> None:
    """What every important metric means, in what unit, and how to read it (collapsed to keep the metrics prominent)."""
    with st.expander("What do these metrics mean?"):
        st.markdown("| Metric | Meaning | Unit | How to read it |\n|---|---|---|---|\n" + "\n".join(
            f"| **{name}** | {meaning} | {unit} | {reading} |" for name, meaning, unit, reading in help_text.METRICS))


def summary_view(run: ExperimentRun, replay: ReplayIndex) -> None:
    o = run.metrics.overall
    link = run.metrics.links[BOTTLENECK]
    cols = st.columns(4)
    tips = help_text.METRIC_TOOLTIPS
    cols[0].metric("Packets delivered", fmt_pct(o.pdr), help=tips["Packets delivered"])
    cols[1].metric("Average delay", fmt_ms(o.avg_delay_s), help=tips["Average delay"])
    cols[2].metric("Throughput", fmt_mbps(o.throughput_bps), help=tips["Throughput"])
    cols[3].metric("Bottleneck utilization", fmt_pct(link.utilization), help=tips["Bottleneck utilization"])
    metric_guide()
    left, right = st.columns([3, 2], gap="large")
    with left:
        section("What this run measured")
        for line in run_observations(run):
            st.markdown(f"- {line}")
        st.caption("Configuration: " + config_summary(run))
    with right:
        section("Packet outcomes")
        show_chart(outcomes_chart({run.label: run.metrics}), "summary_outcomes")


def traffic_view(run: ExperimentRun, replay: ReplayIndex) -> None:
    left, right = st.columns([3, 2], gap="large")
    with left:
        section("Workload")
        show_table(workload_table(run.config))
        capacity = run.result.topology.links[BOTTLENECK_LINK_ID].bandwidth_bps
        st.caption(f"Offered load {offered_load_bps(run.config) / 1e6:.1f} Mbps into the r1-r2 bottleneck of {capacity / 1e6:g} Mbps. "
                   "All flows are datagrams (no retransmission).")
    with right:
        section("Queue policies")
        st.markdown(
            "- **FIFO** serves packets in arrival order, regardless of class.\n"
            "- **Strict priority** serves realtime, then interactive, then bulk, and FIFO within a class. "
            "It never interrupts a packet already being transmitted.\n"
            "- Both use the same finite, drop-tail queue: when it is full the arriving packet is dropped."
        )
    classes = run_class_table(run.metrics, run.label)
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        section("Delivery by traffic class")
        show_table(class_delivery_view(classes).drop(columns=["Run"]))
    with c2:
        section("Delay by traffic class")
        show_chart(delay_by_class_chart({run.label: run.metrics}), "traffic_delay")
    section("Queueing and starvation indicators")
    show_table(class_queueing_view(classes).drop(columns=["Run"]))
    st.caption("Mean queueing delay is per hop. Still waiting and longest wait are project-defined indicators of how long packets of a "
               "class wait for service, not a standard fairness metric; still waiting is a snapshot at the end of the run, so compare "
               "classes within a run and the same class between runs. Use Experiments, preset B, to compare FIFO with strict priority.")


def performance_view(run: ExperimentRun, replay: ReplayIndex) -> None:
    metric_guide()
    duration = run.config.duration_s
    interval = duration / 20
    c1, c2 = st.columns(2, gap="large")
    with c1:
        section("Throughput over time")
        show_chart(throughput_chart({run.label: throughput_over_time(run.result, interval)}, interval, duration), "perf_throughput")
    with c2:
        section("Bottleneck queue occupancy (r1 to r2)")
        show_chart(queue_chart({run.label: queue_length_series(run.result, BOTTLENECK[0], BOTTLENECK[1])}, duration, "r1 to r2"), "perf_queue")
    section("Link utilization and queues (directions that carried traffic)")
    links = link_metrics_table(run.metrics, run.result.topology)
    show_table(links)
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button("Download link statistics (CSV)", to_csv_bytes(links), file_name="run_links.csv", mime="text/csv", width="stretch")
    d2.download_button("Download class results (CSV)", to_csv_bytes(run_class_table(run.metrics, run.label)),
                       file_name="run_classes.csv", mime="text/csv", width="stretch")


def failure_view(run: ExperimentRun, replay: ReplayIndex) -> None:
    with st.expander("How the engine treats packets when something fails", expanded=not run.config.failure_events):
        st.markdown(
            "- A packet's path is fixed when it is created and is never recomputed.\n"
            "- A packet **already transmitting** on a link that fails finishes that hop and continues.\n"
            "- Packets **waiting in the queue** of a failed link are dropped at the moment of failure (flushed).\n"
            "- A packet that later **reaches a node whose next link is down**, or a failed router, is dropped there.\n"
            "- Packets **created after** the failure are routed on the updated network; if no path remains they are dropped at creation (no route)."
        )
    if not run.config.failure_events:
        empty_state("No failure was injected in this run. Add one in the sidebar, or open Experiments, preset D, which compares a redundant "
                    "link with a bridge link.")
        return
    section("What happened to packets at each failure")
    show_table(failure_breakdown(replay))
    section("Routes the flows actually used")
    show_table(flow_routes_table(replay))
    st.caption("Packets carry no flow ID, so flows with the same source, destination and class share a row. Use the timeline's Failure button "
               "to jump to the moment of failure and Follow a flow to see its route before and after.")


def experiments_view() -> None:
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    preset_id = c1.selectbox("Preset", list(PRESETS), format_func=lambda k: f"{k}. {PRESET_INFO[k].title}", key="preset_id")
    info = PRESET_INFO[preset_id]
    c2.button("Run preset", type="primary", width="stretch", on_click=request_action, args=(("preset", preset_id),))

    guide_ = help_text.PRESET_GUIDE[preset_id]
    st.markdown(f"**Question.** {guide_['question']}")
    st.caption(info.summary)
    a, b, c, d = st.columns([1, 1.25, 1.3, 1.7], gap="medium")
    a.markdown("**Change**")
    a.markdown(info.varies[0].upper() + info.varies[1:])
    b.markdown("**Keep fixed**")
    b.markdown("\n".join(f"- {item}" for item in info.fixed))
    c.markdown("**Observe**")
    c.markdown(guide_["observe"])
    d.markdown("**Interpret**")
    d.markdown(guide_["interpret"])
    st.caption("Presets use their own fixed workload, run length (2 s) and seed (42); the sidebar controls apply to the custom run only.")

    experiment: ExperimentResult | None = st.session_state["presets"].get(preset_id)
    if experiment is None:
        empty_state(f"Press **Run preset** to run preset {preset_id}. Each run takes a second or two.")
        return

    section("What the results show")
    for line in preset_observations(experiment):
        st.markdown(f"- {line}")
    metric_guide()
    section("Configuration of each run")
    show_table(config_table(experiment.runs).drop(columns=["Duration (s)", "Seed"]))
    first = experiment.runs[0].config
    st.caption(f"All runs: {first.duration_s:g} s simulated, seed {first.seed}, "
               "bottleneck is the r1-r2 link, offered load is the sum of the flow rates.")
    draw_comparison_charts(experiment.runs, f"preset_{preset_id}")

    v1, v2 = st.columns([3, 1], vertical_alignment="bottom")
    pick = v1.selectbox("Replay one of these runs on the network workspace", [r.label for r in experiment.runs], key=f"replay_pick_{preset_id}")
    v2.button("Show on workspace", on_click=show_run, args=(f"Preset {preset_id}: {pick}",), width="stretch")

    if experiment.routes:
        section("Routing behavior (paths before and after the failure)")
        show_table(routes_table(experiment))
        st.caption("This table shows only what routing does to each flow's path. What happened to the packets is below.")
    section("Outcome counts and ratios" + (" (what happened to packets)" if experiment.routes else ""))
    show_table(outcomes_table(experiment))
    section("Delay, throughput and bottleneck")
    show_table(performance_table(experiment))
    section("Results by traffic class")
    classes = class_table(experiment)
    show_table(class_delivery_view(classes))
    section("Queueing and starvation indicators by traffic class")
    show_table(class_queueing_view(classes))
    st.caption("Starvation indicators are project-defined (see Traffic & QoS); still waiting is a snapshot at the end of the run.")
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button("Download run summary (CSV)", to_csv_bytes(experiment_export(experiment)),
                       file_name=f"preset_{preset_id}_summary.csv", mime="text/csv", width="stretch")
    d2.download_button("Download class results (CSV)", to_csv_bytes(class_table(experiment)),
                       file_name=f"preset_{preset_id}_classes.csv", mime="text/csv", width="stretch")


def draw_comparison_charts(runs: list[ExperimentRun], key: str) -> None:
    metrics = {run.label: run.metrics for run in runs}
    duration = runs[0].config.duration_s
    interval = duration / 20
    c1, c2 = st.columns(2, gap="large")
    with c1:
        section("Packet outcomes")
        show_chart(outcomes_chart(metrics), f"{key}_outcomes")
    with c2:
        section("Delay by traffic class")
        show_chart(delay_by_class_chart(metrics), f"{key}_delay")
    c3, c4 = st.columns(2, gap="large")
    with c3:
        section("Throughput over time")
        series = {run.label: throughput_over_time(run.result, interval) for run in runs}
        show_chart(throughput_chart(series, interval, duration), f"{key}_throughput")
    with c4:
        section("Bottleneck queue occupancy (r1 to r2)")
        series_q = {run.label: queue_length_series(run.result, BOTTLENECK[0], BOTTLENECK[1]) for run in runs}
        show_chart(queue_chart(series_q, duration, "r1 to r2"), f"{key}_queue")


def event_log_view() -> None:
    sources = {label: run.result for label, run in run_sources().items()}
    if not sources:
        empty_state("Run a simulation or a preset to see its event log.")
        return
    choice = st.selectbox("Run", list(sources), key="log_source")
    result = sources[choice]
    frames = st.session_state["frames"]
    if choice not in frames:
        frames[choice] = event_log_frame(result)
    frame = frames[choice]
    if frame.empty:
        empty_state("This run generated no packets, so its log is empty.")
        return

    present = [e for e in EVENT_DESCRIPTIONS if e in set(frame["Event"])]
    f1, f2, f3 = st.columns(3)
    events = f1.multiselect("Event type", present, format_func=lambda e: f"{e}: {EVENT_DESCRIPTIONS[e]}", key="log_events")
    classes = f2.multiselect("Traffic class", sorted(set(frame["Class"]) - {""}), key="log_classes")
    reasons = f3.multiselect("Drop reason", sorted(set(frame["Drop reason"]) - {""}), key="log_reasons")
    g1, g2, g3 = st.columns(3)
    packet = g1.number_input("Packet ID (0 = any)", min_value=0, step=1, value=0, key="log_packet")
    location = g2.text_input("Link or node contains", key="log_location", placeholder="e.g. r1-r2")
    end = float(result.duration_s)
    window = g3.slider("Time window (s)", 0.0, end, (0.0, end), step=max(end / 200, 0.001), key="log_time")

    filtered = filter_event_log(frame, events, classes, reasons, int(packet) or None, location, window)
    st.caption(f"{len(filtered):,} of {len(frame):,} events match" + (f"; showing the first {MAX_LOG_ROWS:,}." if len(filtered) > MAX_LOG_ROWS else "."))
    show_table(filtered.head(MAX_LOG_ROWS), formats={"Time (s)": "%.4f"}, height=420)
    st.download_button("Download filtered log (CSV)", to_csv_bytes(filtered), file_name="event_log.csv", mime="text/csv")


def main() -> None:
    init_state()
    run_clicked = sidebar()
    action = st.session_state.pop("pending_action", None)
    if run_clicked or action == ("custom",):
        execute_custom_run()
    elif action and action[0] == "preset":
        execute_preset(action[1])
    header()
    guide()
    workspace()
    st.divider()
    view = st.segmented_control("Analysis", ANALYSIS_VIEWS, default="Summary", key="analysis_view", label_visibility="collapsed") or "Summary"
    analysis(view, viewed())


main()
