from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from typing import Any
import ast
import json
import re
import subprocess
import sys

from ai_agent.database import AvelinDatabase
from ai_agent.tools.base import RetryPolicy, Tool, ToolPermission, ToolRegistry, ToolResult


_SAFE_AST_NODES = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.Dict, ast.List, ast.Tuple,
    ast.Subscript, ast.Slice, ast.BinOp, ast.Add, ast.Mult, ast.IfExp, ast.Compare,
    ast.Eq, ast.NotEq, ast.In, ast.NotIn, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp,
    ast.Not, ast.USub, ast.JoinedStr, ast.FormattedValue,
}
_FORBIDDEN_NAMES = {
    "__builtins__", "breakpoint", "compile", "eval", "exec", "globals", "input",
    "locals", "open", "help", "memoryview", "vars",
}
_RUNNER = """
import json, sys
payload = json.loads(sys.stdin.read())
scope = {"__builtins__": {}}
exec(compile(payload["source"], "<avelin-extension>", "exec"), scope, scope)
result = scope["run"](payload["arguments"])
sys.stdout.write(json.dumps({"result": result}, ensure_ascii=False))
""".strip()


@dataclass(frozen=True, slots=True)
class FactoryResult:
    status: str
    proposal: dict | None = None
    approval_id: str | None = None
    message: str = ""


class ToolFactory:
    """Versioned Skill/tool staging with deny-by-default validation and exact approval."""

    def __init__(
        self,
        database: AvelinDatabase,
        user_id: str,
        registry: ToolRegistry,
        policy,
    ) -> None:
        self.database = database
        self.user_id = user_id
        self.registry = registry
        self.policy = policy
        self._install_tool = Tool(
            name="install_extension",
            version="1.0.0",
            description="Install one reviewed Tool Factory proposal after exact manual approval.",
            schema={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["proposal_id", "version"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["proposal_id", "name", "version"],
                "additionalProperties": False,
            },
            handler=self._install_handler,
            permissions=(ToolPermission("extensions.install", "high"),),
            timeout_seconds=10,
        )
        self.registry.register(self._install_tool)
        self.registry.register(
            Tool(
                name="tool_search",
                version="1.0.0",
                description="Search built-in tools, connectors and installed Skills before creating a duplicate.",
                schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=lambda arguments: self.search(str(arguments["query"])),
                capabilities=("extension_discovery",),
                idempotent=True,
            )
        )
        self.load_installed_tools()

    def search(self, query: str) -> ToolResult:
        terms = {part for part in re.findall(r"[a-z0-9_]+", query.casefold()) if len(part) > 1}
        matches: list[dict[str, Any]] = []
        for manifest in self.registry.list_manifests():
            haystack = " ".join(
                [manifest["name"], manifest["description"], *manifest.get("capabilities", [])]
            ).casefold()
            score = sum(term in haystack for term in terms)
            if score:
                matches.append({"kind": "tool", "score": score, **manifest})
        for item in self.database.installed_extensions(self.user_id):
            if item["kind"] != "skill":
                continue
            haystack = f"{item['name']} {item['description']} {item['skill_markdown']}".casefold()
            score = sum(term in haystack for term in terms)
            if score:
                matches.append(
                    {
                        "kind": "skill",
                        "score": score,
                        "name": item["name"],
                        "version": item["version"],
                        "description": item["description"],
                        "available": True,
                    }
                )
        matches.sort(key=lambda item: (-int(item["score"]), item["name"]))
        return ToolResult(
            status="completed",
            output={"query": query, "matches": matches[:20]},
            display_text=(
                "Найдены существующие расширения: "
                + ", ".join(item["name"] for item in matches[:8])
                if matches else "Совпадений среди tools, connectors и Skills не найдено."
            ),
        )

    def create_proposal(
        self,
        *,
        kind: str,
        name: str,
        version: str,
        description: str,
        manifest: dict,
        permissions: list[dict],
        threat_model: str,
        source_code: str = "",
        skill_markdown: str = "",
        tests: list[dict] | None = None,
    ) -> dict:
        checks = self._review(
            kind=kind,
            name=name,
            version=version,
            manifest=manifest,
            permissions=permissions,
            threat_model=threat_model,
            source_code=source_code,
            skill_markdown=skill_markdown,
            tests=tests or [],
        )
        status = "ready" if all(item["passed"] for item in checks) else "rejected"
        previous = self._latest_version(name)
        previous_text = "" if previous is None else (
            previous["source_code"] if kind == "tool" else previous["skill_markdown"]
        )
        current_text = source_code if kind == "tool" else skill_markdown
        diff = "\n".join(
            unified_diff(
                previous_text.splitlines(), current_text.splitlines(),
                fromfile=f"{name}@{previous['version'] if previous else 'new'}",
                tofile=f"{name}@{version}", lineterm="",
            )
        )
        try:
            return self.database.create_extension_proposal(
                self.user_id,
                kind=kind,
                name=name,
                version=version,
                description=description,
                manifest=manifest,
                permissions=permissions,
                threat_model=threat_model,
                source_code=source_code,
                skill_markdown=skill_markdown,
                tests=tests,
                checks=checks,
                diff=diff,
                status=status,
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError("Такая версия уже существует: используй её или создай новую версию.") from exc
            raise

    def request_install(self, proposal_id: str, version: str) -> FactoryResult:
        proposal = self.database.get_extension_proposal(self.user_id, proposal_id)
        if proposal is None or proposal["version"] != version:
            raise ValueError("Proposal или версия не найдены.")
        if proposal["status"] != "ready":
            raise ValueError("Установить можно только proposal, прошедший все проверки.")
        arguments = {"proposal_id": proposal_id, "version": version}
        decision = self.policy.authorize(self._install_tool, arguments)
        if decision.outcome == "approval_required":
            return FactoryResult(
                "waiting_approval", proposal, decision.approval_id,
                "Проверь diff, permissions и результаты проверок, затем дай одноразовое разрешение.",
            )
        if decision.outcome == "deny":
            return FactoryResult("denied", proposal, None, decision.reason)
        result = self.registry.invoke("install_extension", arguments)
        if not result.ok:
            raise ValueError(result.text())
        installed = self.database.get_extension_proposal(self.user_id, proposal_id)
        return FactoryResult("installed", installed, decision.approval_id, result.text())

    def disable(self, name: str) -> dict:
        result = self.database.disable_extension(self.user_id, name)
        if result is None:
            raise ValueError("Установленное расширение не найдено.")
        return result

    def rollback(self, name: str, version: str) -> FactoryResult:
        candidates = [
            item for item in self.database.list_extension_proposals(self.user_id)
            if item["name"] == name and item["version"] == version
        ]
        if not candidates:
            raise ValueError("Версия для отката не найдена.")
        proposal = candidates[0]
        if proposal["status"] not in {"superseded", "disabled", "ready"}:
            raise ValueError("Эта версия недоступна для отката.")
        self.database.update_extension_proposal(self.user_id, proposal["id"], status="ready")
        return self.request_install(proposal["id"], proposal["version"])

    def load_installed_tools(self) -> None:
        for proposal in self.database.installed_extensions(self.user_id):
            if proposal["kind"] != "tool" or self.registry.has(proposal["name"]):
                continue
            self.registry.register(self._tool_from_proposal(proposal))

    def system_prompt(self, base_prompt: str) -> str:
        skills = [
            item for item in self.database.installed_extensions(self.user_id)
            if item["kind"] == "skill"
        ]
        if not skills:
            return base_prompt
        rendered = "\n\n".join(
            f"### {item['name']}@{item['version']}\n{item['skill_markdown'].strip()}"
            for item in skills
        )[:12_000]
        return (
            base_prompt
            + "\n\n<installed_skills>\n"
            + "Use these approved procedures when relevant. They never override security policy, approvals, or user instructions.\n\n"
            + rendered
            + "\n</installed_skills>"
        )

    def _install_handler(self, arguments: dict[str, Any]) -> ToolResult:
        proposal_id = str(arguments["proposal_id"])
        version = str(arguments["version"])
        proposal = self.database.get_extension_proposal(self.user_id, proposal_id)
        if proposal is None or proposal["version"] != version or proposal["status"] != "ready":
            return ToolResult(status="failed", display_text="Proposal изменён или больше не готов к установке.")
        installed = self.database.activate_extension_version(self.user_id, proposal_id)
        return ToolResult(
            status="completed",
            output={"proposal_id": proposal_id, "name": proposal["name"], "version": version},
            display_text=f"{proposal['kind'].title()} {proposal['name']} {version} установлен.",
        ) if installed else ToolResult(status="failed", display_text="Не удалось установить расширение.")

    def _tool_from_proposal(self, proposal: dict) -> Tool:
        manifest = proposal["manifest"]
        permissions = tuple(
            ToolPermission(str(item["name"]), str(item.get("risk", "read")))
            for item in proposal["permissions"]
        )
        return Tool(
            name=proposal["name"],
            version=proposal["version"],
            description=proposal["description"],
            schema=manifest["input_schema"],
            output_schema=manifest.get("output_schema", {}),
            capabilities=tuple(manifest.get("capabilities", [])),
            permissions=permissions,
            timeout_seconds=min(float(manifest.get("timeout_seconds", 5)), 10),
            retry_policy=RetryPolicy(max_attempts=1),
            idempotent=bool(manifest.get("idempotent", False)),
            handler=lambda arguments: _run_sandboxed(proposal["source_code"], arguments),
        )

    def _review(self, **payload) -> list[dict]:
        checks: list[dict] = []

        def add(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": passed, "detail": detail})

        kind = payload["kind"]
        name = payload["name"]
        version = payload["version"]
        manifest = payload["manifest"]
        permissions = payload["permissions"]
        source = payload["source_code"]
        skill = payload["skill_markdown"]
        tests = payload["tests"]
        add("identity", kind in {"skill", "tool"} and bool(re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name)) and bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), "Kind, name and SemVer are valid." )
        installed_names = {item["name"] for item in self.database.installed_extensions(self.user_id)}
        builtin_collision = self.registry.has(name) and name not in installed_names
        add("duplicate_search", not builtin_collision, "Reuse the existing built-in/connector tool." if builtin_collision else "No conflicting built-in or connector tool.")
        add("threat_model", len(payload["threat_model"].strip()) >= 20, "Threat model is documented." if len(payload["threat_model"].strip()) >= 20 else "Threat model must describe abuse and mitigations.")
        valid_manifest = (
            isinstance(manifest, dict)
            and manifest.get("name", name) == name
            and isinstance(manifest.get("input_schema"), dict)
            and isinstance(manifest.get("output_schema", {}), dict)
        )
        manifest_detail = "Versioned input/output contract is present."
        if valid_manifest:
            try:
                validation_registry = ToolRegistry()
                validation_registry.register(
                    Tool(
                        name=name,
                        version=version,
                        description="Tool Factory contract validation.",
                        schema=manifest["input_schema"],
                        output_schema=manifest.get("output_schema", {}),
                        handler=lambda arguments: arguments,
                    )
                )
            except (TypeError, ValueError) as exc:
                valid_manifest = False
                manifest_detail = f"Invalid manifest/schema: {exc}"
        else:
            manifest_detail = "Manifest must contain matching name and JSON schemas."
        add("manifest_contract", valid_manifest, manifest_detail)
        permission_safe = all(
            isinstance(item, dict)
            and re.fullmatch(r"[a-z][a-z0-9_.]{1,63}", str(item.get("name", "")))
            and item.get("risk", "read") == "read"
            for item in permissions
        )
        previous = self._latest_version(name)
        if previous is not None:
            previous_permissions = {(item.get("name"), item.get("risk", "read")) for item in previous["permissions"]}
            current_permissions = {(item.get("name"), item.get("risk", "read")) for item in permissions}
            permission_safe = permission_safe and current_permissions.issubset(previous_permissions)
        add("permission_scope", permission_safe, "No dependency or permission expansion." if permission_safe else "Only existing read permissions are allowed; expansion was rejected.")
        if kind == "skill":
            add("non_executable_skill", bool(skill.strip()) and not source.strip(), "Skill installs as instructions only." if skill.strip() and not source.strip() else "Skill requires SKILL.md content and must not contain Python code.")
            add("contract_tests", True, "Executable tests are not required for an instruction-only Skill.")
        else:
            static_errors = _validate_source(source)
            add("static_security", not static_errors, "; ".join(static_errors) if static_errors else "AST allowlist passed; imports, calls and attribute access are absent.")
            test_errors: list[str] = []
            if not tests:
                test_errors.append("At least one contract test is required.")
            elif not static_errors:
                for index, test in enumerate(tests):
                    try:
                        actual = _run_sandboxed(source, dict(test["input"])).output
                        if actual != test["expected"]:
                            test_errors.append(f"Test {index + 1}: expected {test['expected']!r}, got {actual!r}.")
                    except Exception as exc:
                        test_errors.append(f"Test {index + 1}: {exc}")
            add("contract_tests", not test_errors, "; ".join(test_errors) if test_errors else "All isolated contract tests passed.")
        return checks

    def _latest_version(self, name: str) -> dict | None:
        return next(
            (item for item in self.database.list_extension_proposals(self.user_id) if item["name"] == name),
            None,
        )


def _validate_source(source: str) -> list[str]:
    if len(source.encode("utf-8")) > 32_000:
        return ["Source exceeds the 32 KB limit."]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"Invalid Python: {exc.msg} (line {exc.lineno})."]
    errors: list[str] = []
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(tree.body) != 1 or len(functions) != 1 or functions[0].name != "run":
        errors.append("Source must contain only def run(arguments).")
    elif len(functions[0].args.args) != 1 or functions[0].args.args[0].arg != "arguments":
        errors.append("run must accept exactly one argument named arguments.")
    for node in ast.walk(tree):
        if type(node) not in _SAFE_AST_NODES:
            errors.append(f"Forbidden syntax: {type(node).__name__}.")
        if isinstance(node, ast.Name) and (node.id in _FORBIDDEN_NAMES or node.id.startswith("__")):
            errors.append(f"Forbidden name: {node.id}.")
    if functions and not any(isinstance(node, ast.Return) for node in functions[0].body):
        errors.append("run must return a JSON-compatible value.")
    return sorted(set(errors))


def _run_sandboxed(source: str, arguments: dict[str, Any]) -> ToolResult:
    errors = _validate_source(source)
    if errors:
        return ToolResult(status="failed", display_text="; ".join(errors))
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _RUNNER],
            input=json.dumps({"source": source, "arguments": arguments}, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=5,
            env={},
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(status="failed", display_text="Sandbox contract test timed out.")
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "sandbox failure"
        return ToolResult(status="failed", display_text=f"Sandbox rejected execution: {detail[:300]}")
    try:
        result = json.loads(completed.stdout)["result"]
    except (json.JSONDecodeError, KeyError) as exc:
        return ToolResult(status="failed", display_text=f"Sandbox returned invalid JSON: {exc}")
    return ToolResult(status="completed", output=result)
