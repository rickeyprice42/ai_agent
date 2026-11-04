"""Opt-in live model-authoring QA; synthetic data and an isolated workspace only.

The harness represents the user for two explicit decisions after checking the
proposal's static checks and the exact requested pure-data fixture scope.
"""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import argparse
import json
import logging
import os
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ai_agent.agent import Agent
from ai_agent.config import Settings
from ai_agent.tool_factory import _run_sandboxed


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["openai", "ollama_cloud"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--wall-timeout", type=int, default=150)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-stamp", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stamp = args.run_stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    output = ROOT / ".local/followup-2026-11-04/generated-live" / args.provider / stamp
    output.mkdir(parents=True, exist_ok=True)
    if not args._worker:
        started = perf_counter()
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--provider", args.provider,
                                    "--model", args.model, "--_worker", "--run-stamp", stamp],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            stdout, stderr = process.communicate(timeout=max(1, args.wall_timeout))
            if stdout:
                print(stdout, end="")
            if stderr:
                print(stderr, file=sys.stderr, end="")
            return process.returncode
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
            failure = {"status":"FAIL", "provider":args.provider, "model":args.model,
                       "elapsed_seconds":round(perf_counter()-started,3),
                       "failure":f"Whole synthetic workflow exceeded {args.wall_timeout} seconds; fixture process tree stopped.",
                       "decisions":[], "events":[], "actions":[]}
            (output/"result.json").write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps(failure,ensure_ascii=False))
            return 1
    settings = Settings.load(ROOT)
    settings = replace(settings, model_provider=args.provider, model_name=args.model,
                       openai_model=args.model if args.provider == "openai" else settings.openai_model,
                       reasoning_fallback_enabled=False, llm_timeout_seconds=75, llm_max_retries=0,
                       database_file=output / "qa.sqlite3", memory_file=output / "memory.json",
                       tool_workspace_dir=output / "workspace")
    logging.basicConfig(level=logging.WARNING)
    agent = Agent(settings, user_id="generated-live-fixture", thread_id="generated-live-fixture")
    # Keep discovery and execution inside the fixture's advertised tool scope.
    # Merely hiding definitions still lets tool_search expose other tool names.
    agent.tools._tools = {name: tool for name, tool in agent.tools._tools.items() if name in {
        "tool_search", "propose_extension", "install_extension", "qa_sum_pair"}}
    tools = agent.tools.list_for_model
    agent.tools.list_for_model = lambda: [item for item in tools() if item["name"] in {
        "tool_search", "propose_extension", "install_extension", "qa_sum_pair"}]
    prompt = (
        "Нужен именно новый переиспользуемый инструмент qa_sum_pair, версия1.0.0, для дальнейших одинаковых задач. "
        "Сначала проверь tool_search; затем создай через propose_extension Python pure-data tool: def run(arguments), "
        "вход left/right — числа, выход объект {sum: left+right}. Импорты, вызовы, сеть, файлы не нужны. "
        "permissions=[]; manifest.idempotent=true; input/output JSON schemas обязательны. "
        "Тесты:19+23=42 и2+3=5. Сохрани обзорный bundle, запроси install_extension с точным digest и остановись для подтверждения. "
        "После подтверждённой установки запусти qa_sum_pair с left=19,right=23, также с отдельным подтверждением. "
        "Получим JSON-файл в outputs, результат должен быть42. Skill не заменяет Python tool."
    )
    events, decisions = [], []
    started = perf_counter()
    failure = None
    try:
        with patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
            batch = list(agent.respond_stream(prompt))
            events.extend(batch)
            for _ in range(3):
                waiting = next((event["approval"] for event in reversed(batch) if event["type"] == "approval"), None)
                if not waiting:
                    break
                pending = agent.pending_chat_actions.get(agent.memory.user_id, agent.memory.thread_id, waiting["id"])
                name, arguments = pending["tool_name"], pending["arguments"]
                if name == "install_extension":
                    proposal = agent.memory.database.get_extension_proposal(agent.memory.user_id, arguments["proposal_id"])
                    assert execute.call_count == 0, "Generated code ran before fixture approval"
                    assert proposal and proposal["name"] == "qa_sum_pair" and proposal["kind"] == "tool"
                    assert proposal["permissions"] == [] and proposal["status"] == "ready"
                    assert all(check["passed"] for check in proposal["checks"])
                elif name == "qa_sum_pair":
                    assert arguments == {"left": 19, "right": 23}, "Execution scope differs from the synthetic fixture"
                else:
                    raise AssertionError(f"Unexpected approval tool: {name}")
                decisions.append({"tool": name, "decision": "approved", "sandbox_executions_before": execute.call_count})
                agent.policy.decide(waiting["id"], "approved")
                batch = list(agent.resume_chat_action_stream(waiting["id"]))
                events.extend(batch)
            artifact = agent.tool_factory.artifact_for_invocation("qa_sum_pair", {"left": 19, "right": 23})
            assert artifact, "The model did not create a result artifact"
            result = json.loads((agent.files.workspace_dir / artifact["path"]).read_text("utf-8"))
            assert result["result"] == {"sum": 42}, "Incorrect actual generated-tool result"
            assert [item["tool"] for item in decisions] == ["install_extension", "qa_sum_pair"]
            assert execute.call_count >= 3, "Expected approved contract tests plus a runtime execution"
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
    report = {"status": "FAIL" if failure else "PASS", "provider": args.provider, "model": args.model,
              "elapsed_seconds": round(perf_counter() - started, 3), "failure": failure,
              "decisions": decisions, "events": events,
              "actions": agent.memory.database.list_action_logs(agent.memory.user_id, limit=50)}
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "provider", "model", "elapsed_seconds", "failure", "decisions")}, ensure_ascii=False))
    return int(bool(failure))


if __name__ == "__main__":
    raise SystemExit(main())
