from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from services.gateway.planb_e2e import run_planb_e2e

router = APIRouter(prefix="/planb", tags=["planb"])


@router.post("/e2e")
def planb_e2e(payload: dict[str, Any]) -> dict:
    return run_planb_e2e(payload)
