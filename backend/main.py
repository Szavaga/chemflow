import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.db import init_db
from app.api import health, simulations
from app.api import auth as auth_module
from app.api.sims import router as sims_router
from app.api.mpc import router as mpc_router

logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ChemFlow API",
    description="Chemical process simulation platform",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Middleware ────────────────────────────────────────────────────────────────

class TraceIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Trace-ID to every request/response cycle.

    The ID is stored on ``request.state.trace_id`` so exception handlers
    can embed it in 500 response bodies for correlation with server logs.
    """

    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response


app.add_middleware(TraceIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://frontend:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    """Pass HTTP exceptions through unchanged (FastAPI default behaviour)."""
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    """Return 422 with field-level Pydantic errors.

    Delegates to FastAPI's built-in handler which uses jsonable_encoder to
    safely serialise any non-JSON-native objects (e.g. ValueError in ctx).
    """
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _generic_500_handler(request: Request, exc: Exception):
    """Catch-all for any unhandled exception.

    Logs the full traceback server-side and returns a 500 with the trace ID
    so clients can correlate the request with server logs.
    """
    trace_id: str = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.exception("Unhandled error [trace_id=%s]", trace_id, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "trace_id": trace_id,
        },
        headers={"X-Trace-ID": trace_id},
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health.router,          prefix="/api", tags=["health"])
app.include_router(simulations.router,     prefix="/api", tags=["simulations"])
app.include_router(auth_module.router,     prefix="/api", tags=["auth"])
app.include_router(sims_router,            prefix="/api", tags=["simulations"])
app.include_router(mpc_router,             prefix="/api", tags=["mpc"])
