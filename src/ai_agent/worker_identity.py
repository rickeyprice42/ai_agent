"""Identify an OS process without confusing a reused PID with its old owner.

Unknown identity always prevents recovery; a stale heartbeat alone proves nothing.
"""
from hashlib import sha256
from pathlib import Path
import json
import os
import sys


def _host():
    if sys.platform == 'linux':
        machine = Path('/etc/machine-id').read_text().strip()
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return sha256(machine.encode()).hexdigest(), boot
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as key:
            machine = winreg.QueryValueEx(key, 'MachineGuid')[0]
        return sha256(str(machine).encode()).hexdigest(), None
    raise OSError('Process identity is unsupported')


def _birth(pid):
    if sys.platform == 'linux':
        # comm may contain spaces and parentheses; fields after its final ')'
        # begin at field 3. A zombie cannot perform another action.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] in {'Z', 'X'}:
            return None
        return fields[19]
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: PID does not exist.
                return None
            raise OSError(error, 'Cannot inspect worker')
        try:
            creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError('Cannot inspect worker exit code')
            if code.value != 259:
                return None
            if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(system), ctypes.byref(user)):
                raise OSError('Cannot inspect worker creation time')
            return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    raise OSError('Process identity is unsupported')


def current_worker_identity():
    try:
        host, boot = _host()
        identity = {'host': host, 'boot': boot, 'pid': os.getpid(), 'birth': _birth(os.getpid())}
        if sys.platform == 'linux':
            identity['service'] = os.getenv('INVOCATION_ID')
            identity['cgroup'] = next((line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::')), None)
        return identity
    except (OSError, ValueError, IndexError):
        return None


def worker_liveness(identity):
    """Return alive/dead/unknown. Only dead permits release of ownership."""
    try:
        if isinstance(identity, str):
            identity = json.loads(identity)
        if not isinstance(identity, dict) or not identity.get('birth'):
            return 'unknown'
        pid = identity.get('pid')
        if type(pid) is not int or pid <= 0 or not isinstance(identity['birth'],str):
            return 'unknown'
        host, boot = _host()
        if identity.get('host') != host:
            return 'unknown'
        if boot and not identity.get('boot'):return 'unknown'
        if boot and identity['boot'] != boot:
            return 'dead'
        try:
            birth = _birth(pid)
        except FileNotFoundError:
            return 'dead'
        return 'alive' if birth == identity['birth'] else 'dead'
    except (OSError, ValueError, IndexError, TypeError):
        return 'unknown'


def worker_quiescence(identity):
    """Prove that both the old worker and its execution domain are gone.

    A dead parent PID is insufficient: a detached child can still write files.
    On Ubuntu require a reboot or an empty old systemd service cgroup following
    a new invocation. Unsupported/unverifiable containment fails closed.
    """
    try:
        if isinstance(identity, str):identity = json.loads(identity)
        if worker_liveness(identity) != 'dead':return False
        current = current_worker_identity()
        if not current or not identity or current['host'] != identity.get('host'):return False
        if current.get('boot') and current['boot'] != identity.get('boot'):return True
        group = identity.get('cgroup')
        if (sys.platform != 'linux' or not group or not group.startswith('/system.slice/')
                or '..' in Path(group).parts or group != current.get('cgroup')
                or not identity.get('service') or not current.get('service')
                or identity['service'] == current['service']):return False
        root = Path('/sys/fs/cgroup') / group.lstrip('/')
        files = [root / 'cgroup.procs', *root.glob('**/cgroup.procs')]
        members = {int(pid) for path in files for pid in path.read_text().split()}
        return members == {os.getpid()}
    except (OSError, ValueError, TypeError, KeyError):
        return False
