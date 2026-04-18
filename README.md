# ChemFlow

An accessible **pharma and chemical process simulation platform** built for engineers, researchers, and students who need reliable steady-state unit operation calculations without the complexity of traditional simulation suites.

## What it does

ChemFlow lets you set up and run process simulations directly in the browser:

| Unit Operation | Method |
|---|---|
| Flash Drum | Rachford-Rice isothermal flash (Raoult's law K-values) |
| CSTR Reactor | Arrhenius nth-order kinetics steady-state design equation |
| Heat Exchanger | LMTD method with effectiveness-NTU cross-check |

Results are stored per project so you can compare runs over time.

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), asyncpg |
| Frontend | React 18, Vite, TypeScript, Recharts |
| Database | PostgreSQL 16 |
| Dev environment | Docker Compose |

## Quick start

```bash
# Clone and spin up all services
git clone <repo>
cd chemflow
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- Interactive API docs: http://localhost:8000/docs

## Local development (without Docker)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Set DATABASE_URL in .env (see .env.example)
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Project layout

```
chemflow/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI route handlers
│   │   ├── core/         # Simulation engine + app config
│   │   ├── models/       # Pydantic schemas + SQLAlchemy ORM
│   │   └── services/     # Business logic
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/          # Axios client
│       ├── components/   # Reusable UI components
│       ├── hooks/        # React hooks
│       ├── pages/        # Route-level pages
│       └── types/        # TypeScript type definitions
└── docker-compose.yml
```

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/components` | Built-in chemical component library |
| POST | `/api/simulate/flash` | Quick flash drum (no persistence) |
| POST | `/api/simulate/cstr` | Quick CSTR (no persistence) |
| POST | `/api/simulate/hex` | Quick heat exchanger (no persistence) |
| POST | `/api/projects` | Create project |
| GET | `/api/projects` | List projects |
| DELETE | `/api/projects/{id}` | Delete project |
| POST | `/api/projects/{id}/runs` | Save simulation run to project |
| GET | `/api/projects/{id}/runs` | List runs for project |

## Component library

Pre-loaded Antoine constants and critical properties for: benzene, toluene, ethanol, water, methanol, acetone, n-hexane, n-heptane.

## License

MIT
