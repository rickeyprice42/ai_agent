"""Fixed browser host entry point, never selected through tool arguments.

Chromium reserves a large virtual address space. Keep CPU/deadline/IPC limits
without inheriting the generic Python worker's 1 GiB virtual-address limit.
This is not a browser resident-memory/cgroup limit.
"""
from pathlib import Path
from runpy import run_path

if __name__ == '__main__':
    run_path(str(Path(__file__).with_name('process_worker.py')))['main'](address_space_limit=None)
