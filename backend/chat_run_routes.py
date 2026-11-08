"""Authenticated durable chat generation and read-only event subscriptions."""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend import routes
from backend.chat_runs import ACTIVE, chat_runs_for
from backend.models import ChatRequest,ModelSettingsRequest
from backend.service import ChatBusyError

def _no_cache(response: Response):
    response.headers['Cache-Control']='no-store'


router=APIRouter(dependencies=[Depends(_no_cache)])


class ChatRunRequest(ChatRequest):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,80}$')


class ApprovalRunRequest(BaseModel):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,80}$')
    decision: str = Field(pattern=r'^(approved|denied)$')


class QuestionRunRequest(BaseModel):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,80}$')
    option_id: str | None = Field(default=None,max_length=80)
    text: str | None = Field(default=None,max_length=4000)


def _call(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except ChatBusyError as exc:raise HTTPException(409,detail=str(exc)) from exc
    except PermissionError as exc:raise HTTPException(403,detail=str(exc)) from exc
    except ValueError as exc:raise HTTPException(404,detail=str(exc)) from exc


@router.post('/chat/runs',status_code=202)
def start_chat_run(payload: ChatRunRequest,user: dict=Depends(routes.current_user)):
    return _call(chat_runs_for(routes.agent_service).start,user['id'],payload.request_id,payload.message,payload.thread_id,payload.attachments)


@router.post('/chats/{thread_id}/approvals/{approval_id}/runs',status_code=202)
def resume_approval_run(thread_id: str,approval_id: str,payload: ApprovalRunRequest,user: dict=Depends(routes.current_user)):
    return _call(chat_runs_for(routes.agent_service).approve,user['id'],payload.request_id,thread_id,approval_id,payload.decision)


@router.post('/chats/{thread_id}/questions/{question_id}/runs',status_code=202)
def answer_question_run(thread_id: str,question_id: str,payload: QuestionRunRequest,user: dict=Depends(routes.current_user)):
    return _call(chat_runs_for(routes.agent_service).question,user['id'],payload.request_id,thread_id,question_id,payload.option_id,payload.text)


@router.get('/chat/runs/by-request/{request_id}')
def find_chat_run(request_id: str,user: dict=Depends(routes.current_user)):
    return _call(chat_runs_for(routes.agent_service).by_request,user['id'],request_id)


@router.get('/chats/{thread_id}/model-settings')
def get_chat_model(thread_id: str,user: dict=Depends(routes.current_user)):
    return _call(routes.agent_service.chat_model_settings,user['id'],thread_id)


@router.put('/chats/{thread_id}/model-settings')
def set_chat_model(thread_id: str,payload: ModelSettingsRequest,user: dict=Depends(routes.current_user)):
    return _call(routes.agent_service.update_chat_model_settings,user['id'],thread_id,payload.provider,payload.model_name,payload.ollama_url)


@router.delete('/chats/{thread_id}/model-settings')
def reset_chat_model(thread_id: str,user: dict=Depends(routes.current_user)):
    return _call(routes.agent_service.update_chat_model_settings,user['id'],thread_id,reset=True)


@router.get('/chats/{thread_id}/runs/active')
@router.get('/chats/{thread_id}/active-run')
def active_chat_run(thread_id: str,user: dict=Depends(routes.current_user)):
    return _call(chat_runs_for(routes.agent_service).latest,user['id'],thread_id)


@router.get('/chat/runs/{run_id}')
def get_chat_run(run_id: str,user: dict=Depends(routes.current_user)):
    return _call(chat_runs_for(routes.agent_service).get,user['id'],run_id)


@router.post('/chat/runs/{run_id}/cancel')
def cancel_chat_run(run_id: str,user: dict=Depends(routes.current_user)):
    return _call(chat_runs_for(routes.agent_service).cancel,user['id'],run_id)


@router.get('/chat/runs/{run_id}/events')
def chat_run_events(run_id: str,after: int=Query(default=0,ge=0),user: dict=Depends(routes.current_user)):
    store=chat_runs_for(routes.agent_service)
    _call(store.get,user['id'],run_id)
    async def subscribe():
        cursor=after
        # This generator only reads persisted events. HTTP disconnect cancels
        # this subscription, never the producer or its Agent stream/lock.
        while True:
            try:
                snapshot,events=await asyncio.to_thread(store.events_after,user['id'],run_id,cursor)
            except ValueError:return
            for event in events:
                cursor=event['seq']
                yield json.dumps(event,ensure_ascii=False)+'\n'
            if snapshot['state'] not in ACTIVE and cursor>=snapshot['latest_seq']:return
            if not events:await asyncio.sleep(.15)
    return StreamingResponse(subscribe(),media_type='application/x-ndjson',headers={
        'Cache-Control':'no-store, no-transform','X-Accel-Buffering':'no'})
