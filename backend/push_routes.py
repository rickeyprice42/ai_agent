from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend import routes
from ai_agent.web_push import push_service_for

router = APIRouter(prefix="/api/notifications/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=128)


class PushSubscription(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: PushKeys


class PushEndpoint(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)


@router.get("/config")
def push_config(user: dict = Depends(routes.current_user)):
    return push_service_for(routes.agent_service).public_config(user["id"])


@router.put("/subscription")
def push_subscribe(payload: PushSubscription, user: dict = Depends(routes.current_user)):
    try:
        return push_service_for(routes.agent_service).subscribe(user["id"], payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/subscription")
def push_unsubscribe(payload: PushEndpoint, user: dict = Depends(routes.current_user)):
    return push_service_for(routes.agent_service).unsubscribe(user["id"], payload.endpoint)


@router.post("/test")
def push_test(user: dict = Depends(routes.current_user)):
    try:
        return push_service_for(routes.agent_service).test(user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
