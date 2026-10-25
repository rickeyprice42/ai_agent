from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import re
import time

from ai_agent.database import AvelinDatabase


logger = logging.getLogger("avelin.memory.extraction")

IRONY_PATTERN = re.compile(r"\b(?:шучу|сарказм|ирония|just kidding|jk)\b", re.IGNORECASE)
SENSITIVE_PATTERN = re.compile(
    r"\b(?:парол\w*|password|passcode|api[ _-]?key|токен\w*|token|secret|"
    r"паспорт\w*|снилс|инн|банковск\w*|карт[аыу]\s+\d|cvv|cvc|"
    r"зарплат\w*|доход\w*|долг\w*|кредит\w*|"
    r"диагноз\w*|болезн\w*|лекарств\w*|аллерги\w*|здоров\w*|давлени\w*|"
    r"файл\w*|документ(?:е|ом|ы|ов|у|а)?|вложени\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_key: str
    kind: str
    content: str
    polarity: str
    confidence: float
    threshold: int
    expires_at: str | None = None
    correction: bool = False


class AutonomousMemoryEngine:
    def __init__(self, database: AvelinDatabase, user_id: str, thread_id: str) -> None:
        self.database = database
        self.user_id = user_id
        self.thread_id = thread_id

    def process_completed_turn(self, source_message_id: str, user_text: str) -> dict:
        started = time.perf_counter()
        settings = self.database.get_memory_settings(self.user_id)
        metrics = {
            "enabled": settings["autonomous_enabled"],
            "candidate_count": 0,
            "saved_count": 0,
            "merged_count": 0,
            "superseded_count": 0,
            "rejected_count": 0,
            "pending_count": 0,
            "estimated_tokens": max(1, len(user_text.encode("utf-8")) // 4),
            "latency_ms": 0.0,
            "reasons": {},
        }
        if not settings["autonomous_enabled"]:
            metrics["reasons"] = {"opt_out": 1}
            return self._finish(metrics, started)

        candidates, rejected = extract_candidates(user_text)
        metrics["candidate_count"] = len(candidates)
        metrics["rejected_count"] = len(rejected)
        reasons: dict[str, int] = {}
        for reason in rejected:
            reasons[reason] = reasons.get(reason, 0) + 1

        for candidate in candidates:
            observation = self.database.observe_memory_candidate(
                self.user_id,
                memory_key=candidate.memory_key,
                kind=candidate.kind,
                content=candidate.content,
                polarity=candidate.polarity,
                confidence=candidate.confidence,
                source_message_id=source_message_id,
            )
            should_save = candidate.correction or observation["observation_count"] >= candidate.threshold
            if not should_save:
                metrics["pending_count"] += 1
                reasons["needs_repetition"] = reasons.get("needs_repetition", 0) + 1
                continue
            action = self._save_or_merge(candidate, source_message_id)
            metrics[f"{action}_count"] += 1
            self.database.set_memory_candidate_decision(
                observation["id"], "saved", action
            )

        metrics["reasons"] = reasons
        return self._finish(metrics, started)

    def _save_or_merge(self, candidate: MemoryCandidate, source_message_id: str) -> str:
        existing = self.database.find_active_memory_by_key(self.user_id, candidate.memory_key)
        if existing is not None:
            existing_polarity = "negative" if _is_negative(existing["content"]) else "positive"
            if existing_polarity == candidate.polarity and _same_meaning(
                existing["content"], candidate.content
            ):
                self.database.add_memory_provenance(
                    self.user_id,
                    existing["id"],
                    source_type="autonomous_extraction",
                    source_id=source_message_id,
                )
                self.database.update_memory_record(
                    self.user_id,
                    existing["id"],
                    confidence=max(float(existing["confidence"]), candidate.confidence),
                )
                return "merged"
            self.database.update_memory_record(
                self.user_id, existing["id"], status="superseded"
            )
            action = "superseded"
        else:
            action = "saved"

        self.database.create_memory_record(
            self.user_id,
            kind=candidate.kind,
            content=candidate.content,
            scope="global",
            memory_key=candidate.memory_key,
            auto_generated=True,
            confidence=candidate.confidence,
            expires_at=candidate.expires_at,
            consent_granted=True,
            source_type="autonomous_extraction",
            source_id=source_message_id,
        )
        return action

    def summary(self) -> dict:
        records = self.database.list_memory_records(self.user_id)
        sections = {kind: [] for kind in ("profile", "preference", "fact", "episode")}
        for record in records:
            sections.setdefault(record["kind"], []).append(record)
        generated_at = datetime.now(timezone.utc).isoformat()
        return {
            "generated_at": generated_at,
            "settings": self.database.get_memory_settings(self.user_id),
            "sections": sections,
            "record_count": len(records),
        }

    def rebuild_summary(self) -> dict:
        summary = self.summary()
        self.database.set_metadata(
            f"memory_summary:{self.user_id}",
            json.dumps(
                {
                    "generated_at": summary["generated_at"],
                    "record_ids": [
                        item["id"]
                        for items in summary["sections"].values()
                        for item in items
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return summary

    def last_extraction_trace(self) -> dict:
        raw = self.database.get_metadata(
            f"memory_extraction_trace:{self.user_id}:{self.thread_id}"
        )
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _finish(self, metrics: dict, started: float) -> dict:
        metrics["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        self.database.set_metadata(
            f"memory_extraction_trace:{self.user_id}:{self.thread_id}",
            json.dumps(metrics, ensure_ascii=False, sort_keys=True),
        )
        logger.info(
            "memory extraction enabled=%s candidates=%s saved=%s merged=%s superseded=%s rejected=%s pending=%s latency_ms=%.3f",
            metrics["enabled"],
            metrics["candidate_count"],
            metrics["saved_count"],
            metrics["merged_count"],
            metrics["superseded_count"],
            metrics["rejected_count"],
            metrics["pending_count"],
            metrics["latency_ms"],
        )
        return metrics


def extract_candidates(text: str) -> tuple[list[MemoryCandidate], list[str]]:
    normalized = " ".join(text.split())
    if not normalized or re.search(r"\b(?:запомни|remember)\b", normalized, re.IGNORECASE):
        return [], ["explicit_memory_flow"] if normalized else ["empty"]
    sentences = [item.strip(" \t\r\n.!?") for item in re.split(r"(?<=[.!?])\s+", normalized)]
    candidates: list[MemoryCandidate] = []
    rejected: list[str] = []
    for sentence in sentences:
        if len(sentence) < 8:
            rejected.append("too_short")
            continue
        if IRONY_PATTERN.search(sentence):
            rejected.append("irony")
            continue
        if SENSITIVE_PATTERN.search(sentence):
            rejected.append("sensitive")
            continue
        candidate = _candidate_from_sentence(sentence)
        if candidate is None:
            rejected.append("not_durable")
            continue
        candidates.append(candidate)
    return candidates, rejected


def _candidate_from_sentence(sentence: str) -> MemoryCandidate | None:
    lowered = sentence.casefold()
    preference_patterns = (
        (r"^(?:я\s+)?(?:больше\s+не\s+люблю|перестал(?:а)?\s+любить|мне\s+больше\s+не\s+нравится)\s+(.+)$", "negative", True),
        (r"^(?:я\s+)?(?:не\s+люблю|мне\s+не\s+нравится)\s+(.+)$", "negative", False),
        (r"^(?:я\s+)?(?:люблю|обожаю|предпочитаю|мне\s+нравится)\s+(.+)$", "positive", False),
    )
    for pattern, polarity, correction in preference_patterns:
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if match:
            subject = _clean_subject(match.group(1))
            if not subject:
                return None
            content = ("Не любит " if polarity == "negative" else "Любит ") + subject
            return MemoryCandidate(
                memory_key=f"preference:{_key_subject(subject)}",
                kind="preference",
                content=content,
                polarity=polarity,
                confidence=0.92 if correction else 0.78,
                threshold=1 if correction else 2,
                correction=correction,
            )

    profile_patterns = (
        (r"^я\s+(?:живу|проживаю)\s+в\s+(.+)$", "location", "Живёт в {}"),
        (r"^я\s+работаю\s+(?:в|на)\s+(.+)$", "work", "Работает в {}"),
        (r"^я\s+учусь\s+(?:в|на)\s+(.+)$", "education", "Учится в {}"),
        (r"^(?:меня\s+зовут|мой\s+псевдоним)\s+(.+)$", "name", "Имя или псевдоним: {}"),
    )
    for pattern, key, template in profile_patterns:
        match = re.match(pattern, sentence, flags=re.IGNORECASE)
        if match:
            value = _clean_subject(match.group(1))
            if value:
                return MemoryCandidate(
                    memory_key=f"profile:{key}",
                    kind="profile",
                    content=template.format(value),
                    polarity="positive",
                    confidence=0.9,
                    threshold=1,
                )

    fact_match = re.match(
        r"^у\s+меня\s+(?:компьютер|ноутбук|телефон|видеокарта|процессор)\s+(.+)$",
        sentence,
        flags=re.IGNORECASE,
    )
    if fact_match:
        value = _clean_subject(fact_match.group(1))
        return MemoryCandidate(
            memory_key=f"fact:device:{_key_subject(value)}",
            kind="fact",
            content=f"Использует устройство: {value}",
            polarity="positive",
            confidence=0.72,
            threshold=2,
        )

    episode_match = re.match(
        r"^я\s+(?:начал|начала|закончил|закончила)\s+(.+)$",
        sentence,
        flags=re.IGNORECASE,
    )
    if episode_match:
        value = _clean_subject(episode_match.group(1))
        expires_at = (datetime.now(timezone.utc) + timedelta(days=180)).isoformat()
        return MemoryCandidate(
            memory_key=f"episode:{_key_subject(value)}",
            kind="episode",
            content=value,
            polarity="positive",
            confidence=0.7,
            threshold=2,
            expires_at=expires_at,
        )
    return None


def _clean_subject(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n.,!?;:\"'«»")[:300]


def _key_subject(value: str) -> str:
    return "-".join(re.findall(r"[a-zа-яе0-9]+", value.casefold().replace("ё", "е")))[:160]


def _is_negative(content: str) -> bool:
    return bool(re.search(r"\b(?:не|нет|никогда|not|never)\b", content.casefold()))


def _same_meaning(left: str, right: str) -> bool:
    normalize = lambda value: " ".join(
        re.findall(r"[a-zа-яе0-9]+", value.casefold().replace("ё", "е"))
    )
    return normalize(left) == normalize(right)
