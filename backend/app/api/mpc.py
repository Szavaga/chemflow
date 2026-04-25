"""
MPC Control Studio API — REST + WebSocket endpoints.

REST routes (all require JWT auth):
  POST   /simulations/{sim_id}/mpc/{node_id}/start    create/reset session
  POST   /simulations/{sim_id}/mpc/{node_id}/stop     halt loop
  GET    /simulations/{sim_id}/mpc/{node_id}/config   current config
  POST   /simulations/{sim_id}/mpc/{node_id}/config   hot-swap config
  DELETE /simulations/{sim_id}/mpc/{node_id}          tear down session

WebSocket:
  WS /simulations/{sim_id}/mpc/{node_id}/ws?token=<jwt>

JWT passed as query param because browser WebSocket API cannot send
Authorization headers.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.mpc.controller import MPCConfig, MPCController
from app.core.mpc.mhe_estimator import MHEConfig, MHEEstimator
from app.core.mpc.simulation_state import SimulationState
from app.core.mpc.system_model import CSTRModel
from app.db import get_db
from app.models.orm import Simulation, User
from app.models.schemas import MPCConfigPatch, MPCSessionInfo, MPCStartRequest
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mpc_worker")


@dataclass
class MPCSession:
    sim_id:     str
    node_id:    str
    state:      SimulationState
    controller: MPCController
    config:     MPCConfig
    running:    bool = False
    noise_sigma: float = 0.0
    disturbances: np.ndarray = field(default_factory=lambda: np.zeros(2))


# Session registry keyed by (sim_id, node_id)
_sessions: dict[tuple[str, str], MPCSession] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_key(sim_id: str, node_id: str) -> tuple[str, str]:
    return (sim_id, node_id)


async def _verify_ownership(sim_id: str, user: User, db: AsyncSession) -> None:
    """Raise 404 if the simulation doesn't exist or doesn't belong to the user."""
    row = await db.execute(
        select(Simulation).where(Simulation.id == sim_id)
    )
    sim = row.scalar_one_or_none()
    if sim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    # Load project to check ownership
    from sqlalchemy.orm import selectinload
    row2 = await db.execute(
        select(Simulation).options(selectinload(Simulation.project))
        .where(Simulation.id == sim_id)
    )
    sim2 = row2.scalar_one()
    if sim2.project.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Access denied")


async def _validate_ws_token(token: str, db: AsyncSession) -> User | None:
    """Decode JWT from WS query param; return User or None on failure."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        user_id: str | None = payload.get("sub")
        if not user_id:
            return None
    except JWTError:
        return None
    return await db.get(User, user_id)


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.post("/simulations/{sim_id}/mpc/{node_id}/start", response_model=MPCSessionInfo)
async def start_mpc_session(
    sim_id:  str,
    node_id: str,
    body:    MPCStartRequest,
    db:      AsyncSession = Depends(get_db),
    user:    User = Depends(get_current_user),
) -> MPCSessionInfo:
    await _verify_ownership(sim_id, user, db)

    key = _session_key(sim_id, node_id)

    # Build CSTR model seeded from steady-state values if provided
    model = CSTRModel()
    if body.x0 is not None:
        model.x_ss = np.clip(np.array(body.x0), model.x_min, model.x_max)
    if body.u0 is not None:
        model.u_ss = np.clip(np.array(body.u0), model.u_min, model.u_max)

    config = MPCConfig()
    config.dt = body.dt

    state = SimulationState(model=model, dt=body.dt)
    if body.x0 is not None:
        state.reset(x0=body.x0, u0=body.u0)
    if body.ca_sp is not None and body.temp_sp is not None:
        state.sp = np.array([body.ca_sp, body.temp_sp])
    elif body.ca_sp is not None:
        state.sp[0] = body.ca_sp
    elif body.temp_sp is not None:
        state.sp[1] = body.temp_sp

    controller = MPCController(model=model, config=config)

    # Tear down existing session if any
    if key in _sessions:
        _sessions[key].running = False

    _sessions[key] = MPCSession(
        sim_id=sim_id, node_id=node_id,
        state=state, controller=controller, config=config,
    )

    sess = _sessions[key]
    return MPCSessionInfo(
        sim_id=sim_id, node_id=node_id,
        running=sess.running,
        time=sess.state.time,
        states=sess.state.x.tolist(),
        setpoints=sess.state.sp.tolist(),
        controller_type=sess.config.controller_type,
        estimator_type=sess.state.estimator_type,
        noise_sigma=sess.noise_sigma,
    )


@router.post("/simulations/{sim_id}/mpc/{node_id}/stop", status_code=204)
async def stop_mpc_session(
    sim_id:  str,
    node_id: str,
    db:      AsyncSession = Depends(get_db),
    user:    User = Depends(get_current_user),
):
    await _verify_ownership(sim_id, user, db)
    key = _session_key(sim_id, node_id)
    if key in _sessions:
        _sessions[key].running = False


@router.get("/simulations/{sim_id}/mpc/{node_id}/config", response_model=MPCSessionInfo)
async def get_mpc_config(
    sim_id:  str,
    node_id: str,
    db:      AsyncSession = Depends(get_db),
    user:    User = Depends(get_current_user),
) -> MPCSessionInfo:
    await _verify_ownership(sim_id, user, db)
    key = _session_key(sim_id, node_id)
    if key not in _sessions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No active MPC session")
    sess = _sessions[key]
    return MPCSessionInfo(
        sim_id=sim_id, node_id=node_id,
        running=sess.running,
        time=sess.state.time,
        states=sess.state.x.tolist(),
        setpoints=sess.state.sp.tolist(),
        controller_type=sess.config.controller_type,
        estimator_type=sess.state.estimator_type,
        noise_sigma=sess.noise_sigma,
    )


@router.post("/simulations/{sim_id}/mpc/{node_id}/config", response_model=MPCSessionInfo)
async def patch_mpc_config(
    sim_id:  str,
    node_id: str,
    body:    MPCConfigPatch,
    db:      AsyncSession = Depends(get_db),
    user:    User = Depends(get_current_user),
) -> MPCSessionInfo:
    await _verify_ownership(sim_id, user, db)
    key = _session_key(sim_id, node_id)
    if key not in _sessions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No active MPC session")

    sess = _sessions[key]
    patch = body.model_dump(exclude_none=True)
    noise_sigma = patch.pop("noise_sigma", None)
    if patch:
        sess.config.update(patch)
    if noise_sigma is not None:
        sess.noise_sigma = noise_sigma
        sess.state.noise_sigma = noise_sigma

    return MPCSessionInfo(
        sim_id=sim_id, node_id=node_id,
        running=sess.running,
        time=sess.state.time,
        states=sess.state.x.tolist(),
        setpoints=sess.state.sp.tolist(),
        controller_type=sess.config.controller_type,
        estimator_type=sess.state.estimator_type,
        noise_sigma=sess.noise_sigma,
    )


@router.delete("/simulations/{sim_id}/mpc/{node_id}", status_code=204)
async def delete_mpc_session(
    sim_id:  str,
    node_id: str,
    db:      AsyncSession = Depends(get_db),
    user:    User = Depends(get_current_user),
):
    await _verify_ownership(sim_id, user, db)
    key = _session_key(sim_id, node_id)
    if key in _sessions:
        _sessions[key].running = False
        del _sessions[key]


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/simulations/{sim_id}/mpc/{node_id}/ws")
async def mpc_websocket(
    sim_id:  str,
    node_id: str,
    ws:      WebSocket,
    token:   str = "",
    db:      AsyncSession = Depends(get_db),
):
    """
    Real-time MPC simulation loop.

    JWT auth via ?token= query param (browser WS cannot send Authorization headers).
    Close code 4001 on auth failure.
    """
    user = await _validate_ws_token(token, db)
    if user is None:
        await ws.close(code=4001)
        return

    # Ownership check
    try:
        await _verify_ownership(sim_id, user, db)
    except HTTPException:
        await ws.close(code=4001)
        return

    await ws.accept()

    key = _session_key(sim_id, node_id)
    # Auto-create a default session if none exists
    if key not in _sessions:
        model = CSTRModel()
        config = MPCConfig()
        state = SimulationState(model=model, dt=config.dt)
        controller = MPCController(model=model, config=config)
        _sessions[key] = MPCSession(
            sim_id=sim_id, node_id=node_id,
            state=state, controller=controller, config=config,
        )

    sess = _sessions[key]

    async def _sim_loop():
        loop = asyncio.get_event_loop()
        while sess.running:
            t0 = loop.time()
            try:
                x_hat = sess.state.observe()
                u_opt, pred, ok = await loop.run_in_executor(
                    _executor,
                    sess.controller.compute,
                    x_hat.copy(),
                    sess.state.sp.copy(),
                    sess.state.u.copy(),
                    sess.disturbances.copy(),
                )
                snapshot = sess.state.step(u_opt, sess.disturbances, ok)
                await ws.send_json({
                    "type": "state",
                    **snapshot,
                    "mpc_success": ok,
                    "predicted_trajectory": pred,
                })
            except WebSocketDisconnect:
                sess.running = False
                return
            except Exception as exc:
                logger.warning("MPC loop error [%s/%s]: %s", sim_id, node_id, exc)
                try:
                    await ws.send_json({"type": "error", "message": str(exc)})
                except Exception:
                    pass

            elapsed = loop.time() - t0
            await asyncio.sleep(max(0.0, sess.config.dt - elapsed))

    sim_task: asyncio.Task | None = None

    try:
        async for msg in ws.iter_json():
            cmd  = msg.get("cmd", "")
            data = msg.get("data", {})

            if cmd == "start":
                if not sess.running:
                    sess.running = True
                    sim_task = asyncio.create_task(_sim_loop())

            elif cmd == "stop":
                sess.running = False
                if sim_task and not sim_task.done():
                    sim_task.cancel()

            elif cmd == "reset":
                sess.running = False
                if sim_task and not sim_task.done():
                    sim_task.cancel()
                sess.state.reset()
                await ws.send_json({"type": "reset_done"})

            elif cmd == "setpoints":
                if "ca_sp" in data:
                    sess.state.sp[0] = float(data["ca_sp"])
                if "temp_sp" in data:
                    sess.state.sp[1] = float(data["temp_sp"])

            elif cmd == "config":
                noise_sigma = data.pop("noise_sigma", None)
                if data:
                    sess.config.update(data)
                if noise_sigma is not None:
                    sess.noise_sigma = float(noise_sigma)
                    sess.state.noise_sigma = float(noise_sigma)

            elif cmd == "estimator":
                est_type = str(data.get("type", "KF")).upper()
                if est_type == "MHE" and sess.state._mhe is None:
                    # Initialise MHE on first switch
                    mhe = MHEEstimator(
                        model=sess.state.model,
                        config=MHEConfig(),
                        dt=sess.config.dt,
                    )
                    mhe.warmup()
                    sess.state.set_estimator("MHE", mhe=mhe)
                elif est_type == "KF":
                    sess.state.set_estimator("KF")
                else:
                    sess.state.set_estimator(est_type)

    except WebSocketDisconnect:
        pass
    finally:
        sess.running = False
        if sim_task and not sim_task.done():
            sim_task.cancel()
