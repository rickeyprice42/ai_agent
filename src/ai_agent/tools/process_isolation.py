"""Build a fail-closed Linux filesystem and network sandbox for job processes."""
from pathlib import Path
import sys


def isolated_command(workspace: Path, arguments: list[str]) -> list[str]:
    if sys.platform != 'linux' or not Path('/usr/bin/bwrap').is_file():
        raise ValueError('Изолированный исполнитель недоступен. Для запуска кода требуется Linux с bubblewrap.')
    workspace = workspace.resolve(strict=True)
    runtime = Path(sys.prefix).resolve(strict=True)
    trusted_roots = [Path('/usr')]
    if runtime != Path('/usr') and Path('/usr') not in runtime.parents:
        trusted_roots.append(runtime)
    for root in trusted_roots:
        if workspace == root or workspace in root.parents or root in workspace.parents:
            raise ValueError('Workspace пересекается с системным окружением исполнителя.')
    command = ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session',
               '--cap-drop', 'ALL', '--clearenv']
    for root in trusted_roots:
        command += ['--ro-bind', str(root), str(root)]
    for name in ('bin', 'sbin', 'lib', 'lib64'):
        path = Path('/') / name
        if path.is_symlink():
            command += ['--symlink', str(path.readlink()), str(path)]
        elif path.is_dir():
            command += ['--ro-bind', str(path), str(path)]
    command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                '--dir', '/etc', '--bind', str(workspace), '/workspace',
                '--chdir', '/workspace', '--setenv', 'HOME', '/tmp',
                '--setenv', 'PATH', f'{runtime}/bin:/usr/bin:/bin',
                '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONIOENCODING', 'utf-8',
                '--setenv', 'GIT_CONFIG_NOSYSTEM', '1', '--']
    executable = arguments[0]
    if executable in ('python', 'python3', sys.executable):
        arguments = [sys.executable, '-s', '-E', *arguments[1:]]
    return command + arguments
