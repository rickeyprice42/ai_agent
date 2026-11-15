"""Cross-process stop requests, local cancellation and live lease ownership."""
from threading import Event,Thread
from time import monotonic


class RunCancellation:
    def __init__(self,database,user_id,run_id,worker_id,*,remaining_seconds=3600,lease_seconds=180,parent=None):
        self.database=database;self.user_id=user_id;self.run_id=run_id;self.worker_id=worker_id
        self.deadline=monotonic()+max(0,remaining_seconds)
        self.lease_seconds=lease_seconds
        self.cancellation=Event();self.closed=Event()
        self.outcome=None;self.reason=''
        self.thread=None
        self.parent=parent
        self.next_heartbeat=monotonic()+min(5,lease_seconds/3)

    def is_set(self):return self.cancellation.is_set()

    def _cancel(self,outcome,reason):
        if not self.cancellation.is_set():
            self.outcome,self.reason=outcome,reason
            self.cancellation.set()

    def _poll(self):
        if self.parent is not None and self.parent.is_set():
            self._cancel('cancelled','Выполнение остановлено пользователем.')
        run=self.database.get_task_run(self.user_id,self.run_id)
        owned=run and run['state']=='running' and run['worker_id']==self.worker_id
        if not owned:
            self._cancel('interrupted','Владение запуском потеряно. Неизвестные действия не повторяются.')
            return
        if run.get('control_requested'):
            self._cancel(run['control_requested'],'Остановка запрошена пользователем. Уже выполненные действия не откатываются.')
        elif monotonic()>=self.deadline:
            self._cancel('failed','Исчерпан временной бюджет запуска.')
        # Keep the lease during teardown, including after a cancellation signal.
        if monotonic()>=self.next_heartbeat:
            if self.database.heartbeat_task_run(self.user_id,self.run_id,self.worker_id,self.lease_seconds) is None:
                self._cancel('interrupted','Worker lease потерян во время остановки.')
            self.next_heartbeat=monotonic()+min(5,self.lease_seconds/3)

    def _watch(self):
        while not self.closed.wait(.1):
            try:self._poll()
            except Exception:
                self._cancel('interrupted','Не удалось подтвердить владение запуском. Требуется проверка результата.')

    def __enter__(self):
        self._poll()
        self.thread=Thread(target=self._watch,name='avelin-run-cancellation',daemon=True)
        self.thread.start()
        return self

    def __exit__(self,*_):
        self.closed.set()
        if self.thread:self.thread.join()
