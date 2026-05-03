# ChemFlow

A **browser-based steady-state process simulation platform** for chemical and pharma engineers. Build flowsheets visually, run the solver, inspect stream conditions — and for CSTR nodes, open an embedded **Control Studio** for real-time nonlinear MPC.

## Features

- **Visual flowsheet editor** — drag-and-drop unit ops onto a canvas, draw stream connections
- **SCC-based steady-state solver** — Strongly Connected Component analysis with Wegstein-accelerated recycle convergence; nested loops solved in condensation-DAG order
- **Dynamic component library** — 50 global components seeded from the `chemicals` package; users can add project-scoped custom components via the browser-based Component Manager
- **Per-node configuration panel** — click any node to edit parameters and view inlet conditions
- **Results panel** — stream table, energy balance, unit-flow bar chart, Excel export
- **Embedded Control Studio** — real-time NMPC loop for CSTR nodes, seeded from the solved operating point (WebSocket, GEKKO/IPOPT)
- **JWT authentication** — register / login; all flowsheets, simulations, and custom components are per-user

## Changelog

### 2026-05-02
- **NRTL activity coefficient model** — added as a third VLE property package (`property_package="nrtl"`) for the Flash Drum. Implements the full multicomponent NRTL equation (Renon & Prausnitz). Binary parameters sourced from DECHEMA for ethanol/water, methanol/water, acetone/water, IPA/water, and ethanol/benzene. Unlisted pairs fall back to ideal (γ = 1) without silently corrupting results.
- **Peng-Robinson binary interaction parameter (kij) database** — `PengRobinson` now loads a curated `PR_BIP` table with literature kij values for 10 common polar pairs (e.g. EtOH/H₂O: 0.093, CO₂/H₂O: 0.190, toluene/H₂O: 0.220). Priority order: explicit `kij_override` per node → `thermo` IPDB → `PR_BIP` → 0. Flash Drum nodes can supply `kij_override` in their data dict to test custom values.
- **Non-blocking solver** — `FlowsheetSolver.solve()` is now called via `asyncio.to_thread()` inside the `/run` endpoint, so long solves no longer block the uvicorn event loop and concurrent requests are handled in parallel.
- **Parameter sweep endpoint** — new `POST /simulations/{id}/sweep` endpoint. Supply a `node_id`, a `parameter` key (any field in `node.data`), and a list of 2–50 values; the solver runs once per value using a thread-pool (max 4 workers) and returns ordered results. Enables sensitivity analysis, reflux-vs-purity curves, and reactor design sweeps without scripting.
- **Fix: solver reports `converged=False` on node errors** — when a node raises `SimulationError` (e.g. a Feed with no composition) the solver already continued with a zero-flow placeholder and recorded a warning, but incorrectly returned `"converged": true`. Fixed: a `_node_errors` flag is now set on any node failure and propagated to the top-level `"converged"` field.

### 2026-04-29
- **Fix: Feed component addition** — rebuilt the Component Library integration so clicking "Add" in the browser modal reliably updates the feed composition. The `ComponentManager` is now rendered at the top of the canvas component tree and uses functional state updates (`setNodes` / `setSel`) to avoid stale-closure bugs that caused silent no-ops when adding components. Component display names are now lazily fetched per CAS key instead of eagerly pre-loading 100 records.
- **CAS-keyed compositions end-to-end** — `COMPONENT_LIBRARY`, thermodynamic property tables (`_EXTRA`), Wilson binary interaction parameters, and Peng-Robinson kij lookup are all now keyed by CAS Registry Number (e.g. `"64-17-5"`) instead of internal name strings (e.g. `"ethanol"`). The `CAS_LOOKUP`, `CAS_REVERSE_LOOKUP`, and `resolve_composition()` translation shim is removed; the frontend and backend now speak the same key format throughout.
- **PFR auto-stoichiometry** — if a PFR node has no explicit `stoichiometry` dict the solver automatically constructs `{reactant: -1.0, product_comp: +1.0}` from the node's `reactant` and `product_comp` fields, matching the UI form fields. The solver also registers these CAS keys as recognised components.
- **Docker reliability** — `pip install` in the backend Dockerfile now passes `--timeout=300` to avoid failures on slow network connections.
- **Docker Compose networking** — Vite dev-server proxy target changed from `http://localhost:8000` to `http://backend:8000` so the frontend container can reach the backend service by its Compose service name.

## Unit operations

| Unit op | Inputs | Method |
|---|---|---|
| Feed | T (°C), P (bar), flow (mol/s), composition | Source stream |
| Mixer | — (auto) | Energy + mass balance |
| Splitter | Split fractions | Proportional split |
| Heat Exchanger | Fixed duty (W) **or** outlet T (°C) | Enthalpy balance |
| PFR | Reactant, product, conversion, ΔH_rxn | Stoichiometric conversion |
| Flash Drum | T (°C), P (bar), property package | Rachford-Rice + Wilson **or** NRTL activity coefficients **or** Peng-Robinson EoS |
| CSTR | Volume (L), temperature (°C), coolant T (K) | Arrhenius kinetics + `fsolve` steady-state balance |
| Pump | ΔP (bar), efficiency | Shaft-work calculation |
| Distillation (shortcut) | Light/heavy key, recovery, reflux ratio | Fenske-Underwood-Gilliland (FUG) method |
| Product | — | Sink / stream recorder |

## Recycle solver

The flowsheet solver uses **Strongly Connected Component (SCC) analysis** via NetworkX:

1. Builds a directed graph of nodes and edges
2. Finds all SCCs with `nx.condensation()` — singletons are acyclic nodes; larger SCCs are recycle loops
3. Topologically sorts the condensation DAG so nested inner loops converge before outer loops are evaluated
4. For each recycle SCC, selects the tear stream by heuristic (smallest estimated molar flow; tie-break: highest source in-degree) then runs **component-wise Wegstein acceleration**
5. Falls back to 10 direct-substitution steps at iteration 50 if the residual exceeds 0.1, then restarts Wegstein history

Convergence metadata is returned per loop:

```json
"recycle_loops": [
  {
    "tear_stream_id": "E_recycle",
    "iterations": 12,
    "final_residual": 3.4e-6,
    "method_used": "wegstein",
    "slow_convergence_warning": false
  }
]
```

## Component library

Components are stored in PostgreSQL and served via REST. On first startup the seed script inserts **50 global components** (Tc, Pc, ω, MW, formula, Antoine coefficients where available) sourced from the `chemicals` package:

water, ethanol, methanol, acetone, benzene, toluene, ethylene, propylene, n-butane, n-hexane, n-heptane, cyclohexane, acetic acid, ethyl acetate, chloroform, ammonia, carbon dioxide, nitrogen, oxygen, hydrogen, methane, ethane, propane, isobutane, n-pentane, isopentane, n-octane, styrene, vinyl chloride, acetaldehyde, formaldehyde, formic acid, phenol, aniline, glycerol, ethylene glycol, DMSO, THF, diethyl ether, acetonitrile, HCl, H₂S, SO₂, NO, CO, isoprene, p-xylene, o-xylene, m-xylene, cumene

Global components are **read-only**. Engineers can add project-scoped **custom components** (name, CAS, MW, Tc, Pc, ω, optional Antoine coefficients) through the **Component Manager** modal in the Feed node config panel. Custom components are visible only to the owning project.

### Thermodynamic models

The Flash Drum node exposes a **Property package** dropdown with three options:

**Ideal (Raoult's Law)** *(default)*  
K_i = γ_i · VP_i(T) / P using the Wilson activity coefficient model. Binary Wilson parameters (Λ_ij) are pre-loaded for ethanol/water (`64-17-5`/`7732-18-5`), methanol/water (`67-56-1`/`7732-18-5`), and acetone/water (`67-64-1`/`7732-18-5`). Parameters are keyed by CAS number so they apply automatically regardless of how a component was named. All other pairs default to Λ_ij = 1 (Raoult's law). Successive substitution converges on max relative K-change < 1 × 10⁻⁶.

**NRTL**  
K_i = γ_i · VP_i(T) / P using the Non-Random Two-Liquid activity coefficient model (Renon & Prausnitz, 1968). More accurate than Wilson for strongly non-ideal systems and asymmetric mixtures.

- Full multicomponent NRTL equation: τ_ij = A_ij/(RT), G_ij = exp(−α_ij · τ_ij)
- Binary parameters (α, A_ij, A_ji) sourced from DECHEMA VLE Data Collection for five pairs: ethanol/water, methanol/water, acetone/water, IPA/water, ethanol/benzene
- Unlisted pairs silently fall back to γ = 1 (ideal) so mixed systems with some unlisted pairs remain solvable
- Converges on max relative K-change < 1 × 10⁻⁶

**Peng-Robinson EoS**  
Full cubic equation of state VLE. K-values are initialised from the Wilson K-value correlation (K_i = Pc_i/P · exp(5.373(1+ω_i)(1−Tc_i/T))) and then iterated via fugacity coefficients:

    K_i = exp(ln φ_i^L − ln φ_i^V)

- Soave alpha function with κ = 0.37464 + 1.54226ω − 0.26992ω²
- Van der Waals one-fluid mixing rules with a curated `PR_BIP` table of literature kij values for 10 polar pairs (e.g. EtOH/H₂O: 0.093, CO₂/H₂O: 0.190, toluene/H₂O: 0.220); unlisted pairs default to kij = 0
- Node data can supply `kij_override: {CAS_i: {CAS_j: value}}` to override individual pairs
- Cubic Z-root solver with imaginary-root filtering and Z > B physical bound
- Exact PR fugacity coefficient expression (no simplifications)
- Converges on max absolute K-change < 1 × 10⁻⁸
- Requires Tc, Pc, and ω for all feed components; a warning badge is shown in the config panel if Peng-Robinson is selected

## Control Studio (MPC)

Click **Open Control Studio** on any solved CSTR node to open the real-time control panel:

- **Nonlinear MPC** (NMPC) via GEKKO/IPOPT (IMODE=6), with deviation-space linear MPC as fallback
- **State estimation** — toggle between Discrete Kalman Filter (KF) and Moving Horizon Estimator (MHE, IMODE=5)
- Live charts for CA, T, F, Tc — with dashed setpoint reference lines
- Hot-swap Q/R tuning weights and prediction/control horizons without restarting
- Runaway detection badge (Normal / High T / RUNAWAY)
- Seeded automatically from the steady-state solve result (CA_ss, T_ss_K, F_ss_L_min, Tc_ss_K)

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async, asyncpg |
| Solver | NumPy, SciPy (Rachford-Rice, Wilson, fsolve), NetworkX (SCC / condensation) |
| Thermodynamics | `chemicals` ≥ 1.1.0 (Tc, Pc, ω, Antoine data); Peng-Robinson EoS (pure NumPy) |
| MPC | GEKKO ≥ 1.0.6, IPOPT (NMPC + MHE) |
| Frontend | React 18, TypeScript, Vite |
| Canvas | @xyflow/react (React Flow v12) |
| Charts | Recharts |
| Styling | Tailwind CSS v3 |
| Database | PostgreSQL 16 |
| Auth | JWT (python-jose, bcrypt) |

---

## Running locally (no Docker required)

Prerequisites: **Python 3.12+** and **Node 18+**. Check with:

```powershell
python --version
node --version
```

### 1 — Install PostgreSQL (Windows, run in PowerShell)

If you don't have PostgreSQL installed:

```powershell
winget install PostgreSQL.PostgreSQL.16
```

When the installer asks for a superuser password, choose something memorable (e.g. `postgres`). Leave the port as `5432`.

Then create the app database (still in PowerShell — enter your superuser password when prompted):

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE USER chemflow WITH PASSWORD 'chemflow';"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE DATABASE chemflow OWNER chemflow;"
```

> **Already have PostgreSQL?** Skip the install step and just run the two `psql` commands above, or create the database via pgAdmin. If you need different credentials, see [Environment variables](#environment-variables).

### 2 — Backend

Open a terminal in the project root:

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # PowerShell / CMD
# source .venv/bin/activate   # macOS / Linux / Git Bash

# Install dependencies (includes gekko for MPC, chemicals for component data)
pip install -r requirements.txt

# Start the dev server
uvicorn main:app --reload
```

The backend seeds the component library automatically on first startup.

Backend is available at **http://localhost:8000**  
Interactive API docs: **http://localhost:8000/docs**

### 3 — Frontend

Open a **second** terminal:

```bash
cd frontend
npm install
npm run dev
```

Frontend is available at **http://localhost:5173**

> The frontend proxies `/api` (including WebSocket upgrades) to `http://localhost:8000` automatically — no extra config needed.

---

## Running with Docker Compose

If you have Docker Desktop installed you can start everything with one command:

```bash
git clone https://github.com/Szavaga/chemflow.git
cd chemflow
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |

---

## Running the test suite

```bash
cd backend
pip install -r requirements.txt
pytest                   # all tests
pytest -v --tb=short     # verbose output
```

Tests use an in-memory SQLite database (via aiosqlite) — no running Postgres required.

| Test file | Coverage |
|---|---|
| `test_unit_ops.py` | Individual unit-op solvers (Mixer, Splitter, HEX, PFR, Flash, Pump, CSTR) |
| `test_recycle.py` | Recycle convergence, analytical verification, recycle-node estimates |
| `test_solver.py` | SCC ordering, nested loops, Wegstein fallback, ConvergenceError diagnostics |
| `test_components.py` | Seed count (50), Antoine range validation, custom component scoping, fuzzy search |
| `test_simulation_api.py` | Full API integration (auth → project → flowsheet → run) |
| `test_pinch.py` | Pinch analysis (Q_H_min, Q_C_min, temperature intervals) |

---

## Project layout

```
chemflow/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── auth.py              # POST /auth/register, /auth/login
│   │   │   ├── sims.py              # Project + Simulation + Flowsheet CRUD
│   │   │   ├── components.py        # Dynamic component library (6 endpoints)
│   │   │   ├── mpc.py               # MPC WebSocket + REST endpoints
│   │   │   ├── health.py            # GET /api/health
│   │   │   └── simulations.py       # Legacy quick-sim endpoints
│   │   ├── core/
│   │   │   ├── flowsheet_solver.py  # SCC-based solver with Wegstein recycle convergence
│   │   │   ├── unit_ops.py          # Mixer, Splitter, HEX, PFR, Flash, Pump, CSTR, Stream
│   │   │   ├── seed_components.py   # Seeds 50 global components from `chemicals` package
│   │   │   ├── exceptions.py        # ThermodynamicRangeError
│   │   │   ├── activity.py          # Wilson activity coefficients + binary parameters
│   │   │   ├── simulation.py        # COMPONENT_LIBRARY, CAS_LOOKUP, resolve_composition
│   │   │   ├── thermo.py            # Mixture enthalpy, Cp, density, MW; PengRobinson EoS
│   │   │   ├── pinch.py             # Pinch analysis (composite curves, Q_H_min, Q_C_min)
│   │   │   ├── process_metrics.py   # Overall conversion, energy efficiency, recycle ratio
│   │   │   ├── context_builder.py   # Builds result context for API responses
│   │   │   ├── auth.py              # get_current_user dependency
│   │   │   ├── config.py            # Settings (DATABASE_URL, SECRET_KEY, …)
│   │   │   └── mpc/
│   │   │       ├── __init__.py
│   │   │       ├── system_model.py  # CSTRModel: RK4, linearise, runaway checks
│   │   │       ├── controller.py    # MPCController: NMPC (GEKKO) + linear fallback
│   │   │       ├── kalman_filter.py # Discrete Kalman Filter (deviation space)
│   │   │       ├── mhe_estimator.py # Moving Horizon Estimator (GEKKO IMODE=5)
│   │   │       └── simulation_state.py  # SimulationState: observe, step, IAE, history
│   │   └── models/
│   │       ├── orm.py               # SQLAlchemy models (User, Project, Simulation, ChemicalComponent, …)
│   │       └── schemas.py           # Pydantic schemas (ComponentCreate, ComponentResponse, …)
│   ├── tests/
│   │   ├── test_unit_ops.py
│   │   ├── test_recycle.py
│   │   ├── test_solver.py
│   │   ├── test_components.py
│   │   ├── test_simulation_api.py
│   │   └── test_pinch.py
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/
│       │   └── client.ts            # Axios instance + all API calls
│       ├── components/
│       │   ├── flowsheet/
│       │   │   ├── UnitNode.tsx     # Custom React Flow node (SVG icons + handles)
│       │   │   └── StreamEdge.tsx   # Custom edge with hover stream tooltip
│       │   ├── components/
│       │   │   └── ComponentManager.tsx  # Modal: search/add components, create custom
│       │   ├── mpc/
│       │   │   └── ControlStudio.tsx  # Real-time MPC panel (charts + tuning controls)
│       │   └── results/
│       │       ├── ResultsPanel.tsx # Stream table, energy cards, Recharts chart, Excel export
│       │       └── PinchPanel.tsx   # Composite curves, temperature interval table
│       ├── context/
│       │   └── AuthContext.tsx      # JWT auth state + login/logout
│       ├── hooks/
│       │   └── useControlStudio.ts  # WebSocket hook: history, setpoints, MPC config, estimator
│       ├── pages/
│       │   ├── LoginPage.tsx        # Sign in / create account
│       │   ├── Dashboard.tsx        # Project list + new simulation form
│       │   └── FlowsheetPage.tsx    # Main canvas + config panel + results panel
│       └── types/
│           └── index.ts             # All TypeScript interfaces
├── docker-compose.yml
└── README.md
```

---

## API reference

### Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create account (`email`, `password`) |
| POST | `/auth/login` | Get JWT token (OAuth2 form) |

### Projects & simulations (all require `Authorization: Bearer <token>`)

| Method | Path | Description |
|---|---|---|
| POST | `/my/projects` | Create a project |
| GET | `/my/projects` | List your projects |
| POST | `/simulations/` | Create a simulation under a project |
| GET | `/simulations/{id}` | Get simulation with flowsheet + last result |
| PUT | `/simulations/{id}/flowsheet` | Save flowsheet (nodes + edges JSON) |
| POST | `/simulations/{id}/run` | Run solver, persist result |
| GET | `/simulations/{id}/results` | List results |
| POST | `/simulations/{id}/sweep` | Parameter sweep — vary one node parameter across N values (2–50), returns results for each |
| DELETE | `/simulations/{id}` | Delete simulation (cascades) |

### Component library (require `Authorization: Bearer <token>`)

| Method | Path | Description |
|---|---|---|
| GET | `/components` | Search global + project components (`?search=eth&limit=20`) |
| GET | `/components/validate-antoine` | Check Antoine validity at temperature T (`?cas=…&T=…`) |
| GET | `/components/{cas}` | Get full component data by CAS number |
| POST | `/components` | Create project-scoped custom component |
| PUT | `/components/{id}` | Update custom component (global components are read-only) |
| DELETE | `/components/{id}` | Delete custom component |

### MPC Control Studio (require `Authorization: Bearer <token>`)

| Method | Path | Description |
|---|---|---|
| POST | `/simulations/{id}/mpc/{node_id}/start` | Create / reset MPC session, seed from SS |
| POST | `/simulations/{id}/mpc/{node_id}/stop` | Halt control loop |
| GET | `/simulations/{id}/mpc/{node_id}/config` | Current MPC configuration |
| POST | `/simulations/{id}/mpc/{node_id}/config` | Hot-swap Q/R weights and horizons |
| DELETE | `/simulations/{id}/mpc/{node_id}` | Tear down session |
| WS | `/simulations/{id}/mpc/{node_id}/ws?token=<jwt>` | Real-time control loop |

### Legacy quick-sim (no auth required)

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/simulate/flash` | One-shot flash drum |
| POST | `/api/simulate/cstr` | One-shot CSTR |
| POST | `/api/simulate/hex` | One-shot heat exchanger |

---

## Environment variables

All variables can be set in `backend/.env` or as environment variables.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://chemflow:chemflow@localhost:5432/chemflow` | PostgreSQL connection string |
| `SECRET_KEY` | *(insecure default — change in production)* | JWT signing key |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `JWT_EXPIRE_MINUTES` | `1440` | Token lifetime (24 h) |
| `DEBUG` | `false` | Enable debug mode |

---

## License

MIT
