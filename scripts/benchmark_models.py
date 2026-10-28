from __future__ import annotations

from argparse import ArgumentParser
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from threading import Event, Thread
from time import perf_counter, sleep
from urllib import error, request
from urllib import parse
import csv
import json
import os
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmarks" / "avelin_v1.jsonl"
MODEL_MANIFEST = ROOT / "benchmarks" / "models.json"
DEFAULT_OUTPUT = ROOT / "reports" / "benchmarks"
SYSTEM_PROMPT = (
    "Ты модель ядра Avelin. Точно следуй формату пользователя. "
    "Если задача требует доступный инструмент, вызови инструмент вместо выдуманного результата."
)
DISTRACTOR = (
    "Служебный отвлекающий абзац не меняет исходную цель, ограничения или требуемый формат ответа. "
)


TOOLS = [
    {"name": "get_time", "description": "Текущее время", "parameters": {"type": "object", "properties": {}}},
    {"name": "read_file", "description": "Прочитать файл", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "list_workspace_files", "description": "Список файлов", "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "web_search", "description": "Поиск в интернете", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "research_read", "description": "Исследовать публичную страницу", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "plan_task", "description": "Создать задачу", "parameters": {"type": "object", "properties": {"goal": {"type": "string"}, "priority": {"type": "integer"}}, "required": ["goal"]}},
    {"name": "write_file", "description": "Записать файл", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "overwrite": {"type": "boolean"}}, "required": ["path", "content"]}},
    {"name": "browser_read", "description": "Открыть страницу в браузере", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "screenshot": {"type": "boolean"}}, "required": ["url"]}},
    {"name": "analyze_image", "description": "Проанализировать изображение", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["path"]}},
]


class BenchmarkError(RuntimeError):
    pass


class VramMonitor:
    def __init__(self) -> None:
        self.stop_event = Event()
        self.samples: list[int] = []
        self.thread: Thread | None = None

    def start(self) -> None:
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> int | None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        return max(self.samples) if self.samples else None

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                output = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2, check=True,
                ).stdout.splitlines()
                self.samples.extend(int(line.strip()) for line in output if line.strip().isdigit())
            except (OSError, subprocess.SubprocessError, ValueError):
                return
            self.stop_event.wait(0.1)


def load_dataset(path: Path) -> list[dict]:
    tasks = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            task = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"Invalid JSONL line {number}: {exc}") from exc
        tasks.append(task)
    ids = [str(task.get("id", "")) for task in tasks]
    if len(tasks) < 30 or len(ids) != len(set(ids)) or any(not item for item in ids):
        raise BenchmarkError("Dataset must contain at least 30 tasks with unique non-empty ids.")
    return tasks


def request_json(base_url: str, path: str, payload: dict | None, timeout: int, api_key: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    target = f"{base_url.rstrip('/')}{path}"
    req = request.Request(
        target,
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except error.HTTPError as exc:
        raise BenchmarkError(f"Ollama returned HTTP {exc.code} for {path}.") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise BenchmarkError(f"Cannot reach Ollama at {base_url}: {type(exc).__name__}.") from exc
    except json.JSONDecodeError as exc:
        raise BenchmarkError("Ollama returned invalid JSON.") from exc


def benchmark_task(
    base_url: str,
    model: str,
    task: dict,
    timeout: int,
    api_key: str,
    num_ctx: int,
    num_predict: int = 256,
) -> dict:
    prompt = str(task["prompt"])
    repeat = int(task.get("distractor_repeat", 0))
    if repeat:
        prompt = f"{prompt}\n\n{DISTRACTOR * repeat}\n\nТеперь выполни исходную инструкцию."
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0,
            "seed": 42,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    if task["category"] == "tool_call":
        payload["tools"] = [{"type": "function", "function": item} for item in TOOLS]
    monitor = VramMonitor()
    monitor.start()
    started = perf_counter()
    try:
        raw = request_json(base_url, "/api/chat", payload, timeout, api_key)
        failure = None
    except BenchmarkError as exc:
        raw = {}
        failure = str(exc)
    latency_ms = round((perf_counter() - started) * 1000, 2)
    peak_vram_mib = monitor.stop()
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    text = str(message.get("content") or "").strip()
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    score, judge_details = score_response(task, text, tool_calls)
    eval_count = int(raw.get("eval_count") or 0)
    eval_duration = int(raw.get("eval_duration") or 0)
    tokens_per_second = round(eval_count / (eval_duration / 1_000_000_000), 3) if eval_count and eval_duration else None
    return {
        "id": task["id"], "category": task["category"], "score": score,
        "latency_ms": latency_ms, "tokens_per_second": tokens_per_second,
        "prompt_tokens": raw.get("prompt_eval_count"), "output_tokens": raw.get("eval_count"),
        "peak_vram_mib": peak_vram_mib, "error": failure,
        "judge": judge_details,
        "response_preview": _redacted_preview(text, tool_calls),
    }


def score_response(task: dict, text: str, tool_calls: list) -> tuple[float, dict]:
    judge = task["judge"]
    expected = task["expected"]
    if judge == "exact":
        actual = _normalize(text)
        target = _normalize(str(expected))
        return (1.0 if actual == target else 0.0, {"actual": actual, "expected": target})
    if judge == "contains_all":
        actual = text.casefold()
        matched = [item for item in expected if str(item).casefold() in actual]
        return (len(matched) / len(expected), {"matched": matched, "required": expected})
    if judge in {"json_exact", "json_subset"}:
        parsed = _parse_json(text)
        ok = parsed == expected if judge == "json_exact" else _contains_subset(parsed, expected)
        return (1.0 if ok else 0.0, {"valid_json": parsed is not None, "expected": expected})
    if judge == "tool_call":
        expected_name = expected["name"]
        call = tool_calls[0] if tool_calls and isinstance(tool_calls[0], dict) else {}
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        actual_name = str(function.get("name") or "")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        name_score = 0.5 if actual_name == expected_name else 0.0
        expected_args = expected.get("arguments")
        args_score = 0.5 if expected_args is None or _contains_subset(arguments, expected_args) else 0.0
        return (name_score + args_score, {"name": actual_name, "arguments": arguments, "expected": expected})
    raise BenchmarkError(f"Unknown judge: {judge}")


def aggregate(model: str, rows: list[dict], metadata: dict, manifest: dict | None) -> dict:
    successful = [row for row in rows if not row["error"]]
    by_category: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(float(row["score"]))
    tps = [row["tokens_per_second"] for row in successful if row["tokens_per_second"] is not None]
    vram = [row["peak_vram_mib"] for row in rows if row["peak_vram_mib"] is not None]
    latency = [row["latency_ms"] for row in successful]
    return {
        "model": model,
        "score": round(mean(row["score"] for row in rows), 4),
        "category_scores": {key: round(mean(values), 4) for key, values in sorted(by_category.items())},
        "passed": sum(row["score"] == 1 for row in rows),
        "tasks": len(rows),
        "errors": sum(bool(row["error"]) for row in rows),
        "median_latency_ms": round(median(latency), 2) if latency else None,
        "mean_tokens_per_second": round(mean(tps), 3) if tps else None,
        "peak_vram_mib": max(vram) if vram else None,
        "ollama_metadata": metadata,
        "manifest": manifest,
    }


def model_metadata(base_url: str, model: str, timeout: int, api_key: str) -> dict:
    try:
        data = request_json(base_url, "/api/show", {"model": model}, timeout, api_key)
    except BenchmarkError as exc:
        return {"error": str(exc)}
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    info = data.get("model_info") if isinstance(data.get("model_info"), dict) else {}
    context = next((value for key, value in info.items() if key.endswith(".context_length")), None)
    return {
        "family": details.get("family"), "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"), "context_length": context,
        "license_present": bool(data.get("license")),
    }


def write_reports(
    output_dir: Path,
    dataset_path: Path,
    summaries: list[dict],
    results: dict[str, list[dict]],
    base_url: str,
    num_ctx: int,
    num_predict: int = 256,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)), "dataset_sha256": _sha256(dataset_path),
        "base_url_host": parse.urlparse(base_url).hostname,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "thinking": False,
        "summaries": summaries,
        "results": results,
    }
    json_path = output_dir / f"avelin-benchmark-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = output_dir / f"avelin-benchmark-{stamp}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["model", "score", "passed", "tasks", "errors", "median_latency_ms", "mean_tokens_per_second", "peak_vram_mib"])
        writer.writeheader()
        writer.writerows({key: item.get(key) for key in writer.fieldnames} for item in summaries)
    md_path = output_dir / f"avelin-benchmark-{stamp}.md"
    lines = ["# Avelin model benchmark", "", f"Dataset: `{payload['dataset']}` (`{payload['dataset_sha256']}`)", "", "| Model | Score | Passed | Errors | Median latency | tokens/s | Peak VRAM | Hardware fit |", "|---|---:|---:|---:|---:|---:|---:|---|"]
    for item in summaries:
        fit = (item.get("manifest") or {}).get("gtx_1650_ti_4gb", "not_recorded")
        lines.append(f"| {item['model']} | {item['score']:.3f} | {item['passed']}/{item['tasks']} | {item['errors']} | {item['median_latency_ms'] or 'n/a'} ms | {item['mean_tokens_per_second'] or 'n/a'} | {item['peak_vram_mib'] or 'n/a'} MiB | {fit} |")
    lines.extend(["", "Category scores:", ""])
    for item in summaries:
        lines.append(f"- **{item['model']}**: " + ", ".join(f"{key}={value:.3f}" for key, value in item["category_scores"].items()))
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, csv_path, md_path


def _parse_json(text: str):
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(?:\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _contains_subset(actual, expected) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _contains_subset(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(_contains_subset(a, e) for a, e in zip(actual, expected))
    return actual == expected


def _normalize(value: str) -> str:
    return re.sub(r"[\s\"'`]+", " ", value.strip().casefold()).strip().rstrip(".!?")


def _redacted_preview(text: str, calls: list) -> str:
    if calls:
        return json.dumps(calls[:1], ensure_ascii=False)[:500]
    return text.replace("\n", " ")[:500]


def _sha256(path: Path) -> str:
    from hashlib import sha256
    return sha256(path.read_bytes()).hexdigest()


def _model_available(model: str, installed: set[str]) -> bool:
    requested_base = model.split(":", 1)[0]
    return any(
        item == model or item.split(":", 1)[0] == requested_base
        for item in installed
    )


def main() -> int:
    parser = ArgumentParser(description="Reproducible Avelin Ollama benchmark")
    parser.add_argument("--model", action="append", help="Ollama model; repeat for comparison")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    tasks = load_dataset(args.dataset.resolve())
    manifests = {item["model"]: item for item in json.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))}
    print(json.dumps({"dataset": str(args.dataset), "tasks": len(tasks), "categories": sorted({task['category'] for task in tasks})}, ensure_ascii=False))
    if args.dry_run:
        return 0
    models = args.model or []
    if not models:
        raise BenchmarkError("Pass at least one --model. Ollama is not installed/configured automatically.")
    api_key = os.getenv("OLLAMA_API_KEY", "")
    tags = request_json(args.base_url, "/api/tags", None, min(args.timeout, 15), api_key)
    installed = {
        str(item.get("name") or item.get("model") or "")
        for item in tags.get("models", []) if isinstance(item, dict)
    }
    missing = [model for model in models if not _model_available(model, installed)]
    if missing:
        raise BenchmarkError("Models are not installed/available: " + ", ".join(missing))
    all_results: dict[str, list[dict]] = {}
    summaries = []
    for model in models:
        metadata = model_metadata(args.base_url, model, args.timeout, api_key)
        rows = []
        for index, task in enumerate(tasks, start=1):
            row = benchmark_task(
                args.base_url,
                model,
                task,
                args.timeout,
                api_key,
                args.num_ctx,
                args.num_predict,
            )
            rows.append(row)
            print(json.dumps({"model": model, "task": task["id"], "index": index, "score": row["score"], "latency_ms": row["latency_ms"], "error": row["error"]}, ensure_ascii=False))
        all_results[model] = rows
        summaries.append(aggregate(model, rows, metadata, manifests.get(model)))
    paths = write_reports(
        args.output_dir.resolve(),
        args.dataset.resolve(),
        summaries,
        all_results,
        args.base_url,
        args.num_ctx,
        args.num_predict,
    )
    print(json.dumps({"reports": [str(path) for path in paths], "summaries": summaries}, ensure_ascii=False))
    return 0 if all(item["errors"] == 0 for item in summaries) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
