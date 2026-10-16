<div align="center">
    <h1>🚗 agv-dispatch-sim</h1>
    <h3><em>Multi-Vehicle Tote Dispatch Simulation — 200×200 grid · 100 vehicles · Zero-collision spatio-temporal scheduling</em></h3>
</div>

<p align="center">
    <a href="#quick-start-3-commands"><img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+"/></a>
    <a href="#automated-tests"><img src="https://img.shields.io/badge/tests-pytest-brightgreen" alt="pytest"/></a>
    <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/framework-FastAPI-teal" alt="FastAPI"/></a>
    <a href="#features"><img src="https://img.shields.io/badge/simulation-200%C3%97200%20grid%20%C2%B7%20100%20vehicles-blueviolet" alt="200×200 grid · 100 vehicles"/></a>
    <a href="#features"><img src="https://img.shields.io/badge/collision-0-success" alt="Zero collisions"/></a>
    <a href="./LICENSE"><img src="https://img.shields.io/badge/license-CC%20BY--NC%204.0-orange" alt="License: CC BY-NC 4.0"/></a>
    <a href="./LICENSE"><img src="https://img.shields.io/badge/usage-non--commercial-important" alt="Non-commercial use"/></a>
</p>

<p align="center">
    <a href="./README.md">简体中文</a> ·
    <strong>English</strong>
</p>

---

A warehouse tote-transport dispatch demo: a 200×200 grid map where 100
homogeneous vehicles execute inbound/outbound tote-transport tasks in a
discrete-time simulation (generated 1:1 in pairs, cyclic reuse). Features A*
path planning (soft turn penalty, straight-line bias) + spatio-temporal
reservations for guaranteed zero collisions, automatic deadlock recovery, a
battery model with a centralized charging area, and a dual-layer Canvas
frontend supporting dynamic obstacle add/remove plus viewport zoom & pan.

## Quick Start (3 commands)

Development, testing, and running all happen inside the **rcs_core_dev**
Docker container (the host machine only needs a browser; host networking
provides direct access).

```bash
pip install -r requirements.txt
python -m agv_dispatch_sim            # defaults to 0.0.0.0:8000, --port changes the port
# open http://localhost:8000 in a browser
```

Optional arguments: `--port N` (port), `--seed N` (fixed random seed to
reproduce a scenario), `--fast` (10× simulation speed-up), `--layout NAME`
(layout presets: `baseline` / `wide-oneway` / `charge-expanded`, takes effect
on restart; `--check` without `--layout` validates all presets).

## Stop

`Ctrl+C` in the terminal.

## Startup Self-Check

```bash
python -m agv_dispatch_sim --check
```

Validates fixed layout generation (shelf area with 2,400 points / stations /
charging area), A* reachability spot checks, reservation-table mutual
exclusion, API port binding, and prints
`OK: grid layout / planner / engine self-test passed`.

## Features

- **Live visualization**: 200×200 grid, 100 numbered vehicles moving in real
  time (10 ticks/s logic + frontend interpolation rendering), totes shown as
  translucent squares stacked on points, vehicles as numbered dots; vehicles
  pass under totes / carried totes follow their vehicle
- **Task dispatch**: inbound/outbound tasks generated 1:1 in pairs
  (1 task/second), least-loaded even assignment (any two vehicles within
  ±20% of the mean), totes cycle back to the inbound entrance after outbound
  for an endless demo
- **Zero collisions**: spatio-temporal reservations + per-tick movement
  arbitration; overlapping paths automatically queue and wait, at most one
  vehicle per cell at any time
- **Dynamic obstacles**: click empty ground to add / click an obstacle to
  delete; affects planning and triggers detour re-planning within 1 second;
  cells occupied by vehicles/totes or designated as stations/charging cells
  are rejected (toast notification)
- **Deadlock avoidance & recovery**: 15 s no-movement detection →
  re-planning → yielding → reassignment, a three-tier recovery ladder;
  events shown live in the event panel
- **Battery & charging**: full charge 1,000 points, energy consumption =
  load multiplier × distance + turning surcharge; below 20% the vehicle
  finishes its current task then heads to the centralized charging area on
  the south side (charging spots in one row, west-in east-out **one-way
  flow with non-traversable pile cells** (vector property), low-battery
  vehicles queue first when piles are full, and when queue depth exceeds
  the soft limit non-urgent low-battery vehicles are automatically
  **stagger-deferred**); fully charged in 30 seconds and returns; no
  dead-stalled vehicles from depleted batteries
- **Task governance** (002): task-ledger identity (generated = completed +
  in-flight + pending + reassigned), stagnation-timeout auto reassignment,
  workstation capacity caps, congestion backpressure (waiting rate above
  threshold automatically slows task generation, recovers after congestion
  subsides)
- **Viewport zoom & pan**: scroll-wheel zoom anchored at the cursor from
  0.25×–4×, drag to pan (smartly distinguished from clicks, no mode
  switching), one-click reset to full view
- **Motion trail**: trails **evaporate over time** (~3 seconds by default,
  tunable via `AGV_TRAIL_TTL_TICKS`) — refreshed while moving, fading while
  stopped, rather than a fixed-length tail (toolbar "Trail" toggle); position
  jumps automatically break the line
- **Display enhancements** (002): five-state coloring (executing task =
  blue / waiting = orange / charging = green / idle = gray / blocked = red),
  on-board battery bar (<20% orange, <10% red, charging ⚡), congestion
  heatmap layer, vehicle-target straight-line layer (button toggle,
  default off), click-a-vehicle detail card (with recent state changes),
  throughput / average-cycle trend charts
- **Metrics panel**: completed task count, waiting/blocked vehicle count,
  collision count (always 0), charging/queueing counts, average battery,
  per-vehicle completion distribution, throughput trend chart
- **Conflict event recording & analysis (for development iteration)**: groups
  of ≥2 mutually blocked vehicles are recorded as one event, persisted as
  JSONL at `logs/conflicts_<startup-timestamp>.jsonl`; the analysis tool
  outputs hotspot locations, frequency (events / 1,000 ticks), and duration
  distribution:
  ```bash
  python -m agv_dispatch_sim.analyze_conflicts logs/conflicts_XXXXXX.jsonl
  ```

## Tunable Parameters (overridable via AGV_* environment variables; full list at `GET /api/config`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `AGV_TASKS_PER_SECOND` | 1 | Task generation rate (sizing baseline) |
| `AGV_BATTERY_FULL` | 1000 | Full-charge points (grid cells of range) |
| `AGV_LOAD_FACTOR` | 1.5 | Loaded per-cell energy multiplier (1 point/cell unloaded) |
| `AGV_BATTERY_LOW_RATIO` | 0.2 | Low-battery threshold (triggers charge flagging) |
| `AGV_BATTERY_CRITICAL_RATIO` | 0.05 | Critical threshold (fallback: head straight to charging area) |
| `AGV_CHARGE_SECONDS` | 20 | Seconds to charge from 20% to full (adjusted from 30 in phase 002: restores charging capacity headroom, see research.md D11) |
| `AGV_CHARGE_PILES` | 10 | Number of charging spots (FR-029 formula sizing default) |
| `AGV_CHARGE_POWER_BUDGET_RATIO` | 1.0 | Charging-area total power budget ratio (<1 = power-limited) |
| `AGV_LOADED_SPEED_RATIO` | 0.7 | Loaded speed multiplier (1.0 unloaded) |
| `AGV_TURN_SPEED_RATIO` | 0.5 | Turning-step speed multiplier |
| `AGV_TURN_ENERGY` | 0.5 | Extra energy per turning step (points) |
| `AGV_REASSIGN_TICKS` | 300 | No-progress task reassignment threshold (ticks) |
| `AGV_STATION_CAPACITY` | 6 | Max vehicles simultaneously at a workstation |
| `AGV_CHARGE_QUEUE_SOFT_LIMIT` | 6 | Charging queue-depth soft limit (exceeding triggers stagger deferral) |
| `AGV_CHARGE_TRIP_CAP` | 8 | In-flight charging-trip cap (waiting-room flow control, prevents the charging-area entrance corridor from jamming, research.md D14) |
| `AGV_CHARGE_CLAIM_STALL_TICKS` | 300 | Charging-spot claim-stall release threshold (traveler stalls too long → release claim back to queue, D14) |
| `AGV_GOVERN_BACKPRESSURE` / `AGV_GOVERN_REASSIGN` / `AGV_GOVERN_CHARGE_DEFER` | 1 | Congestion-governance combination switches (backpressure / reassign / stagger, independently disable for A/B comparison) |
| `AGV_TRAIL_TTL_TICKS` | 30 | Trail evaporation time (ticks, ≈3 s) |
| `AGV_TURN_PENALTY` | 3 | Path-cost penalty per turn (soft constraint) |
| `AGV_ZOOM_MIN` / `AGV_ZOOM_MAX` | 0.25 / 4.0 | Frontend viewport zoom range |
| `AGV_TICK_RATE` | 10 | Simulation tick frequency (/s) |
| `AGV_STALL_TICKS` | 150 | Stuck detection threshold (ticks, ~15 s) |

Charging-spot count is linked by the FR-029 formula: busy piles ≈ task rate ×
average energy per task ÷ per-pile charging rate, rounded up + ~30% headroom;
the baseline parameters evaluate to 10 (`GET /api/config` shows the current
value as `recommended_charge_piles`).

## REST / WebSocket API

See [specs/001-multi-vehicle-dispatch/contracts/](specs/001-multi-vehicle-dispatch/contracts/):
`GET /api/state`, `GET /api/vehicle/{id}`, `GET /api/config`,
`POST/DELETE /api/obstacle`, `POST /api/control/pause|resume`,
`POST /api/reset`, `WS /ws` (snapshot + delta + event stream).

## Automated Tests

```bash
pytest tests/ -q
```

Scaled-down fast regression (40×40 / 10 vehicles / shares the full engine
code): layout constraints, A* reachability / detours / turn costs,
reservation mutual exclusion, zero collisions, assignment evenness,
no idle wandering, battery drain and charging flow, charging-spot mutual
exclusion, deadlock recovery, conflict-event JSONL; phase 002 adds: charging
pile vector property, three layout presets, task-ledger identity,
stagnation reassignment, congestion backpressure, hardware speed/energy
model, metrics snapshot, target-line / state-history contracts
(tests/test_constraints.py).

## In-Container Verification (docker rcs_core_dev)

```bash
docker exec -it rcs_core_dev \
  bash -c "cd /home/dev/workspace/binglee/agv-dispatch-sim && pip install -r requirements.txt && python -m agv_dispatch_sim --check"
# after starting the service in the container, visit http://localhost:8000 from the host browser (host networking)
```

## Layout Presets & Efficiency Acceptance (002)

- **Layout presets** (`agv_dispatch_sim/layouts/*.json`, switch via `--layout`,
  takes effect on restart):
  - `baseline`: phase-001 status quo (2-cell-wide perimeter loop, two-way
    corridors, 10 piles)
  - `wide-oneway`: perimeter trunk widened to 3 cells + local one-way
    segments at the north/south shelf edges (parallel alternate routes
    exist, no deadlock traps)
  - `charge-expanded`: 16 charging piles + charging area extended east
    (relieves charging-queue congestion)
- **Dual efficiency criteria** (quickstart ③ / SC-001): run continuously for
  60 minutes; average throughput of the last 30 minutes ≥ 80% of the peak of
  the first 10 minutes (primary), waiting/blocked ratio peak <30%
  (secondary); mutual-blocking frequency down ≥30% vs the phase-001 baseline
  (SC-003, `analyze_conflicts` methodology)
- **Scaled-regression criteria**: FR-004 ±20% evenness is accepted at the
  full-spec scale (100 vehicles / 10 piles); the 40×40 scale amplifies
  charging duty cycle and narrow-corridor congestion, and backpressure
  actively slows generation, so tests only assert a smoke-test upper bound
  of ±80% (see tests/test_simulation.py comments)

## Directory Structure

```text
agv_dispatch_sim/
├── __main__.py            # entry point (--check/--layout/--port/--seed/--fast)
├── config.py              # all tunable parameters (AGV_* env overrides + conflicting-combination validation)
├── layouts/               # layout preset JSONs (baseline / wide-oneway / charge-expanded)
├── analyze_conflicts.py   # conflict-event batch analysis CLI
├── sim/
│   ├── grid.py            # grid & layout (LayoutProfile-driven + charging-pile vector-property directed edges)
│   ├── entities.py        # Vehicle/Tote/Task (lifecycle)/Obstacle/ChargingArea
│   ├── planner.py         # direction-aware A* + reservation table (vector-property same-source validation)
│   ├── dispatcher.py      # 1:1 task generation + least-loaded + ledger/reassign/backpressure
│   ├── charging.py        # charging spots / queue management
│   └── engine.py          # tick loop / progress-accumulated movement / deadlock recovery / conflict events / metrics snapshot
├── api/routes.py          # REST + WebSocket + static hosting
└── static/                # frontend (vanilla HTML/CSS/JS, three-layer Canvas)
```

## License

This project is licensed under [CC BY-NC 4.0](./LICENSE)
(Attribution-NonCommercial 4.0 International): **free for personal learning,
research, and open-source projects; commercial use is prohibited**.
中文文档请见 [README.md](./README.md)。
