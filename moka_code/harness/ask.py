"""The ``ask`` tool: questions the model puts to the user.

It never reaches a transport (sandbox or not): the harness yields one
``events.Questions`` for each run of adjacent ``ask`` calls, waits for
``Harness.answer_questions`` and answers each call in call order.
"""

import json
from typing import Any, Dict, List, Optional, Tuple, Union

from moka_code.harness import events

NAME = "ask"
MAX_OPTIONS = 6
MAX_LABEL = 12

# What ``result`` tells the model (a replayed transcript reads them back).
DISMISSED = "The user dismissed the question without answering."
UNANSWERED = "The user left this question unanswered."
PICKED = "The user picked: "
WROTE = "The user wrote: "


def parse(call_id: str, args: Any) -> Union[events.Question, str]:
    """The question an ``ask`` call holds, or why its arguments are invalid."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    question = args.get("question")
    if not isinstance(question, str) or not question.strip():
        return "'question' must be a non-empty string"
    options = args.get("options", [])
    if (not isinstance(options, list)
            or not all(isinstance(o, str) and o.strip() for o in options)):
        return "'options' must be a list of non-empty strings"
    if len(options) > MAX_OPTIONS:
        return f"at most {MAX_OPTIONS} options"
    multiple = args.get("multiple", False)
    if not isinstance(multiple, bool):
        return "'multiple' must be true or false"
    label = args.get("label", "")
    if not isinstance(label, str):
        return "'label' must be a string"
    return events.Question(id=call_id, question=question.strip(),
                           options=[o.strip() for o in options], multiple=multiple,
                           label=label.strip()[:MAX_LABEL])


def group(tool_calls: List[Dict[str, Any]], start: int) -> List[events.Question]:
    """The valid questions of the run of ``ask`` calls starting at ``start``
    (an invalid one is skipped here and answered with its error later)."""
    questions: List[events.Question] = []
    for call in tool_calls[start:]:
        if call["function"]["name"] != NAME:
            break
        try:
            args = json.loads(call["function"]["arguments"])
        except json.JSONDecodeError:
            continue
        parsed = parse(call["id"], args)
        if isinstance(parsed, events.Question):
            questions.append(parsed)
    return questions


def result(answer: Optional[events.Answer], dismissed: bool) -> Tuple[str, str]:
    """``(outcome, text for the model)`` for one question."""
    if dismissed:
        return "cancelled", DISMISSED
    if answer is None or not (answer.choices or answer.text.strip()):
        return "completed", UNANSWERED
    lines = []
    if answer.choices:
        lines.append(PICKED + ", ".join(answer.choices))
    if answer.text.strip():
        lines.append(WROTE + answer.text.strip())
    return "completed", "\n".join(lines)
