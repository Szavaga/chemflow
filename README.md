# ChemFlow

A **browser-based steady-state process simulation platform** for chemical and pharma engineers. Build flowsheets visually, run the solver, inspect stream conditions — and for CSTR nodes, open an embedded **Control Studio** for real-time nonlinear MPC.

## Features

- **Visual flowsheet editor** — drag-and-drop unit ops onto a canvas, draw stream connections
- **Steady-state solver** — topological (Kahn's algorithm) propagation through the flowsheet
- **Per-node configuration panel** — click any node to edit parameters and view inlet conditions
- **Results panel** — stream table, energy balance, unit-flow bar chart, Excel export
- **Embedded Control Studio** — real-time NMPC loop for CSTR nodes, seeded from the solved operating point (WebSocket, GEKKO/IPOPT)
- **JWT authentication** — register / login; all flowsheets are per-user

## Unit operations

| Unit op | Inputs | Method |
|---|---|---|
| Feed | T (°C), P (bar), flow (mol/s), composition | Source stream |
| Mixer | — (auto) | Energy + mass balance |
| Splitter | Split fractions | Proportional split |
| Heat Exchanger | Fixed duty (W) **or** outlet T (°C) | Enthalpy balance |
| PFR | Reactant, product, conversion, ΔH_rxn | Stoichiometric conversion |
| Flash Drum | T (°C), P (bar) | Rachford-Rice + Wilson activity coefficients |
| CSTR | Volume (L), temperature (°C), coolant T (K) | Arrhenius kinetics + `fsolve` steady-state balance |
| Pump | ΔP (bar), efficiency | Shaft-work calculation |
| Product | — | Sink / stream recorder |

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
| Solver | NumPy, SciPy (Rachford-Rice, Wilson, fsolve) |
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

# Install dependencies (includes gekko for MPC)
pip install -r requirements.txt

# Start the dev server
uvicorn main:app --reload
```

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
pytest                   # ~65 s
pytest -v --tb=short     # verbose output
```

Tests use an in-memory SQLite database (via aiosqlite) — no running Postgres required.

---

## Project layout

```
chemflow/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── auth.py              # POST /auth/register, /auth/login
│   │   │   ├── sims.py              # Project + Simulation + Flowsheet CRUD
│   │   │   ├── mpc.py               # MPC WebSocket + REST endpoints
│   │   │   ├── health.py            # GET /api/health
│   │   │   └── simulations.py       # Legacy quick-sim endpoints
│   │   ├── core/
│   │   │   ├── auth.py              # get_current_user dependency
│   │   │   ├── config.py            # Settings (DATABASE_URL, SECRET_KEY, …)
│   │   │   ├── flowsheet_solver.py  # Topological steady-state solver
│   │   │   ├── unit_ops.py          # Feed, Mixer, Splitter, HEX, PFR, Flash, Pump, CSTR
│   │   │   ├── activity.py          # Wilson activity coefficients + binary parameters
│   │   │   ├── simulation.py        # Component library, Antoine VP, simulate_flash
│   │   │   ├── thermo.py            # Mixture enthalpy, Cp, density, MW
│   │   │   └── mpc/
│   │   │       ├── __init__.py
│   │   │       ├── system_model.py  # CSTRModel: RK4, linearise, runaway checks
│   │   │       ├── controller.py    # MPCController: NMPC (GEKKO) + linear fallback
│   │   │       ├── kalman_filter.py # Discrete Kalman Filter (deviation space)
│   │   │       ├── mhe_estimator.py # Moving Horizon Estimator (GEKKO IMODE=5)
│   │   │       └── simulation_state.py  # SimulationState: observe, step, IAE, history
│   │   └── models/
│   │       ├── orm.py               # SQLAlchemy models (User, Project, Simulation, …)
│   │       └── schemas.py           # Pydantic request / response schemas
│   ├── tests/
│   │   ├── test_unit_ops.py         # Unit-op solver tests
│   │   └── test_simulation_api.py   # API integration tests
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
│       │   ├── mpc/
│       │   │   └── ControlStudio.tsx  # Real-time MPC panel (charts + tuning controls)
│       │   └── results/
│       │       └── ResultsPanel.tsx # Stream table, energy cards, Recharts chart, Excel export
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
| DELETE | `/simulations/{id}` | Delete simulation (cascades) |

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
| GET | `/api/components` | Built-in component library |
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

## Component library

Pre-loaded thermodynamic properties (Tc, Pc, ω, Antoine constants, Cp, ΔHvap, ρ) for:

benzene, toluene, ethanol, water, methanol, acetone, n-hexane, n-heptane, methane, ethane, propane, n-butane, isobutane, n-pentane, isopentane, cyclohexane, hydrogen, nitrogen, carbon dioxide, hydrogen sulfide, acetic acid, chloroform, diethyl ether.

### Activity coefficient model

The flash drum uses the **Wilson equation** (modified Raoult's law: K_i = γ_i · VP_i / P) with successive substitution to converge liquid-phase compositions. Binary Wilson parameters (Λ_ij) are pre-loaded for ethanol/water, methanol/water, and acetone/water. All other pairs default to Λ_ij = 1 (ideal liquid, pure Raoult's law).

---

## License

MIT
