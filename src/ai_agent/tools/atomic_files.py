"""Publish complete tool outputs without exposing partially written targets."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile

from ai_agent.tools.base import CURRENT_EXECUTION


@contextmanager
def atomic_output(target: Path, *, overwrite: bool):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and overwrite and not os.access(target,os.W_OK):
        raise PermissionError('Output file is read-only')
    descriptor,name = tempfile.mkstemp(prefix=f'.{target.name}.',suffix='.pending',dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        context = CURRENT_EXECUTION.get()
        if context is not None and context.cancelled:
            raise InterruptedError('Output publication cancelled')
        with temporary.open('r+b') as stream:os.fsync(stream.fileno())
        if overwrite:
            if target.is_file() and os.name!='nt':
                temporary.chmod(stat.S_IMODE(target.stat().st_mode))
            os.replace(temporary,target)
        else:
            # Same-directory hard link is an atomic no-clobber publication.
            # A concurrent writer cannot turn overwrite=False into replacement.
            try:os.link(temporary,target)
            except FileExistsError as exc:raise ValueError('Файл результата уже существует; выберите другое имя.') from exc
    finally:
        temporary.unlink(missing_ok=True)
