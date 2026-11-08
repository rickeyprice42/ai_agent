import { useState } from "react";
import type { ChatQuestionItem as ChatQuestion } from "./types";

export function ChatClarification({ question, disabled, onAnswer }: { question: ChatQuestion; disabled: boolean; onAnswer: (answer: { option_id?: string; text?: string }) => void }) {
  const [answer, setAnswer] = useState("");
  return <section className="clarification-card" aria-label="Уточнение Avelin"><strong>{question.prompt}</strong>
    <div className="clarification-options">{question.options.map((option) => <button className="ghost-button" disabled={disabled} type="button" key={option.id} onClick={() => onAnswer({ option_id: option.id })}>{option.label}</button>)}</div>
    {question.allow_free_text ? <form onSubmit={(event) => { event.preventDefault(); if (answer.trim() && !disabled) onAnswer({ text: answer.trim() }); }}><label className="sr-only" htmlFor={`answer-${question.id}`}>Свой ответ</label><input id={`answer-${question.id}`} placeholder="Или напиши свой ответ" value={answer} onChange={(event) => setAnswer(event.target.value)} maxLength={4000} disabled={disabled} /><button className="text-button" type="submit" disabled={!answer.trim() || disabled}>Ответить</button></form> : null}
  </section>;
}
