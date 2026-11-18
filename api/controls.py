import asyncio
import fcntl
import ipaddress
import logging
import secrets
import subprocess
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .incidents import Controller
from .presets import Scenario

router = APIRouter()


async def monitor(controller, done):
    while not done.is_set():
        try:
            await asyncio.to_thread(controller.step)
        except Exception:
            logging.exception("NetGuard incident observation failed")
            with controller.lock:
                if controller.active and controller.run["phase"] != "handoff":
                    controller.event(
                        "handoff", "Observation failed. Check lab state before manual reset."
                    )
        await asyncio.sleep(1)


@asynccontextmanager
async def running_controls(app, database):
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.with_suffix(".p1.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller = Controller(database)
        app.state.controller = controller
        app.state.token = secrets.token_urlsafe(32)
        done = asyncio.Event()
        task = asyncio.create_task(monitor(controller, done))
        try:
            yield
        finally:
            done.set()
            await task
            app.state.controller = None


def local(request: Request):
    host = request.url.hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "Local host required")
    try:
        loopback = ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        loopback = False
    if not loopback or getattr(request.app.state, "controller", None) is None:
        raise HTTPException(403, "Controls require a live local session")
    return request.app.state.controller


def authorized(request: Request, controller=Depends(local)):
    origin = urlsplit(request.headers.get("origin", ""))
    if (
        origin.scheme != request.url.scheme
        or origin.netloc != request.url.netloc
        or not secrets.compare_digest(
            request.headers.get("x-netguard-session", ""), request.app.state.token
        )
    ):
        raise HTTPException(403, "Same-origin local session required")
    return controller


@router.get("/api/session")
def session(request: Request, controller=Depends(local)):
    return JSONResponse({"token": request.app.state.token}, headers={"Cache-Control": "no-store"})


@router.get("/api/incidents")
def incidents(controller=Depends(local)):
    return controller.status()


@router.get("/api/incidents/{identity}")
def incident(identity: str, controller=Depends(local)):
    run = controller.detail(identity)
    if not run:
        raise HTTPException(404, "Unknown incident")
    return run


def perform(fn, *args):
    try:
        fn(*args)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise HTTPException(409, "Lab command failed. Inspect incident evidence.") from exc
    return {"accepted": True}


@router.post("/api/scenarios/{scenario}/inject")
def inject(scenario: Scenario, ai: bool = False, controller=Depends(authorized)):
    return perform(controller.inject, scenario, ai)


@router.post("/api/scenarios/reset")
def reset(controller=Depends(authorized)):
    return perform(controller.restore, "reset")


@router.post("/api/incidents/{identity}/approve")
def approve(identity: str, controller=Depends(authorized)):
    return perform(controller.restore, "approved", identity)


@router.post("/api/incidents/{identity}/reject")
def reject(identity: str, controller=Depends(authorized)):
    with controller.lock:
        if (
            not controller.active
            or controller.run["id"] != identity
            or controller.run["phase"] != "proposed"
        ):
            raise HTTPException(409, "No matching proposal")
        controller.event("handoff", "Operator rejected repair. Manual reset required.")
    return {"accepted": True}


class Mode(BaseModel):
    enabled: bool


@router.post("/api/automation/mode")
def automation(mode: Mode, controller=Depends(authorized)):
    with controller.lock:
        if mode.enabled and (controller.stopped or controller.active):
            raise HTTPException(409, "Enable automatic repair before starting the scenario")
        controller.auto = mode.enabled
    return {"automatic": mode.enabled}


@router.post("/api/automation/stop")
def stop(mode: Mode, controller=Depends(authorized)):
    controller.emergency_stop(mode.enabled)
    return {"stopped": mode.enabled}
