# CampusNet

Intelligent Campus Network Monitoring, Congestion Control and Optimization System.

CampusNet is a laptop-based discrete-event network **simulation** built with Python and Streamlit. All devices, links, packets, queues and events exist inside the application. It does not capture real network traffic, control physical hardware, use real sockets, or implement a complete TCP/IP stack. `tcp_like` and `udp_like` are simplified simulated behaviors.

Status: simulator, metrics, experiment presets A-E and the Streamlit dashboard are implemented. Preset F (a simplified TCP-like retransmission model) is not implemented; every flow is a simulated datagram.

The dashboard is organised around a network workspace: the topology, a timeline that replays a finished run's recorded events (previous/next event, a time slider, jumps to the first drop, failure or delivery), and an inspector for the selected event, packet, route, device or link. Analysis views (summary, traffic and QoS, performance, failure and routing, experiments, event log) sit below it. Replay is reconstructed from the event log; nothing is re-simulated or animated live.

## Setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
streamlit run app.py
```

## Test

```powershell
python -m pytest
```
