from __future__ import annotations


FILE_TOOLS = {"write_file", "create_docx", "append_docx", "create_presentation"}


def history_for_ui(history: list[dict], files, *, attachment_display=None) -> list[dict]:
    visible: list[dict] = []
    pending_artifacts: list[dict] = []
    pending_tool_results: list[tuple[str, str]] = []

    for raw_item in history:
        item = dict(raw_item)
        if item.get('role')=='user':
            _user_attachment_display(item,files,attachment_display)
        if item.get("role") == "tool":
            pending_tool_results.append(
                (str(item.get("name", "")), str(item.get("content", "")))
            )
            artifact = _legacy_tool_artifact(item, files)
            if artifact is not None:
                pending_artifacts.append(artifact)
            continue

        if item.get("role") == "assistant":
            stored_artifacts = [
                artifact for artifact in item.get("artifacts", []) if isinstance(artifact, dict)
            ]
            merged = {
                artifact.get("path"): artifact
                for artifact in pending_artifacts + stored_artifacts
                if artifact.get("path")
            }
            item["artifacts"] = list(merged.values())
            if (
                str(item.get("content", "")).strip() == "Не смог сформировать ответ."
                and pending_tool_results
            ):
                item["content"] = (
                    artifact_completion_text(item["artifacts"])
                    if item["artifacts"]
                    else pending_tool_results[-1][1]
                )
            elif pending_tool_results and str(item.get("content", "")) == pending_tool_results[-1][1]:
                tool_name, tool_result = pending_tool_results[-1]
                if tool_name in {"http_request", "web_search"}:
                    item["content"] = _legacy_web_result_text(tool_result)
            pending_artifacts = []
            pending_tool_results = []

        visible.append(item)

    return visible


def _user_attachment_display(item: dict,files,legacy_display=None) -> None:
    stored=[artifact for artifact in item.get('artifacts',[]) if isinstance(artifact,dict) and artifact.get('_user_attachment') is True]
    descriptors=[]
    for artifact in stored:
        path=str(artifact.get('path',''))
        if not path.startswith('attachments/'):
            return
        try:
            file=files.describe_file(path)
        except (ValueError,OSError):
            return
        if file.path!=path:
            return
        descriptors.append({'path':file.path,'name':file.name,'extension':file.extension,'size_bytes':file.size_bytes})
    if descriptors and isinstance(stored[0].get('_display_content'),str):
        item['content']=stored[0]['_display_content']
        item['artifacts']=descriptors
    elif legacy_display is not None:
        result=legacy_display(str(item.get('content','')))
        if result is not None:
            item['content'],item['artifacts']=result


def artifact_completion_text(artifacts: list[dict]) -> str:
    if len(artifacts) == 1:
        return f"Готово — файл **{artifacts[0]['name']}** создан. Его можно скачать ниже."
    return f"Готово — создано файлов: {len(artifacts)}. Их можно скачать ниже."


def _legacy_tool_artifact(item: dict, files) -> dict | None:
    if item.get("name") not in FILE_TOOLS:
        return None
    first_line = str(item.get("content", "")).splitlines()[0] if item.get("content") else ""
    if ":" not in first_line:
        return None
    relative_path = first_line.split(":", 1)[1].strip()
    try:
        file = files.describe_file(relative_path)
    except ValueError:
        return None
    return {
        "path": file.path,
        "name": file.name,
        "extension": file.extension,
        "size_bytes": file.size_bytes,
    }


def _legacy_web_result_text(result: str) -> str:
    for line in result.splitlines():
        if line.startswith("Status:"):
            status = line.partition(":")[2].strip()
            return (
                f"Не удалось получить данные с выбранного сайта: он вернул HTTP {status}. "
                "Попробуй повторить запрос — веб-поиск теперь использует другой источник."
            )
    return "Веб-запрос завершён, но старый технический результат не удалось корректно отобразить."
