"""Real local HTTP disconnect/reload QA with a gated synthetic provider.

No model, public listener, production DB or external push call is used. Environment
paths are isolated before importing the real API/service modules.
"""
from __future__ import annotations
import argparse
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from threading import Event,Thread
from time import monotonic,sleep
from uuid import uuid4
import gc,json,os,socket,sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['legacy','durable'],default='durable')
    parser.add_argument('--scenario',choices=['disconnect','cancel'],default='disconnect')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    for key,value in {'DATABASE_FILE':str(output/'qa.sqlite3'),'MEMORY_FILE':str(output/'memory.json'),
                      'TOOL_WORKSPACE_DIR':str(output/'workspace'),'MODEL_PROVIDER':'mock','MODEL_NAME':'mock-local',
                      'REASONING_FALLBACK_ENABLED':'false','LLM_MAX_RETRIES':'0'}.items():
        os.environ[key]=value
    sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from backend import routes
    from ai_agent.types import ModelResponse,ModelStreamChunk

    release=Event()
    provider_done=Event()
    provider_started=Event()
    class GatedProvider:
        def __init__(self):self.calls=0;self.completed=False
        def stream(self,**_):
            self.calls+=1
            provider_started.set()
            try:
                yield ModelStreamChunk(delta='PARTIAL_SENTINEL')
                if not release.wait(12):
                    raise RuntimeError('QA completion gate timeout')
                yield ModelStreamChunk(delta=' FINAL_SENTINEL')
                self.completed=True
                yield ModelStreamChunk(response=ModelResponse(text='PARTIAL_SENTINEL FINAL_SENTINEL'))
            finally:
                provider_done.set()
    service=routes.agent_service
    user=service.database.create_user('network-fixture@example.test','network-'+uuid4().hex[:8],'synthetic-no-password','Network QA')
    agent=service.agent_for_user(user['id'])
    provider=GatedProvider()
    agent.provider=provider
    thread_id=agent.memory.thread_id
    app=FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.current_user]=lambda:user
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',0));listener.listen(128)
    port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',lifespan='off',timeout_graceful_shutdown=2))
    server_thread=Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
    server_thread.start()
    deadline=monotonic()+6
    while not server.started and monotonic()<deadline:sleep(.02)
    report={'mode':args.mode,'scenario':args.scenario,'transport':'actual uvicorn localhost TCP with socket disconnect',
            'external_model_calls':0,'external_push_calls':0,'timeline':[]}
    started=monotonic()
    def record(event,**values):report['timeline'].append({'t':round(monotonic()-started,3),'event':event,**values})
    def notifications():
        return [row for row in service.database.list_in_app_notifications(user['id']) if row['kind']=='chat_completed']
    failure=None
    try:
        assert server.started,'Fixture server failed to start'
        base=f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=base,timeout=8) as client:
            payload={'message':'Объясни результат синтетической проверки.','thread_id':thread_id,'attachments':[]}
            run_id=None
            last_seen_seq=0
            request_id=str(uuid4())
            if args.mode=='durable':
                response=client.post('/api/chat/runs',json={**payload,'request_id':request_id})
                assert response.status_code in {200,202},f'Run creation HTTP {response.status_code}'
                initial=response.json();run_id=initial['run_id']
                stream_url=f'/api/chat/runs/{run_id}/events?after=0'
                context=client.stream('GET',stream_url)
            else:
                context=client.stream('POST','/api/chat/stream',json=payload)
            with context as response:
                assert response.status_code==200,f'Stream HTTP {response.status_code}'
                for line in response.iter_lines():
                    if not line:continue
                    event=json.loads(line)
                    last_seen_seq=max(last_seen_seq,int(event.get('seq',0)))
                    if event.get('type')=='delta':
                        record('first_delta_received',provider_completed=provider.completed)
                        assert not provider.completed,'Provider completed before socket disconnect; QA not meaningful'
                        break
            # Exiting the response closes the actual HTTP stream/socket. We do
            # not call the service generator's close/cancel method ourselves.
            record('socket_disconnected')
            sleep(.25)
            if args.mode=='durable':
                reloaded=client.get(f'/api/chat/runs/{run_id}')
                assert reloaded.status_code==200
                state=reloaded.json()['state']
                record('reloaded_run',state=state)
                assert state not in {'cancelled','failed','interrupted'},'Disconnect cancelled server-owned generation'
                active=client.get(f'/api/chats/{thread_id}/runs/active')
                by_request=client.get(f'/api/chat/runs/by-request/{request_id}')
                assert active.status_code==200 and active.json()['run_id']==run_id,'Reload cannot recover active run from the chat'
                assert by_request.status_code==200 and by_request.json()['run_id']==run_id,'Lost POST response cannot recover the request id'
                if args.scenario=='cancel':
                    cancelled=client.post(f'/api/chat/runs/{run_id}/cancel')
                    assert cancelled.status_code in {200,202}
                    record('explicit_cancel_requested')
            release.set()
            if args.mode=='durable':
                assert provider_done.wait(8),'Provider did not leave bounded completion gate'
            else:
                gc.collect()
                provider_done.wait(.3)
            deadline=monotonic()+(8 if args.mode=='durable' else 1.5)
            snapshot=None
            while monotonic()<deadline:
                gc.collect()
                if args.mode=='durable':
                    snapshot=client.get(f'/api/chat/runs/{run_id}').json()
                    if snapshot['state'] in {'completed','cancelled','failed','interrupted','partial'}:break
                elif agent.last_turn_outcome in {'completed','cancelled','failed','partial'}:break
                sleep(.05)
            rows=[row for row in service.database.list_messages(thread_id) if row['role']=='assistant']
            row=rows[-1] if rows else {}
            notices=notifications()
            report.update(provider_calls=provider.calls,provider_completed=provider.completed,
                provider_generator_closed=provider_done.is_set(),
                message_status=row.get('status'),message_contains_final='FINAL_SENTINEL' in row.get('content',''),
                notification_count=len(notices),outcome=getattr(agent,'last_turn_outcome',None),snapshot=snapshot)
            record('terminal_observed',message_status=row.get('status'),notification_count=len(notices))
            if args.scenario=='cancel':
                assert snapshot and snapshot['state']=='cancelled','Explicit Stop did not cancel the run'
                assert not notices,'Explicit cancellation incorrectly created completion notification'
            else:
                assert row.get('status')=='complete' and 'FINAL_SENTINEL' in row.get('content',''),'Answer did not complete after socket disconnect'
                assert len(notices)==1,'Completion notification is missing or duplicated'
                assert provider.calls==1,'Reload started a duplicate model generation'
                if args.mode=='durable':
                    events=[]
                    with client.stream('GET',f'/api/chat/runs/{run_id}/events?after=0') as response:
                        assert response.status_code==200
                        for line in response.iter_lines():
                            if line:events.append(json.loads(line))
                    seq=[event['seq'] for event in events]
                    assert seq==sorted(set(seq)),'Replay sequence is not ordered and deduplicated'
                    assert any(event.get('type')=='done' for event in events),'Completed run events cannot be replayed after reload'
                    resumed=[]
                    with client.stream('GET',f'/api/chat/runs/{run_id}/events?after={last_seen_seq}') as response:
                        assert response.status_code==200
                        for line in response.iter_lines():
                            if line:resumed.append(json.loads(line))
                    assert all(event['seq']>last_seen_seq for event in resumed),'Cursor replay repeated already received events'
                    assert sum(event.get('type')=='done' for event in resumed)==1,'Cursor replay lost or duplicated completion'
                    duplicate=client.post('/api/chat/runs',json={**payload,'request_id':request_id})
                    assert duplicate.status_code in {200,202} and duplicate.json()['run_id']==run_id,'Request idempotency lost on reconnect'
                    assert provider.calls==1 and len(notifications())==1,'Duplicate request reran model or completion notification'
                    report['replayed_events']=len(events)
                    report['resumed_after_seq']=last_seen_seq
                    report['resumed_events']=len(resumed)
    except Exception as exc:
        failure=type(exc).__name__+': '+str(exc)
    finally:
        release.set()
        server.should_exit=True
        server_thread.join(timeout=4)
        with suppress(Exception):listener.close()
    report.update(status='FAIL' if failure else 'PASS',failure=failure,elapsed_seconds=round(monotonic()-started,3))
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:report.get(key) for key in ['status','mode','scenario','elapsed_seconds','failure','message_status','notification_count','provider_calls']},ensure_ascii=False))
    return int(bool(failure))


if __name__=='__main__':raise SystemExit(main())
