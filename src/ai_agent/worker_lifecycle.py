"""One service-wide stop signal shared by all durable executors."""
from threading import Event, Lock

_lock = Lock()


def task_worker_stop(service):
    with _lock:
        if not hasattr(service, '_task_worker_stop'):
            service._task_worker_stop = Event()
        return service._task_worker_stop
