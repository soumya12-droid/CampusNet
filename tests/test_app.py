"""Headless runs of the Streamlit app (streamlit.testing). They execute the real app.py and its widgets."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from campusnet import help_text

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def new_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=180).run()


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def run_custom(at: AppTest) -> AppTest:
    return button(at, "Run simulation").click().run()


def replay(at: AppTest, label: str = "Custom run"):
    return at.session_state["replays"][label][1]


def text_of(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


def view(at: AppTest, name: str) -> AppTest:
    at.segmented_control(key="analysis_view").set_value(name)
    return at.run()


def with_failure(link: str) -> AppTest:
    at = new_app()
    at.selectbox(key="failure_kind").set_value("Link").run()
    at.selectbox(key="fail_link").set_value(link)
    return run_custom(at)


# --- guide, help and explanations ---


def test_the_start_here_guide_is_shown_with_five_steps_and_the_scope() -> None:
    at = new_app()
    body = text_of(at)
    assert "Start here" in body and "five steps" in body
    for n, (title, _) in enumerate(help_text.GUIDE_STEPS, 1):
        assert f"{n}. {title}" in body
    assert help_text.SCOPE in body and "Hide guide" in [b.label for b in at.button]


def test_the_guide_can_be_dismissed_and_reopened_and_survives_runs_and_reset() -> None:
    at = button(new_app(), "Hide guide").click().run()
    assert at.session_state["guide_open"] is False and "five steps" not in text_of(at)
    assert "Show guide" in [b.label for b in at.button] and "Hide guide" not in [b.label for b in at.button]
    at = run_custom(at)
    assert at.session_state["guide_open"] is False and not at.exception  # running does not bring it back
    at = button(at, "Reset").click().run()
    assert at.session_state["guide_open"] is False  # a preference, not part of the run
    at = button(at, "Show guide").click().run()
    assert at.session_state["guide_open"] is True and "five steps" in text_of(at) and "Show guide" not in [b.label for b in at.button]


def test_sidebar_controls_carry_their_help_text() -> None:
    at = new_app()
    h = help_text.CONTROL_HELP
    assert at.number_input(key="duration").help == h["duration"] and at.number_input(key="seed").help == h["seed"]
    assert at.slider(key="intensity").help == h["intensity"] and at.number_input(key="capacity").help == h["capacity"]
    assert at.number_input(key="bottleneck").help == h["bottleneck"] and at.radio(key="policy").help == h["policy"]
    assert at.selectbox(key="failure_kind").help == h["failure_kind"]
    assert help_text.FAILURE_NOTE not in text_of(at)  # failure explanations appear only when a failure is chosen
    at.selectbox(key="failure_kind").set_value("Link").run()
    assert help_text.FAILURE_NOTE in text_of(at) and help_text.PATH_NOTE in text_of(at)
    assert at.number_input(key="fail_time").help == h["fail_time"]


def test_the_workspace_explains_the_network_timeline_and_inspector() -> None:
    at = run_custom(new_app())
    body = text_of(at)
    assert help_text.INSPECTOR_HELP["Event"] in body and "Virtual clock" in body and "circle = client" in body
    labels = [e.label for e in at.expander]
    assert "How to read this network" in labels and "How the timeline works" in labels
    at.segmented_control(key="inspector_view").set_value("Route").run()
    assert help_text.INSPECTOR_HELP["Route"] in text_of(at) and help_text.PATH_NOTE in text_of(at)
    at.segmented_control(key="inspector_view").set_value("Event").run()
    button(at, "First drop").click().run()
    assert help_text.DROP_NOTE in text_of(at)  # a drop event comes with the reminder that a valid route can still lose packets


def test_the_empty_workspace_still_explains_the_picture() -> None:
    at = new_app()
    assert "How to read this network" in [e.label for e in at.expander] and "First action" in text_of(at)


def test_metric_help_is_attached_to_the_headline_metrics_and_a_guide_is_available() -> None:
    at = run_custom(new_app())
    assert {m.label: m.help for m in at.metric} == help_text.METRIC_TOOLTIPS
    assert "What do these metrics mean?" in [e.label for e in at.expander]
    assert "not a standard fairness index" in text_of(at)


def test_every_preset_shows_question_change_fixed_observe_interpret() -> None:
    at = view(new_app(), "Experiments")
    for preset_id in ("A", "B", "C", "D", "E"):
        at.selectbox(key="preset_id").set_value(preset_id).run()
        body = text_of(at)
        guide = help_text.PRESET_GUIDE[preset_id]
        for heading in ("Question.", "Change", "Keep fixed", "Observe", "Interpret"):
            assert heading in body, (preset_id, heading)
        assert guide["question"] in body and guide["observe"] in body and guide["interpret"] in body


def test_there_is_no_guided_experiment_panel() -> None:
    at = run_custom(new_app())
    assert not any("Guided experiment" in e.label for e in at.expander) and not at.exception
    assert "run_history" not in at.session_state


# --- first run, running, resetting ---


def test_first_run_experience_explains_how_to_start() -> None:
    at = new_app()
    assert not at.exception
    assert "Start here" in text_of(at)
    assert "Run the default congested scenario" in [b.label for b in at.button]
    assert at.session_state["custom"] is None and at.session_state["view_source"] is None
    assert at.segmented_control(key="analysis_view").value == "Summary"
    assert any("Run a simulation to see its analysis" in i.value for i in at.info)
    assert not at.sidebar.error  # the default configuration is valid


def test_default_run_opens_the_workspace_at_the_end_of_the_run() -> None:
    at = run_custom(new_app())
    assert not at.exception
    r = replay(at)
    assert at.session_state["view_source"] == "Custom run" and at.session_state["evt_idx"] == len(r) - 1
    assert "Viewing Custom run" in text_of(at) and f"{len(r):,} recorded events" in text_of(at)
    assert [m.label for m in at.metric] == ["Packets delivered", "Average delay", "Throughput", "Bottleneck utilization"]


def test_the_start_here_button_runs_the_default_scenario() -> None:
    at = button(new_app(), "Run the default congested scenario").click().run()
    assert not at.exception and at.session_state["custom"] is not None
    assert at.session_state["custom"]["metrics"].overall.generated == 2400


def test_the_ui_reports_the_same_numbers_as_the_simulation() -> None:
    at = run_custom(new_app())
    overall = at.session_state["custom"]["metrics"].overall
    shown = {m.label: m.value for m in at.metric}
    assert shown["Packets delivered"] == f"{overall.pdr * 100:.1f}%"
    assert shown["Average delay"] == f"{overall.avg_delay_s * 1000:.2f} ms"
    assert shown["Throughput"] == f"{overall.throughput_bps / 1e6:.2f} Mbps"
    assert f"{overall.generated:,} packets were generated" in text_of(at)


def test_reset_clears_the_run_the_timeline_and_the_selections() -> None:
    at = new_app()
    at.number_input(key="capacity").set_value(7).run()
    at = run_custom(at)
    button(at, "Next").click().run()
    at = button(at, "Reset").click().run()
    s = at.session_state
    assert s["custom"] is None and s["view_source"] is None and s["capacity"] == 50 and s["evt_idx"] == -1
    assert s["followed_packet"] is None and s["inspect_item"] == "None" and s["presets"] == {}
    assert not at.exception and "Start here" in text_of(at)


def test_rerunning_replaces_the_previous_run_and_returns_to_its_end() -> None:
    at = run_custom(new_app())
    button(at, "Start").click().run()
    at.number_input(key="capacity").set_value(5).run()
    at = run_custom(at)
    assert not at.exception
    assert at.session_state["evt_idx"] == len(replay(at)) - 1
    assert {l.queue_capacity_packets for l in at.session_state["custom"]["result"].topology.links.values()} == {5}


# --- scenarios ---


def test_strict_priority_scenario_uses_the_selected_policy() -> None:
    fifo = run_custom(new_app()).session_state["custom"]
    at = new_app()
    at.radio(key="policy").set_value("strict_priority")
    at = run_custom(at)
    priority = at.session_state["custom"]
    assert not at.exception and "policy: Strict priority" in text_of(at)
    assert priority["config"].flows == fifo["config"].flows and priority["config"].queue_policy == "strict_priority"
    assert priority["metrics"].by_class["realtime"].avg_delay_s != fifo["metrics"].by_class["realtime"].avg_delay_s


def test_redundant_link_failure_can_be_jumped_to_and_explained() -> None:
    at = with_failure("r1-r2")
    assert not at.exception
    r = replay(at)
    button(at, "Failure").click().run()
    assert at.session_state["evt_idx"] == r.first("Failures")
    assert "Link r1-r2 failed" in text_of(at)
    summary = r.failure_summary()[0]
    assert summary["flushed"] > 0 and summary["no_route"] == 0  # a backup path exists
    at = view(at, "Failure & routing")
    assert not at.exception and len(at.dataframe) >= 2


def test_bridge_link_failure_leaves_the_server_unreachable() -> None:
    at = with_failure("r2-portal")
    r = replay(at)
    row = r.failure_summary()[0]
    assert row["no_route"] > 0
    routes = {u.path for uses in r.flow_routes().values() for u in uses}
    assert () in routes  # packets for the portal had no route after the failure
    at = view(at, "Failure & routing")
    assert not at.exception


def test_queue_capacity_scenario_is_visible_in_the_replayed_queues() -> None:
    at = new_app()
    at.number_input(key="capacity").set_value(5).run()
    at = run_custom(at)
    r = replay(at)
    busiest = max(len(port.waiting) for i in range(0, len(r), 200) for port in r.snapshot(i).ports.values())
    assert 0 < busiest <= 5


# --- timeline and event selection ---


def test_timeline_buttons_move_between_events() -> None:
    at = run_custom(new_app())
    r = replay(at)
    at = button(at, "Start").click().run()
    assert at.session_state["evt_idx"] == -1 and at.session_state["tl_time"] == 0.0
    assert "Before the first event" in text_of(at)
    at = button(at, "Next").click().run()
    assert at.session_state["evt_idx"] == 0
    assert "was created at" in text_of(at) and at.session_state["tl_time"] == r.records[0].timestamp_s
    at = button(at, "Previous").click().run()
    assert at.session_state["evt_idx"] == -1
    at = button(at, "End").click().run()
    assert at.session_state["evt_idx"] == len(r) - 1 and not at.exception


def test_step_filter_jumps_between_drops() -> None:
    at = run_custom(new_app())
    r = replay(at)
    drops = r.indices("Drops")
    at = button(at, "Start").click().run()
    at.selectbox(key="step_filter").set_value("Drops").run()
    at = button(at, "Next").click().run()
    assert at.session_state["evt_idx"] == drops[0]
    assert "waiting queue was full" in text_of(at)
    at = button(at, "Next").click().run()
    assert at.session_state["evt_idx"] == drops[1]
    at = button(at, "Previous").click().run()
    assert at.session_state["evt_idx"] == drops[0]


def test_the_status_line_uses_the_singular_for_one_packet() -> None:
    at = run_custom(new_app())
    r = replay(at)
    one = next(i for i in range(len(r)) if r.snapshot(i).in_network == 1)
    at = button(at, "Start").click().run()
    for _ in range(one + 1):  # step event by event to the first moment with exactly one packet in the network
        at = button(at, "Next").click().run()
        if at.session_state["evt_idx"] == one:
            break
    line = next(c.value for c in at.caption if c.value.startswith("Event "))
    assert "1 packet in the network" in line and "1 packets" not in line


def test_jump_buttons_go_to_real_events() -> None:
    at = run_custom(new_app())
    r = replay(at)
    for label, name in [("First drop", "Drops"), ("First delivery", "Deliveries"), ("First transmission", "Transmissions")]:
        at = button(at, label).click().run()
        assert at.session_state["evt_idx"] == r.first(name), label
    assert button(at, "Failure").disabled  # this run has no failure


def test_the_time_slider_selects_the_last_event_at_or_before_that_time() -> None:
    at = run_custom(new_app())
    r = replay(at)
    at.slider(key="tl_time").set_value(0.5).run()
    assert not at.exception
    assert at.session_state["evt_idx"] == r.index_at_time(0.5)
    assert r.records[at.session_state["evt_idx"]].timestamp_s <= 0.5


def test_timeline_status_line_reports_the_snapshot() -> None:
    at = run_custom(new_app())
    r = replay(at)
    at.slider(key="tl_time").set_value(0.3).run()
    snap = r.snapshot(at.session_state["evt_idx"], 0.3)
    waiting = sum(len(p.waiting) for p in snap.ports.values())
    line = next(c.value for c in at.caption if c.value.startswith("Event "))
    assert f"{snap.in_network:,} packets in the network ({waiting:,} waiting in queues)" in line
    assert f"{snap.delivered:,} delivered" in line and f"{snap.dropped:,} dropped so far" in line


# --- flows, packets, devices ---


def test_following_a_flow_before_and_after_it_exists() -> None:
    at = with_failure("r1-r2")
    button(at, "Start").click().run()
    at.selectbox(key="view_flow").set_value("download-1").run()
    assert not at.exception and "No packet of this flow has been created yet" in text_of(at)
    at = button(at, "End").click().run()
    assert "No packet of this flow has been created yet" not in text_of(at)
    uses = replay(at).flow_routes()["download-1"]
    assert len(uses) == 2 and uses[0].path != uses[1].path  # its route really changed, which is what the selector labels


def test_following_a_packet_and_stopping() -> None:
    at = run_custom(new_app())
    at.segmented_control(key="inspector_view").set_value("Packet").run()
    at.number_input(key="packet_widget").set_value(5).run()
    at = button(at, "Follow on the network").click().run()
    assert at.session_state["followed_packet"] == 5 and not at.exception
    assert "Path (fixed at creation)" in text_of(at)
    at = button(at, "Stop following").click().run()
    assert at.session_state["followed_packet"] is None


def test_following_the_packet_of_an_event_opens_its_details() -> None:
    at = run_custom(new_app())
    at = button(at, "First delivery").click().run()
    pid = replay(at).records[at.session_state["evt_idx"]].packet_id
    at = next(b for b in at.button if b.label.startswith("Follow packet")).click().run()
    assert at.session_state["followed_packet"] == pid and at.session_state["inspector_view"] == "Packet"


def test_device_and_link_inspector_shows_real_properties() -> None:
    at = run_custom(new_app())
    at.segmented_control(key="inspector_view").set_value("Inspect").run()
    at.selectbox(key="inspect_widget").set_value("Link: r1-r2").run()
    body = text_of(at)
    assert "**Bandwidth:** 10 Mbps" in body and "**Routing cost:** 1" in body and "waiting" in body
    assert at.session_state["inspect_item"] == "Link: r1-r2"
    at.selectbox(key="inspect_widget").set_value("Device: r1").run()
    assert "10.10.0.1" in text_of(at) and not at.exception


def test_the_inspected_item_survives_leaving_the_device_section() -> None:
    # Streamlit discards the state of a widget that was not drawn in the previous run, so the selection
    # must not depend on the selector widget (a real browser run hit this as a KeyError).
    at = run_custom(new_app())
    at.segmented_control(key="inspector_view").set_value("Inspect").run()
    at.selectbox(key="inspect_widget").set_value("Link: r1-r2").run()
    at.segmented_control(key="inspector_view").set_value("Event").run()  # the selector is no longer drawn
    assert not at.exception
    assert "inspect_widget" not in at.session_state  # Streamlit has discarded the undrawn widget's state
    at.segmented_control(key="inspector_view").set_value("Packet").run()
    assert not at.exception and at.session_state["inspect_item"] == "Link: r1-r2"
    at.segmented_control(key="inspector_view").set_value("Inspect").run()
    assert at.selectbox(key="inspect_widget").value == "Link: r1-r2"


def test_packet_choice_survives_leaving_and_returning_to_the_packet_section() -> None:
    at = run_custom(new_app())
    at.segmented_control(key="inspector_view").set_value("Packet").run()
    at.number_input(key="packet_widget").set_value(9).run()
    assert at.session_state["packet_choice"] == 9
    at.segmented_control(key="inspector_view").set_value("Event").run()
    at.segmented_control(key="inspector_view").set_value("Packet").run()
    assert not at.exception and at.number_input(key="packet_widget").value == 9


def test_the_packet_box_keeps_showing_the_followed_packet_after_visiting_another_section() -> None:
    # Regression: found in the real app. Following packet N, visiting Inspect and coming back reset the box to 1
    # while the picture still followed N.
    at = run_custom(new_app())
    at = button(at, "First drop").click().run()
    pid = replay(at).records[at.session_state["evt_idx"]].packet_id
    at = next(b for b in at.button if b.label.startswith("Follow packet")).click().run()
    assert at.session_state["followed_packet"] == pid and at.number_input(key="packet_widget").value == pid
    at.segmented_control(key="inspector_view").set_value("Inspect").run()
    at.segmented_control(key="inspector_view").set_value("Packet").run()
    assert at.number_input(key="packet_widget").value == pid == at.session_state["followed_packet"]


def test_first_run_state_lets_you_inspect_and_route_without_a_run() -> None:
    at = new_app()
    at.selectbox(key="inspect_widget").set_value("Device: portal").run()
    assert "10.10.3.10" in text_of(at) and not at.exception
    at.selectbox(key="route_from").set_value("stu3").run()
    assert not at.exception and "stu3" in text_of(at)


# --- analysis, presets, logs, larger runs ---


def test_every_analysis_view_renders_for_a_run() -> None:
    at = run_custom(new_app())
    for name in ["Summary", "Traffic & QoS", "Performance", "Failure & routing", "Experiments", "Event log"]:
        at = view(at, name)
        assert not at.exception, name


def test_every_preset_runs_and_states_what_the_results_show() -> None:
    at = new_app()
    at = view(at, "Experiments")
    for preset_id in ("A", "B", "C", "D", "E"):
        at.selectbox(key="preset_id").set_value(preset_id).run()
        at = button(at, "Run preset").click().run()
        assert not at.exception, preset_id
        experiment = at.session_state["presets"][preset_id]
        assert experiment.experiment_id == preset_id and experiment.runs
        assert "What the results show" in text_of(at) and "What it shows" in text_of(at), preset_id
        assert at.session_state["view_source"] == f"Preset {preset_id}: {experiment.runs[-1].label}"
    assert sorted(at.session_state["presets"]) == ["A", "B", "C", "D", "E"]


def test_a_preset_run_can_be_replayed_on_the_workspace() -> None:
    at = view(new_app(), "Experiments")
    at.selectbox(key="preset_id").set_value("B").run()
    at = button(at, "Run preset").click().run()
    at.selectbox(key="replay_pick_B").set_value("fifo").run()
    at = button(at, "Show on workspace").click().run()
    assert at.session_state["view_source"] == "Preset B: fifo" and not at.exception
    assert at.session_state["evt_idx"] == len(replay(at, "Preset B: fifo")) - 1


def test_switching_the_run_on_screen_resets_the_timeline_to_that_runs_end() -> None:
    at = view(new_app(), "Experiments")
    at.selectbox(key="preset_id").set_value("B").run()
    at = button(at, "Run preset").click().run()
    at.selectbox(key="view_source").set_value("Preset B: fifo").run()
    assert not at.exception and at.session_state["view_source"] == "Preset B: fifo"
    r = replay(at, "Preset B: fifo")
    assert at.session_state["evt_idx"] == len(r) - 1


def test_event_log_filters_and_lists_available_runs() -> None:
    at = view(run_custom(new_app()), "Event log")
    assert at.selectbox(key="log_source").options == ["Custom run"]
    at.multiselect(key="log_events").set_value(["dropped"]).run()
    assert not at.exception
    caption = next(c.value for c in at.caption if "events match" in c.value)
    dropped = at.session_state["custom"]["metrics"].overall.dropped
    assert caption.startswith(f"{dropped:,} of ")
    at.multiselect(key="log_events").set_value([]).run()
    at.text_input(key="log_location").set_value("zzz-no-such-place").run()
    assert next(c.value for c in at.caption if "events match" in c.value).startswith("0 of ")


def test_failure_time_follows_a_shorter_duration_without_error() -> None:
    at = new_app()
    at.selectbox(key="failure_kind").set_value("Router").run()
    at.number_input(key="fail_time").set_value(1.5).run()
    at.number_input(key="duration").set_value(0.5).run()
    assert not at.exception and at.session_state["fail_time"] <= 0.5


def test_a_longer_run_with_many_events_still_replays() -> None:
    at = new_app()
    at.number_input(key="duration").set_value(6.0).run()
    at = run_custom(at)
    r = replay(at)
    assert not at.exception and len(r) > 60_000
    at.slider(key="tl_time").set_value(3.0).run()
    assert not at.exception and at.session_state["evt_idx"] == r.index_at_time(3.0)

