"""Short explanations shown in the UI.

Static wording follows the engine's documented behaviour (see campusnet/simulator.py and
campusnet/replay.py); tests check the two stay in agreement.
"""

from campusnet import theme

SCOPE = (
    "CampusNet simulates a campus network with a discrete-event model. Everything is simulated inside the program: "
    "it does not monitor a real campus network or capture real packets."
)

GUIDE_STEPS = [
    ("Configure", "Set a custom run in the sidebar, or pick an experiment (A–E) under Experiments."),
    ("Run", "Press Run simulation or Run preset. The run completes at once, in virtual time."),
    ("Explore", "Click a device or link, follow a flow, or follow a packet. The inspector shows what you select."),
    ("Step through", "Use the timeline to replay the recorded events: Previous, Next, the slider, and the jump buttons."),
    ("Compare", "Read the measured results below the network and explain what happened, using the packet and event details."),
]

# --- controls ---

CONTROL_HELP = {
    "duration": "How long the virtual clock runs, in simulated seconds. It is not wall-clock time: a longer run needs more computing, not more waiting.",
    "intensity": "How fast the four flows generate packets, relative to a reference load of about 11.9 Mbps. 1.0 is the reference; 0.4 is 40% of it. Higher values create more congestion.",
    "seed": "Recorded with the run for reproducibility. This simulator is deterministic and uses no random numbers yet, so changing the seed does not change the results.",
    "bottleneck": "Capacity of the r1-r2 core link, which carries every flow. A lower capacity makes each packet take longer to transmit, so queues fill sooner.",
    "capacity": "The most packets that may wait in each directed link's queue. The packet being transmitted is not counted. A packet that arrives at a full queue is dropped.",
    "policy": "FIFO: packets are served in arrival order. Strict priority: waiting realtime packets are served first, then interactive, then bulk, FIFO within a class. A packet already transmitting is never interrupted.",
    "failure_kind": "Break one link or router partway through the run, to see how routing and packets respond.",
    "fail_time": "When the failure happens, in simulated seconds from the start of the run.",
}

FAILURE_NOTE = (
    "At the failure time: a packet already transmitting on the failed link finishes that hop; packets waiting in its queue are dropped; "
    "packets created later are routed around it if a path exists, otherwise dropped at creation (no_route)."
)

PATH_NOTE = (
    "A packet's path is fixed when it is created. The path can be valid then and still fail later: "
    "if a link on it goes down, the packet is dropped when it reaches that point."
)

# --- reading the network ---


def network_legend() -> str:
    """Markdown explaining the picture, using the app's actual palette."""
    def swatch(color: str, text: str) -> str:
        return f'<span style="color:{color};font-weight:600">{text}</span>'

    return "\n".join([
        "- **Devices:** circle = client, diamond = switch, square = router, hexagon = server. Colour supports the shape; it is not the only cue.",
        "- **Links:** the label is the link's bandwidth. Hover a link or device for delay, routing cost, queue size and address.",
        f"- {swatch(theme.ORANGE, 'Orange line with arrows')}: the route you are following (a flow's path, or one packet's fixed path). Arrows show the direction of travel.",
        f"- {swatch(theme.AMBER, 'Amber link')}: a queue on it is at least half full (brighter at 90%). The badge, such as 47/50, is the packets waiting out of the queue capacity, next to the sending end.",
        f"- {swatch(theme.RED, 'Dashed red')}: a failed link or router at this moment in the run. A red X marks where a packet was dropped.",
        f"- {swatch(theme.AMBER, 'Amber ring')}: the selected event. An off-white ring is the device or link you are inspecting. Small dots are packets on a link.",
        f"- {swatch(theme.GREEN, 'Green')}: servers, and a delivered packet.",
        "- Click a device or link to inspect it. **Follow a flow** draws the routes its packets really took. **Focus** crops the view.",
    ])


INSPECTOR_HELP = {
    "Event": "The selected recorded event, explained in plain language. Move through events with the timeline.",
    "Packet": "One packet's fixed path, waits and outcome. Follow it to see where it is on the network at the chosen moment.",
    "Route": "The paths a flow's packets actually took in this run. A route is chosen when a packet is created and is never recomputed for it.",
    "Inspect": "Properties and live queue state of the device or link you selected.",
}

DROP_NOTE = (
    "A packet can be dropped even though its route was valid when it was created: it may be waiting in a failed link's queue, "
    "or reach a failed next hop later. If no path to the destination exists, a new packet is dropped at creation (no_route)."
)

TIMELINE_HELP = "\n".join([
    "- **Virtual clock:** simulated time, in seconds. Events are processed in time order and the clock jumps to the next event. It is not wall-clock time, so a 2 s run does not take 2 s.",
    "- **Replay, not a live run:** the run has already finished. The slider and buttons inspect its recorded events; nothing is simulated again while you move.",
    "- **Start / Previous / Next / End:** move to the idle network before the run, the previous or next event, or the end of the run.",
    "- **Step through:** restricts Previous and Next to one kind of event, such as drops or deliveries.",
    "- **Jump buttons:** go to the first drop, failure, delivery or transmission of the run. A button is greyed out when the run has none.",
    "- **Event list:** the recorded events around the current one. Click a row to jump to it.",
    "- **Slider:** the last event at or before that time is selected; packet positions between events follow the link delays.",
])

# --- metrics: name -> (definition, unit, how to read it) ---

METRICS = [
    ("Packet delivery ratio (PDR)", "Delivered packets ÷ generated packets.", "%",
     "Higher means more of the offered traffic arrived. Packets still in the network at the end are not counted as delivered or lost."),
    ("Packet loss ratio (PLR)", "Packets explicitly dropped ÷ generated packets.", "%",
     "Drops are counted by reason (queue full, link down, no route). Unresolved packets are not drops."),
    ("Unresolved at the end", "Generated − delivered − dropped: queued or in transit when the run ended.", "packets or %",
     "A packet that has not finished yet is not automatically lost; a longer run might deliver it."),
    ("Average end-to-end delay", "Mean of (delivery time − creation time) over delivered packets.", "ms",
     "Includes queueing, transmission and propagation. Packets that were dropped or unresolved are not in the average."),
    ("Throughput", "Bits of delivered packets ÷ run length.", "Mbps",
     "What actually arrived per second, not what was offered. It cannot exceed the capacity of the bottleneck."),
    ("Link utilization", "Time a link spent transmitting ÷ run length, per direction.", "%",
     "Busy time, not success: a link can be fully busy while packets are dropped at its queue."),
    ("Queue length (average, maximum)", "Packets waiting at a link's output (not the one being transmitted); the average is weighted by time.", "packets",
     "A maximum equal to the queue capacity means the queue filled. A large average means packets waited long."),
    ("Queueing delay, still waiting, longest wait", "Time packets waited for service per hop; packets in a queue at the end; the longest wait.", "ms / packets",
     "Project-defined indicators of how long a class waits, not a standard fairness index. Still waiting is a snapshot at the end of the run."),
]

COLUMN_HELP = {
    "PDR (%)": "Delivered ÷ generated. Unresolved packets are not counted as delivered.",
    "PLR (%)": "Explicitly dropped ÷ generated. Unresolved packets are not drops.",
    "Unresolved": "Still queued or in transit when the run ended. Not automatically lost.",
    "Unresolved (%)": "Share of generated packets still in the network at the end.",
    "Avg delay (ms)": "Mean end-to-end delay of delivered packets: queueing + transmission + propagation.",
    "Throughput (Mbps)": "Delivered bits per second over the run.",
    "Utilization (%)": "Share of the run the link spent transmitting. Busy time, not successful delivery.",
    "Bottleneck util. (%)": "Share of the run the r1 to r2 link spent transmitting. Busy time, not successful delivery.",
    "Avg queue (packets)": "Time-weighted number of packets waiting (not counting the one transmitting).",
    "Max queue (packets)": "Most packets waiting at once. Equal to the capacity means the queue filled.",
    "Bottleneck avg queue (pkts)": "Time-weighted packets waiting at the r1 to r2 link.",
    "Bottleneck max queue (pkts)": "Most packets waiting at once at the r1 to r2 link.",
    "Mean queueing delay per hop (ms)": "Average wait for service per hop. A project-defined indicator.",
    "Still waiting at end (packets)": "Packets in a queue when the run ended: a snapshot, not an average. Project-defined.",
    "Longest wait (ms)": "Longest queueing wait seen, including waits still in progress at the end. Project-defined.",
    "Queue-full drops": "Packets dropped on arrival because the queue was full.",
    "Bottleneck drops (full)": "Packets dropped on arrival at the r1 to r2 queue because it was full.",
}

METRIC_TOOLTIPS = {
    "Packets delivered": "Packet delivery ratio: delivered ÷ generated. Packets still in the network at the end are not counted as lost.",
    "Average delay": "Mean end-to-end delay of delivered packets (queueing + transmission + propagation).",
    "Throughput": "Bits delivered per second over the run. It cannot exceed the bottleneck's capacity.",
    "Bottleneck utilization": "Share of the run the r1 to r2 link spent transmitting. Busy time, not successful delivery.",
}


# --- presets: what each investigates, what to look at, how to read it ---
# "Change" and "Keep fixed" come from PRESET_INFO (campusnet/presentation.py), which matches the real configurations.

PRESET_GUIDE = {
    "A": {
        "question": "How does the offered load, compared with the bottleneck's capacity, change queueing, delay and loss?",
        "observe": "The r1-r2 queue (peak and average), queue-full drops, average delay and utilization, in the two runs.",
        "interpret": "A drop-tail queue holds packets only up to its capacity. If the load stays under the link rate the queue stays short; "
                     "if it exceeds the rate the queue fills, arrivals are dropped, and delay grows while throughput stays near the link rate. Check each claim against the measured numbers.",
    },
    "B": {
        "question": "With the same congested workload, how does the queue policy change which traffic classes wait or are dropped?",
        "observe": "Per class: average delay, delivered share and queueing delay per hop; for bulk, packets still waiting and the longest wait.",
        "interpret": "Strict priority serves a waiting higher class first, so higher classes can wait less and lower classes more. The link's capacity is the same, "
                     "so the trade-off depends on the traffic mix. Read each class's numbers, not only the totals.",
    },
    "C": {
        "question": "How much does adding bottleneck bandwidth reduce congestion?",
        "observe": "Throughput, queue-full drops, average delay and the r1-r2 utilization at each capacity.",
        "interpret": "More capacity shortens transmission time and queues until the offered load fits. After that, utilization falls but throughput cannot rise above what was offered. "
                     "Find the smallest capacity where queue-full drops reach zero.",
    },
    "D": {
        "question": "What happens to routes and to packets when a link fails, with and without a backup path?",
        "observe": "Each flow's path before and after the failure; drops by reason (link_down or no_route); the Failure & routing view and the timeline's Failure button.",
        "interpret": "Routing and packet outcomes are different. After a failure a route may exist, yet packets already on the old path, or waiting at the failed link, can still be lost. "
                     "With no alternative path, new packets are dropped at creation.",
    },
    "E": {
        "question": "How does buffer size trade queue-full drops against waiting time?",
        "observe": "Queue-full drops, average delay, the peak queue and packets still waiting at the end, at each capacity.",
        "interpret": "A larger queue absorbs bursts, so fewer packets are dropped, but packets wait longer. It does not add link capacity, and a sustained overload eventually fills any queue.",
    },
}
