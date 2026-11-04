"""Opt-in real model test of upload -> vision -> document, isolated from user data."""
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/"src")]
from backend.service import AgentService
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from PIL import Image, ImageDraw, ImageFont


def main():
    sys.stdout.reconfigure(encoding="utf8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    output = ROOT/".local"/"product-review"/stamp
    output.mkdir(parents=True, exist_ok=True)
    service = AgentService.__new__(AgentService)
    service.root_dir = ROOT
    service.settings = replace(Settings.load(ROOT), model_provider="openai", model_name="gpt-6.1-sol",
        openai_model="gpt-6.1-sol", ollama_api_key="", vision_fallback_model="gpt-6.1-sol",
        reasoning_fallback_enabled=False, llm_timeout_seconds=65, llm_max_retries=0,
        database_file=output/"qa.sqlite3", memory_file=output/"memory.json", tool_workspace_dir=output/"files")
    service.database = AvelinDatabase(service.settings.database_file,
        default_model_provider="openai", default_model_name="gpt-6.1-sol")
    service._agents = {}
    service._connector_factories = []
    user = service.database.create_user("live-qa@example.test", "live-qa", "unused", "QA")
    thread = service.create_chat_thread(user["id"], "Upload vision document QA")
    marker = "AVELIN " + uuid4().hex[:8].upper()
    picture = Image.new("RGB", (760, 320), "white")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((30, 50, 180, 200), fill="blue")
    draw.text((220, 90), marker, font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 38), fill="black")
    data = BytesIO()
    picture.save(data, format="PNG")
    uploaded = service.upload_attachment(user["id"], "visual-test.png", data.getvalue())
    prompt = "Прочитай точный текст на прикреплённом изображении и опиши цвет и форму фигуры. Создай results/image-report.docx с этими результатами. Не угадывай: сначала проанализируй фото инструментом. Ответь кратко и приложи файл."
    message = service.message_with_attachments(user["id"], prompt, [uploaded["path"]])
    started = perf_counter()
    _, stream = service.chat_stream(message, user["id"], thread["id"], attachment_paths=[uploaded["path"]])
    try:
        events = list(stream)
    finally:
        stream.close()
    actions = list(reversed(service.database.list_action_logs(user["id"], 100)))
    agent = service.agent_for_user(user["id"], thread["id"])
    target = agent.files.workspace_dir/"results/image-report.docx"
    text = ""
    if target.exists():
        with ZipFile(target) as package:
            document = ElementTree.fromstring(package.read("word/document.xml"))
            text = "\n".join(node.text or "" for node in document.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
    tools = [a["tool_name"] for a in actions if a["status"] == "completed"]
    passed = ("analyze_image" in tools and "create_docx" in tools and tools.index("analyze_image") < tools.index("create_docx")
        and marker in text and ("син" in text.lower() or "blue" in text.lower())
        and not any(e["type"] in {"approval", "error"} for e in events)
        and not agent.policy.image_attachment_consent)
    row = dict(case="attached_image_to_docx", status="PASS" if passed else "FAIL", duration_s=round(perf_counter()-started, 3),
        provider="openai", model="gpt-6.1-sol", prompt=prompt, expected_marker=marker, tools=tools,
        answer="".join(e.get("text", "") for e in events if e["type"] == "delta"), document_text=text,
        events=events, actions=[dict(tool_name=a["tool_name"], status=a["status"], result=a["result"][:3000]) for a in actions])
    (output/"results.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf8")
    (ROOT/".local/product-review/latest-live.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({k:row[k] for k in ("case", "status", "duration_s", "tools", "answer")}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
