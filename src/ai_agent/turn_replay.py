"""Shrink completed tool exchanges without leaving unmatched calls or outputs."""

import json
from typing import Iterator

from ai_agent.security import mark_untrusted_tool_output
from ai_agent.tool_payloads import compact_tool_output
from ai_agent.types import Message


_REPLAY_ARGUMENTS = {"path", "url", "query", "start_line", "end_line", "start_column", "start_page", "end_page", "start_character"}


def compact_finished_exchanges(messages: list[Message], *, query: str) -> Iterator[list[Message]]:
    """Yield progressively smaller replays, oldest complete exchange first.

    Persisted history and on-disk outputs retain the full values. Each replay
    either keeps an entire exchange or replaces it with cited observations.
    """
    index = 0
    working = list(messages)
    while index < len(working):
        request = working[index]
        if request.role != "assistant" or not request.tool_calls:
            index += 1
            continue
        end = index + 1
        while end < len(working) and working[end].role == "tool":
            end += 1
        calls = request.tool_calls
        outputs = working[index + 1:end]
        call_ids = [call.call_id for call in calls]
        output_ids = [result.tool_call_id for result in outputs]
        if not any(call_ids) and not any(output_ids) and [call.name for call in calls] == [result.name for result in outputs]:
            ordered_outputs = outputs
        elif all(call_ids) and len(set(call_ids)) == len(call_ids) == len(output_ids) and set(call_ids) == set(output_ids):
            by_id = dict(zip(output_ids, outputs))
            ordered_outputs = [by_id[call_id] for call_id in call_ids]
        else:
            index = end
            continue
        observations = []
        for call, result in zip(calls, ordered_outputs):
            location = {key: str(value)[:220] for key, value in call.arguments.items() if key in _REPLAY_ARGUMENTS}
            observations.append({
                "tool": call.name,
                "location": location,
                "observation": compact_tool_output(result.content, 480, query=query),
            })
        compacted = Message(
            role="user",
            name="agent_runtime",
            content="Краткая запись уже выполненных вызовов. При необходимости перечитай конкретный фрагмент файла или источника.\n"
                    + mark_untrusted_tool_output("tool_replay", json.dumps(observations, ensure_ascii=False)),
        )
        working[index:end] = [compacted]
        index += 1
        yield list(working)
