# ChemFlow

A **browser-based steady-state process simulation platform** for chemical and pharma engineers. Build flowsheets visually, run the solver, and inspect stream conditions — all in the browser.

## Features

- **Visual flowsheet editor** — drag-and-drop unit ops onto a canvas, draw stream connections
- **Steady-state solver** — topological (Kahn's algorithm) propagation through the flowsheet
- **Per-node configuration panel** — click any node to edit parameters and view inlet conditions
- **Results panel** — stream table (T in K, kmol/hr), energy balance, unit-flow bar chart, Excel export
- **JWT authentication** — register / login; all flowsheets are per-user

## Unit operations

| Unit op | Inputs | Method |
|---|---|---|
| Feed | T (°C), P (bar), flow (mol/s), composition | Source stream |
| Mixer | — (auto) | Energy + mass balance |
| Splitter | Split fractions | Proportional split |
| Heat Exchanger | Fixed duty (W) **or** outlet T (°C) | Enthalpy balance |
| PFR | Reactant, product, conversion, ΔH_rxn | Stoichiometric conversion |
| Flash Drum | T (°C), P (bar) | Rachford-Rice + Peng-Robinson K-values |
| Pump | ΔP (bar), efficiency | Shaft-work calculation |
| Product | — | Sink / stream recorder |

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async, asyncpg |
| Solver | NumPy, SciPy (Rachford-Rice, Peng-Robinson EOS) |
| Frontend | React 18, TypeScript, Vite |
| Canvas | @xyflow/react (React Flow v12) |
| Charts | Recharts |
| Styling | Tailwind CSS v3 |
| Database | PostgreSQL 16 |
| Auth | JWT (python-jose, bcrypt) |

---

## Running locally (recommended for development)

You need **Python 3.12+**, **Node 18+**, and a running **PostgreSQL 16** instance.

### 1 — PostgreSQL

The easiest way is to spin up just the database with Docker:

```bash
docker compose up postgres -d
```

Or use any local PostgreSQL with a database named `chemflow` and user `chemflow / chemflow`.

### 2 — Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# (Optional) override settings via .env file
# DATABASE_URL=postgresql+asyncpg://chemflow:chemflow@localhost:5432/chemflow
# SECRET_KEY=your-random-secret

# Start the dev server
uvicorn main:app --reload
```

Backend is available at **http://localhost:8000**  
Interactive API docs: **http://localhost:8000/docs**

### 3 — Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend is available at **http://localhost:5173**

> The frontend proxies `/api` to `http://localhost:8000` via the Vite config.

---

## Running with Docker Compose (all-in-one)

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
pytest                   # 228 tests, ~65 s
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
│   │   │   ├── auth.py          # POST /auth/register, /auth/login
│   │   │   ├── sims.py          # Project + Simulation + Flowsheet CRUD
│   │   │   ├── health.py        # GET /api/health
│   │   │   └── simulations.py   # Legacy quick-sim endpoints
│   │   ├── core/
│   │   │   ├── auth.py          # get_current_user dependency
│   │   │   ├── config.py        # Settings (DATABASE_URL, SECRET_KEY, …)
│   │   │   ├── flowsheet_solver.py  # Topological steady-state solver
│   │   │   ├── unit_ops.py      # Feed, Mixer, Splitter, HEX, PFR, Flash, Pump
│   │   │   └── thermo.py        # Peng-Robinson EOS, Antoine, mixture enthalpy
│   │   └── models/
│   │       ├── orm.py           # SQLAlchemy models (User, Project, Simulation, …)
│   │       └── schemas.py       # Pydantic request / response schemas
│   ├── tests/
│   │   ├── test_unit_ops.py     # Unit-op solver tests
│   │   └── test_simulation_api.py  # API integration tests
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/
│       │   └── client.ts        # Axios instance + all API calls
│       ├── components/
│       │   ├── flowsheet/
│       │   │   ├── UnitNode.tsx     # Custom React Flow node (SVG icons + handles)
│       │   │   └── StreamEdge.tsx   # Custom edge with hover stream tooltip
│       │   └── results/
│       │       └── ResultsPanel.tsx # Stream table, energy cards, Recharts chart, Excel export
│       ├── context/
│       │   └── AuthContext.tsx  # JWT auth state + login/logout
│       ├── pages/
│       │   ├── LoginPage.tsx    # Sign in / create account
│       │   ├── Dashboard.tsx    # Project list + new simulation form
│       │   └── FlowsheetPage.tsx  # Main canvas + config panel + results panel
│       └── types/
│           └── index.ts         # All TypeScript interfaces
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

Pre-loaded thermodynamic properties (Tc, Pc, ω, Antoine constants) for:
benzene, toluene, ethanol, water, methane, propane, methanol, acetone, n-hexane, n-heptane.

---

## License

MIT
