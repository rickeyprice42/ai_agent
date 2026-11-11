"""Durable, opt-in Web Push delivery; subscriptions and keys never enter chat."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from uuid import uuid4

from ai_agent.database import AvelinDatabase


SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, endpoint TEXT NOT NULL UNIQUE,
 p256dh TEXT NOT NULL, auth TEXT NOT NULL, start_rowid INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id, active);
CREATE TABLE IF NOT EXISTS push_deliveries (
 id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES push_subscriptions(id) ON DELETE CASCADE,
 notification_id TEXT NOT NULL REFERENCES in_app_notifications(id) ON DELETE CASCADE, payload_json TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT NOT NULL, locked_until TEXT, claim_token TEXT, last_error TEXT,
 created_at TEXT NOT NULL, delivered_at TEXT,
 UNIQUE(subscription_id, notification_id)
);
CREATE INDEX IF NOT EXISTS idx_push_deliveries_queue ON push_deliveries(state, next_attempt_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Keep upstream diagnostics useful without persisting its body, tokens or URLs.
PUSH_REASONS = frozenset({"BadTtl", "BadUrgency", "BadWebPushRequest", "BadWebPushTopic",
    "VapidPkHashMismatch", "IdleTimeout", "BadAuthorizationHeader", "BadJwtToken",
    "BadVapidPublicKey", "BadPath", "MethodNotAllowed", "PayloadTooLarge",
    "TooManyRequests", "InternalServerError", "ServiceUnavailable", "Shutdown"})


def _response_reason(response) -> str | None:
    try:
        reason = response.json().get("reason")
        return reason if reason in PUSH_REASONS else None
    except (ValueError, AttributeError, TypeError):
        return None


def _public_subject(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname if parsed.scheme == "https" else parsed.path.rsplit("@", 1)[-1]
            if parsed.scheme == "mailto" and "@" in parsed.path else "") or ""
        return (bool(host) and "." in host and not host.casefold().endswith((".local", ".localhost", ".invalid", ".test"))
            and host.casefold() not in {"localhost", "127.0.0.1"} and not parsed.username and not parsed.password
            and not parsed.fragment and not any(ord(char) < 33 for char in value))
    except ValueError:
        return False


def _decode_key(value: str, length: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("Некорректные ключи push-подписки.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as exc:
        raise ValueError("Некорректные ключи push-подписки.") from exc
    if len(decoded) != length:
        raise ValueError("Некорректные ключи push-подписки.")
    return decoded


def validate_subscription(payload: dict) -> dict:
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise ValueError("Некорректный адрес push-подписки.")
    try:
        url = urlsplit(endpoint)
        host = (url.hostname or "").casefold()
        port = url.port
    except ValueError as exc:
        raise ValueError("Некорректный адрес push-подписки.") from exc
    allowed = (host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
        or host.endswith(".push.services.mozilla.com") or host.endswith(".push.apple.com")
        or host.endswith(".notify.windows.com"))
    if (url.scheme != "https" or port not in {None, 443} or not allowed
            or url.username or url.password or url.fragment or not url.path
            or any(ord(char) < 33 for char in endpoint)):
        raise ValueError("Поддерживаются только HTTPS-адреса служб push браузера.")
    keys = payload.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("Отсутствуют ключи push-подписки.")
    public = _decode_key(keys.get("p256dh"), 65)
    _decode_key(keys.get("auth"), 16)
    if public[0] != 4:
        raise ValueError("Некорректный ключ push-подписки.")
    from cryptography.hazmat.primitives.asymmetric import ec
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    except ValueError as exc:
        raise ValueError("Некорректный ключ push-подписки.") from exc
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}


@dataclass(frozen=True)
class PushConfig:
    private_key_file: Path | None = None
    public_key: str = ""
    subject: str = "https://avelin.local"
    enabled: bool = True

    @classmethod
    def load(cls, root: Path) -> "PushConfig":
        private = Path(os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY_FILE") or root / "data/web-push/private.pem")
        public = os.getenv("WEB_PUSH_VAPID_PUBLIC_KEY", "")
        subject = os.getenv("WEB_PUSH_VAPID_SUBJECT", "").strip()
        # Older releases shipped a local placeholder accepted by config validation,
        # but rejected by Apple's VAPID authentication. Reuse this installation's
        # existing HTTPS preview origin, never invent an operator email/domain.
        if not subject or subject == "https://avelin.local":
            try:
                preview = json.loads((root / ".local/remote-preview.json").read_text(encoding="utf-8"))["url"]
                parsed = urlsplit(preview)
                candidate = f"{parsed.scheme}://{parsed.netloc}"
                if _public_subject(candidate):
                    subject = candidate
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if not public and private.is_file():
            try:
                from cryptography.hazmat.primitives import serialization
                key = serialization.load_pem_private_key(private.read_bytes(), password=None)
                public = base64.urlsafe_b64encode(key.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
            except (ValueError, OSError, ImportError):
                public = ""
        return cls(private, public, subject,
            os.getenv("WEB_PUSH_ENABLED", "true").casefold() not in {"0", "false", "no"})

    def reason(self) -> str:
        if not self.enabled:
            return "Push-уведомления отключены на сервере."
        if not self.private_key_file or not self.private_key_file.is_file() or not self.public_key:
            return "На сервере ещё не настроены ключи Web Push."
        if not importlib.util.find_spec("pywebpush"):
            return "Установите зависимости Web Push на сервере."
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            key = serialization.load_pem_private_key(self.private_key_file.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
                return "Некорректные ключи VAPID на сервере."
            public = base64.urlsafe_b64encode(key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
            if public != self.public_key:
                return "Публичный ключ VAPID не соответствует приватному."
        except (ValueError, OSError, ImportError):
            return "Некорректные ключи VAPID на сервере."
        if not _public_subject(self.subject):
            return "Укажите действующий HTTPS-адрес или email контакта VAPID: локальный адрес Apple не принимает."
        return ""


class WebPushService:
    def __init__(self, database: AvelinDatabase, config: PushConfig, sender=None) -> None:
        self.database = database
        self.config = config
        self.sender = sender or self._send
        with database.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(push_deliveries)")}
            if "claim_token" not in columns:
                connection.execute("ALTER TABLE push_deliveries ADD COLUMN claim_token TEXT")

    def public_config(self, user_id: str) -> dict:
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=? AND active=1", (user_id,)).fetchone()[0]
        reason = self.config.reason()
        return {"enabled": not bool(reason), "public_key": self.config.public_key if not reason else None,
            "reason": reason, "subscription_count": count}

    def subscribe(self, user_id: str, payload: dict) -> dict:
        if self.config.reason():
            raise ValueError(self.config.reason())
        info = validate_subscription(payload)
        stamp = _now().isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM push_subscriptions WHERE endpoint=?", (info["endpoint"],)).fetchone()
            if existing is not None and existing["user_id"] != user_id:
                raise ValueError("Эта подписка принадлежит другому аккаунту. Отключите и создайте подписку заново.")
            if existing is not None:
                connection.execute("UPDATE push_subscriptions SET p256dh=?,auth=?,active=1,updated_at=?,last_error=NULL WHERE id=?",
                    (info["keys"]["p256dh"], info["keys"]["auth"], stamp, existing["id"]))
                identifier = existing["id"]
            else:
                if connection.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?", (user_id,)).fetchone()[0] >= 8:
                    raise ValueError("Достигнут лимит устройств для push-уведомлений.")
                identifier = str(uuid4())
                start = connection.execute("SELECT COALESCE(MAX(rowid),0) FROM in_app_notifications").fetchone()[0]
                connection.execute("INSERT INTO push_subscriptions(id,user_id,endpoint,p256dh,auth,start_rowid,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (identifier, user_id, info["endpoint"], info["keys"]["p256dh"], info["keys"]["auth"], start, stamp, stamp))
        return {"ok": True, "subscription_id": identifier}

    def unsubscribe(self, user_id: str, endpoint: str) -> dict:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user_id, endpoint))
        return {"ok": True}

    def test(self, user_id: str, request_id: str | None = None) -> dict:
        if self.config.reason():
            raise ValueError(self.config.reason())
        request_id = request_id or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
            raise ValueError("Некорректный идентификатор проверки уведомления.")
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=? AND active=1", (user_id,)).fetchone()[0]
        if not count:
            raise ValueError("Сначала включите push-уведомления на этом устройстве.")
        self.database.create_in_app_notification(user_id, deduplication_key=f"push-test:{request_id}",
            kind="push_test", title="Avelin", body="Проверка push-уведомлений.", entity_type="settings", entity_id="notifications")
        self.enqueue_notifications()
        return {"ok": True, "queued": count, "request_id": request_id, **self.test_status(user_id, request_id)}

    def test_status(self, user_id: str, request_id: str) -> dict:
        with self.database.connect() as connection:
            notification = connection.execute("SELECT id FROM in_app_notifications WHERE user_id=? AND kind='push_test' AND deduplication_key=?",
                (user_id, f"push-test:{request_id}")).fetchone()
            if notification is None:
                return {"state": "not_found"}
            rows = connection.execute("SELECT state,last_error FROM push_deliveries WHERE notification_id=?", (notification["id"],)).fetchall()
        states = {row["state"] for row in rows}
        state = "accepted" if "delivered" in states else "sending" if "sending" in states else "pending" if not rows or "pending" in states else "failed"
        error = next((row["last_error"] for row in rows if row["last_error"]), None)
        return {"state": state, "reason": error if state == "failed" else None}

    def enqueue_notifications(self) -> int:
        stamp = _now().isoformat()
        with self.database.connect() as connection:
            rows = connection.execute("""SELECT n.*,s.id AS subscription_id FROM in_app_notifications n
                JOIN push_subscriptions s ON s.user_id=n.user_id AND s.active=1 AND n.rowid>s.start_rowid
                LEFT JOIN push_deliveries d ON d.subscription_id=s.id AND d.notification_id=n.id
                WHERE d.id IS NULL ORDER BY n.rowid LIMIT 500""").fetchall()
            for row in rows:
                approval = None
                key = str(row["deduplication_key"] or "")
                if row["kind"] == "approval_required" and key.startswith("chat-approval:"):
                    approval = connection.execute(
                        "SELECT tool_name,risk FROM approval_requests WHERE id=? AND user_id=?",
                        (key.removeprefix("chat-approval:"), row["user_id"]),
                    ).fetchone()
                connection.execute("INSERT OR IGNORE INTO push_deliveries(id,subscription_id,notification_id,payload_json,next_attempt_at,created_at) VALUES(?,?,?,?,?,?)",
                    (str(uuid4()), row["subscription_id"], row["id"], json.dumps(_notification_payload(dict(row), approval=dict(approval) if approval else None), ensure_ascii=False), stamp, stamp))
        return len(rows)

    def deliver_pending(self, limit: int = 4) -> list[dict]:
        if self.config.reason():
            return []
        self.enqueue_notifications()
        outcomes = []
        for _ in range(max(1, min(limit, 20))):
            stamp = _now()
            claim_token = uuid4().hex
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("""SELECT d.*,s.endpoint,s.p256dh,s.auth FROM push_deliveries d
                    JOIN push_subscriptions s ON s.id=d.subscription_id AND s.active=1
                    WHERE (d.state='pending' AND d.next_attempt_at<=?) OR (d.state='sending' AND d.locked_until<=?)
                    ORDER BY d.created_at,d.rowid LIMIT 1""", (stamp.isoformat(), stamp.isoformat())).fetchone()
                if row is None:
                    break
                connection.execute("UPDATE push_deliveries SET state='sending',attempts=attempts+1,locked_until=?,claim_token=? WHERE id=?",
                    ((stamp+timedelta(seconds=60)).isoformat(), claim_token, row["id"]))
            status = 0
            upstream_reason = None
            try:
                status = int(self.sender({"endpoint":row["endpoint"],"keys":{"p256dh":row["p256dh"],"auth":row["auth"]}}, json.loads(row["payload_json"])))
            except Exception as exc:
                response = getattr(exc, "response", None)
                status = int(getattr(response, "status_code", 0) or 0)
                upstream_reason = _response_reason(response)
            attempts = row["attempts"] + 1
            delivered = 200 <= status < 300
            expired = status in {404, 410}
            permanent = status in {400, 401, 403} or expired or attempts >= 6
            state = "delivered" if delivered else ("failed" if permanent else "pending")
            reason = None if delivered else f"push_http_{status}" if status else "push_transport_error"
            if reason and upstream_reason:
                reason += ":" + upstream_reason
            with self.database.connect() as connection:
                changed = connection.execute("UPDATE push_deliveries SET state=?,locked_until=NULL,claim_token=NULL,last_error=?,delivered_at=?,next_attempt_at=? WHERE id=? AND claim_token=?",
                    (state, reason, stamp.isoformat() if delivered else None,
                     (stamp+timedelta(seconds=min(3600,30*2**attempts))).isoformat(), row["id"], claim_token)).rowcount
                if expired and changed:
                    connection.execute("UPDATE push_subscriptions SET active=0,last_error=?,updated_at=? WHERE id=?", (reason, stamp.isoformat(), row["subscription_id"]))
            outcomes.append({"delivery_id":row["id"],"state":state if changed else "superseded","status":status})
        return outcomes

    def _send(self, subscription: dict, payload: dict) -> int:
        from pywebpush import webpush
        import requests
        class NoRedirectSession(requests.Session):
            def post(self, url, *args, **kwargs):
                kwargs["allow_redirects"] = False
                return super().post(url, *args, **kwargs)
        with NoRedirectSession() as session:
            result = webpush(subscription, data=json.dumps(payload,ensure_ascii=False),
                vapid_private_key=str(self.config.private_key_file), vapid_claims={"sub":self.config.subject},
                content_encoding="aes128gcm", ttl=86400, timeout=8, requests_session=session)
        return result.status_code


def _notification_payload(notification: dict, *, approval: dict | None = None) -> dict:
    kind = notification["kind"]
    if kind == "push_test":
        body = "Push-уведомления Avelin работают."
    elif kind == "approval_required":
        if approval:
            from ai_agent.chat_approvals import approval_reason
            body = approval_reason(approval["tool_name"], approval["risk"])
        else:
            body = "Для продолжения задачи требуется ваше подтверждение."
    elif kind == "task_report":
        key = str(notification.get("deduplication_key") or "")
        body = ("Задача завершилась ошибкой. Подробности доступны в Avelin." if key.endswith(":failed")
            else "Задача отменена." if key.endswith(":cancelled")
            else "Задача завершена. Результат доступен в Avelin.")
    elif kind == "automation_queued":
        body = "Автоматизация запущена."
    elif kind == "chat_completed":
        body = "Ответ готов. Откройте диалог в Avelin."
    elif kind == "shared_chat_message":
        body = "В общем чате появилось новое сообщение."
    else:
        body = "В Avelin появилось новое уведомление."
    # Lockscreen receives no prompt, source text, tool arguments or raw errors.
    url = "/?view=automations" if notification["entity_type"] in {"automation","task_run","task","task_report"} else "/?view=settings"
    if kind in {"chat_completed", "approval_required", "shared_chat_message"} and notification["entity_type"] == "chat":
        url = "/?view=chat&thread=" + quote(str(notification.get("entity_id") or ""), safe="")
    return {"title":"Avelin","body":body,"tag":f"avelin:{notification['id']}", "data":{"url":url}}


def push_service_for(agent_service) -> WebPushService:
    existing = getattr(agent_service, "_web_push_service", None)
    if existing is None:
        existing = WebPushService(agent_service.database, PushConfig.load(agent_service.root_dir))
        agent_service._web_push_service = existing
    return existing
