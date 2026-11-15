"""Declarative, JSON-only host for trusted Python tool handlers.

Handlers are imported fresh inside a Linux PID/filesystem/network sandbox. No
server locks, live clients, pickle, credentials or forked Python state cross IPC.
"""
from dataclasses import dataclass, field, replace
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from ai_agent.tools.base import ToolError, ToolResult, CURRENT_EXECUTION
from ai_agent.tools.process_execution import run_process
from ai_agent.tools.process_isolation import isolated_command

SOURCE_ROOT = Path(__file__).resolve().parents[2]
MAX_IPC_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ProcessHandlerSpec:
    entrypoint: str
    workspace: Path
    configuration: dict[str, Any] = field(default_factory=dict)
    code_root: Path = SOURCE_ROOT
    workspace_subpath: str = ''
    expose_value_errors: bool = False
    read_only_workspace: bool = True
    validation_errors_safe: bool = False

    def __post_init__(self):
        object.__setattr__(self,'workspace',Path(self.workspace))
        object.__setattr__(self,'code_root',Path(self.code_root))
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*:[A-Za-z][A-Za-z0-9_]*',self.entrypoint):
            raise ValueError('Handler must be a trusted importable module:function.')
        # Validate the configuration before any process is launched.
        json.dumps(self.configuration,allow_nan=False)


class HostedHandler:
    def __init__(self, spec: ProcessHandlerSpec, *, timeout_seconds=30):
        if not math.isfinite(timeout_seconds) or timeout_seconds<=0:
            raise ValueError('Process host timeout must be finite and positive.')
        self.spec, self.timeout_seconds = spec, timeout_seconds

    @property
    def requires_workspace_write(self):return not self.spec.read_only_workspace

    def _executed_failure(self,result):
        if not self.requires_workspace_write:return result
        if result.error and result.error.code=='invalid_operation' and self.spec.validation_errors_safe:return result
        error = result.error or ToolError('host_failed','Исход действия неизвестен.')
        return replace(result,error=replace(error,retryable=False,details={**error.details,
            'outcome_unknown':True,'termination_confirmed':True}),
            display_text=result.text()+' Результат изменения неизвестен. Проверьте его перед повторением.')

    def restricted_to(self, workspace, subpath):
        return HostedHandler(replace(self.spec,workspace=Path(workspace),workspace_subpath=subpath),timeout_seconds=self.timeout_seconds)

    def __call__(self, arguments, context=None):
        context = context or CURRENT_EXECUTION.get()
        payload = {'entrypoint':self.spec.entrypoint,'configuration':self.spec.configuration,
                   'arguments':arguments,'expose_value_errors':self.spec.expose_value_errors,
                   'timeout_seconds':self.timeout_seconds}
        encoded = json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(encoded.encode('utf-8')) > MAX_IPC_BYTES:
            return _failed('host_input_limit','Аргументы инструмента превышают предел процесса.')
        roots = (SOURCE_ROOT,) if self.spec.code_root.resolve()==SOURCE_ROOT.resolve() else (SOURCE_ROOT,self.spec.code_root)
        try:
            command = isolated_command(self.spec.workspace,
                [sys.executable,'-I','/avelin_code/0/ai_agent/tools/process_worker.py'],
                code_roots=roots,workspace_subpath=self.spec.workspace_subpath,read_only_workspace=self.spec.read_only_workspace)
        except (ValueError,OSError):
            return _failed('host_unavailable','Изолированный Python-исполнитель недоступен.',status='blocked')
        try:
            token = CURRENT_EXECUTION.set(context) if context is not None else None
            try:
                completed = run_process(command,cwd=self.spec.workspace,timeout=self.timeout_seconds,env={},input=encoded)
            finally:
                if token is not None:CURRENT_EXECUTION.reset(token)
        except subprocess.TimeoutExpired:
            return self._executed_failure(_failed('timeout','Превышен срок выполнения инструмента. Процесс остановлен; уже выполненные действия не откатываются.'))
        if context is not None and context.cancelled:
            return self._executed_failure(_failed('cancelled','Процесс инструмента остановлен. Результат незавершённого действия требует проверки.',status='cancelled'))
        if completed.returncode != 0:
            # Worker stderr may contain source data/secrets; never return it.
            return self._executed_failure(_failed('host_failed','Процесс инструмента завершился без подтверждённого результата.'))
        if len(completed.stdout.encode('utf-8')) > MAX_IPC_BYTES:
            return self._executed_failure(_failed('host_output_limit','Результат инструмента превышает предел процесса.'))
        try:
            frame = json.loads(completed.stdout)
            if frame['kind']=='raw':return frame['value']
            value = frame['value']
            if frame['kind']!='result' or value['status'] not in {'completed','failed','blocked','cancelled','not_found','validation_error'}:
                raise ValueError('Invalid result')
            if not isinstance(value.get('display_text',''),str):raise ValueError('Invalid display text')
            if value.get('error'):
                error_data = value['error']
                if (not isinstance(error_data,dict) or not isinstance(error_data.get('code'),str)
                        or not isinstance(error_data.get('message'),str)
                        or type(error_data.get('retryable',False)) is not bool
                        or not isinstance(error_data.get('details',{}),dict)):
                    raise ValueError('Invalid error contract')
            error = ToolError(**value['error']) if value.get('error') else None
            result = ToolResult(status=value['status'],output=value.get('output'),display_text=value.get('display_text',''),error=error)
            return result if result.ok else self._executed_failure(result)
        except (ValueError,TypeError,KeyError):
            return self._executed_failure(_failed('host_protocol','Процесс вернул некорректный результат; действие не будет автоматически повторено.'))


def _failed(code,message,*,status='failed'):
    return ToolResult(status=status,error=ToolError(code,message),display_text=message)
