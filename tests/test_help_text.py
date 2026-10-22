import re

import pytest

from campusnet import help_text as h
from campusnet import simulator, theme
from campusnet.experiments import PRESETS, base_config, run_preset
from campusnet.metrics import calculate_metrics
from campusnet.presentation import (
    PRESET_INFO,
    class_table,
    link_metrics_table,
    outcomes_table,
    performance_table,
    run_class_table,
)
from campusnet.replay import ReplayIndex
from campusnet.simulator import run_simulation

JUDGEMENT = re.compile(r"\b(better|worse|best|worst|superior|optimal|always|never fails)\b", re.I)


# --- the explanations are true of the simulator ---


def test_the_seed_really_has_no_effect_so_the_help_says_so() -> None:
    a = run_simulation(base_config(duration_s=1.0, seed=1))
    b = run_simulation(base_config(duration_s=1.0, seed=9999))
    assert a.records == b.records and [p.status for p in a.packets] == [p.status for p in b.packets]
    text = h.CONTROL_HELP["seed"].lower()
    assert "deterministic" in text and "no random" in text and "does not change" in text
    assert "no effect" in (simulator.__doc__ or "")  # the engine documents the same fact


def test_every_sidebar_control_has_concise_help() -> None:
    assert set(h.CONTROL_HELP) == {"duration", "intensity", "seed", "bottleneck", "capacity", "policy", "failure_kind", "fail_time"}
    for key, text in h.CONTROL_HELP.items():
        assert 30 < len(text) < 260, key  # a sentence or two, not a paragraph


def test_control_help_states_the_documented_semantics() -> None:
    t = h.CONTROL_HELP
    assert "not wall-clock" in t["duration"] and "reference load" in t["intensity"]
    assert "not counted" in t["capacity"] and "dropped" in t["capacity"]
    assert "arrival order" in t["policy"] and "FIFO within a class" in t["policy"] and "never interrupted" in t["policy"]
    assert "r1-r2" in t["bottleneck"] and "transmit" in t["bottleneck"]
    note = h.FAILURE_NOTE.lower()
    assert "finishes that hop" in note and "waiting" in note and "dropped" in note and "no_route" in note
    assert "fixed when it is created" in h.PATH_NOTE and "valid" in h.PATH_NOTE


def test_failure_explanations_agree_with_the_replay_wording() -> None:
    from campusnet.config import FailureEvent

    config = base_config(duration_s=1.0, intensity=0.4)
    config.failure_events = [FailureEvent(0.5, "link", "r2-portal")]
    replay = ReplayIndex(run_simulation(config))
    texts = " ".join(replay.explain(i) for i in replay.indices("Drops") + replay.indices("Failures"))
    assert "fixed when it is created" in texts and "finishes that hop" in replay.explain(replay.first("Failures"))
    assert "no path" in texts  # the no_route drop
    assert "no_route" in h.DROP_NOTE and "failed next hop" in h.DROP_NOTE and "waiting" in h.DROP_NOTE


def test_scope_and_guide_steps() -> None:
    assert "discrete-event" in h.SCOPE and "does not monitor a real campus network" in h.SCOPE and "real packets" in h.SCOPE
    assert [t for t, _ in h.GUIDE_STEPS] == ["Configure", "Run", "Explore", "Step through", "Compare"]
    assert all(len(text) < 140 for _, text in h.GUIDE_STEPS)  # short explanations, not a tutorial


def test_network_legend_uses_the_real_palette_and_only_working_interactions() -> None:
    legend = h.network_legend()
    for color in (theme.ORANGE, theme.AMBER, theme.RED, theme.GREEN):
        assert color in legend
    for word in ("circle = client", "diamond = switch", "square = router", "hexagon = server", "bandwidth", "badge", "Dashed red",
                 "Click a device or link", "Follow a flow", "Focus"):
        assert word in legend
    assert "drag" not in legend.lower() and "zoom" not in legend.lower()  # the figure cannot be dragged or zoomed


def test_timeline_help_covers_every_control_and_the_replay_distinction() -> None:
    text = h.TIMELINE_HELP
    for part in ("Virtual clock", "wall-clock", "Replay, not a live run", "Start / Previous / Next / End", "Step through",
                 "Jump buttons", "Event list", "Slider"):
        assert part in text
    assert "nothing is simulated again" in text


def test_inspector_help_covers_every_section() -> None:
    assert set(h.INSPECTOR_HELP) == {"Event", "Packet", "Route", "Inspect"}
    assert "never recomputed" in h.INSPECTOR_HELP["Route"] and "fixed path" in h.INSPECTOR_HELP["Packet"]


# --- metrics ---


def test_metric_guide_covers_every_important_metric_with_unit_and_reading() -> None:
    names = " ".join(n for n, *_ in h.METRICS)
    for term in ("delivery ratio", "loss ratio", "Unresolved", "delay", "Throughput", "utilization", "Queue length", "Queueing delay"):
        assert term in names
    for name, meaning, unit, reading in h.METRICS:
        assert meaning and unit and reading, name
    body = " ".join(m + r for _, m, _, r in h.METRICS)
    assert "not drops" in body and "Busy time, not success" in body and "not a standard fairness index" in body
    assert "snapshot" in body


def test_every_column_help_key_is_a_real_table_column() -> None:
    b = run_preset("B", duration_s=1.0)
    custom = run_simulation(base_config(duration_s=1.0))
    metrics = calculate_metrics(custom)
    columns: set[str] = set()
    for frame in (outcomes_table(b), performance_table(b), class_table(b), run_class_table(metrics),
                  link_metrics_table(metrics, custom.topology)):
        columns |= set(frame.columns)
    assert set(h.COLUMN_HELP) <= columns, set(h.COLUMN_HELP) - columns


def test_metric_tooltips_match_the_headline_metric_labels() -> None:
    assert set(h.METRIC_TOOLTIPS) == {"Packets delivered", "Average delay", "Throughput", "Bottleneck utilization"}
    assert "Busy time" in h.METRIC_TOOLTIPS["Bottleneck utilization"] and "not counted as lost" in h.METRIC_TOOLTIPS["Packets delivered"]


# --- presets ---


def test_every_preset_has_question_observe_and_interpret() -> None:
    assert set(h.PRESET_GUIDE) == set(PRESETS) == set(PRESET_INFO)
    for key, guide in h.PRESET_GUIDE.items():
        assert set(guide) == {"question", "observe", "interpret"}
        assert guide["question"].endswith("?") and guide["observe"] and guide["interpret"]
        assert not JUDGEMENT.search(" ".join(guide.values())), key
        assert PRESET_INFO[key].fixed and PRESET_INFO[key].varies  # "change" and "keep fixed" come from the real configuration


def test_preset_guidance_names_things_that_exist_in_the_results() -> None:
    d = run_preset("D", duration_s=1.0)
    reasons = {k for row in d.rows() for k in row if k.startswith("drops_")}
    assert {"drops_link_down", "drops_no_route"} <= reasons and "link_down" in h.PRESET_GUIDE["D"]["observe"]
    assert len(run_preset("A", duration_s=1.0).runs) == 2 and "two runs" in PRESET_INFO["A"].how_to_read.lower()
    assert "bulk" in h.PRESET_GUIDE["B"]["observe"] and "traffic mix" in h.PRESET_GUIDE["B"]["interpret"]  # a trade-off, not a winner


def test_help_text_has_no_hard_coded_colors() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "campusnet" / "help_text.py").read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", source)
