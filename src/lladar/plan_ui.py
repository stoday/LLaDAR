"""Terminal rendering and keyboard input for the planner."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import copy_context
from threading import Thread, Event
from time import monotonic
from typing import Any, Callable
import json
import os
import sys
from .language import current_language

_PLAN_UI = {
    "en": {
        "saved": "Plan saved to",
        "commands": "Planned commands (not executed)",
        "invalid_commands": "The planned commands are missing required arguments or use unsupported options; revise them before execution.",
        "stage_create": "1/4 Prepare questions · create test-dataset / situation",
        "stage_run-agent": "2/4 Connect the Agent · run-agent",
        "stage_eval": "3/4 Decide how to evaluate · eval",
        "stage_report": "4/4 Present the results · report",
        "heading": "Need to confirm",
        "context": "Context so far:",
        "recommended": " (recommended)",
        "choice_hint": "Enter a number, or type your own answer.",
        "keyboard_hint": "Use the up/down keys, then press Enter to confirm.",
        "free_option": "Write my own answer",
        "free_option_description": "Type the answer in your own words.",
        "free_input": "Please answer the question above:",
        "working": "Working",
        "free_hint": "Please answer directly. If you are unsure, say so.",
        "intro": (
            "\n── LLaDAR Test Planning ─────────────────────────────\n"
            "Define the test objective, inputs, and evaluation criteria.\n"
            "Planning does not run tests or modify your code.\n"
            "Press Ctrl+C to exit.\n"
            "──────────────────────────────────────────────────"
        ),
    },
    "zh-TW": {
        "saved": "計畫已保存至",
        "commands": "計畫指令（尚未執行）",
        "invalid_commands": "計畫指令缺少必要參數，或使用了不支援的選項；需要修正後才能執行。",
        "stage_create": "1/4 準備題目 · create test-dataset / situation",
        "stage_run-agent": "2/4 連接受測 Agent · run-agent",
        "stage_eval": "3/4 決定評估方式 · eval",
        "stage_report": "4/4 決定報告呈現 · report",
        "heading": "需要確認",
        "context": "前情提要：",
        "recommended": "  (建議)",
        "choice_hint": "請輸入選項編號，或直接輸入你的回答。",
        "keyboard_hint": "請使用上下鍵、按 Enter 確認。",
        "free_option": "自己輸入回答",
        "free_option_description": "用自己的話直接回答這個問題。",
        "free_input": "請直接回答上面的問題：",
        "working": "正在思考",
        "free_hint": "請直接回答；如果不確定，也可以說明你目前的想法。",
        "intro": (
            "\n── LLaDAR 測試規劃 ───────────────────────────────\n"
            "定義測試目標、輸入資料與評估準則。\n"
            "規劃過程不會執行測試或修改程式。\n"
            "按 Ctrl+C 結束。\n"
            "──────────────────────────────────────────────────"
        ),
    },
}


def _plan_ui(key: str) -> str:
    language = _PLAN_UI.get(current_language().ui_lang, _PLAN_UI["en"])
    return language[key]


def _render_question(question: dict[str, Any], selected: int | None = None,
                     interactive: bool = False) -> str:
    context = str(question.get("context", "")).strip()
    message = str(question.get("message", "")).strip()
    options = question.get("options", [])
    if not isinstance(options, list):
        options = []
    rows: list[tuple[str, str]] = []
    for option in options:
        if isinstance(option, str):
            rows.append((option, ""))
        elif isinstance(option, dict) and isinstance(option.get("label"), str):
            rows.append((option["label"], str(option.get("description", ""))))
    recommended = question.get("recommended")
    lines = ["", f"── {_plan_ui('heading')} ─────────────────────────────"]
    if context:
        lines.extend([_plan_ui('context'), context, ""])
    lines.append(message)
    if rows:
        lines.append("")
        for index, (label, description) in enumerate(rows, start=1):
            suffix = f" — {description}" if description else ""
            marker = _plan_ui("recommended") if isinstance(recommended, int) and recommended == index - 1 else ""
            option_line = f"  {index}. {label}{suffix}{marker}"
            if selected == index - 1:
                option_line = "\x1b[7m" + option_line + "\x1b[0m"
            lines.append(option_line)
        lines.append("")
        hint = _plan_ui("keyboard_hint" if interactive else "choice_hint")
        lines.append(hint)
    else:
        lines.append("")
        lines.append(_plan_ui('free_hint'))
    lines.append("──────────────────────────────────────────────────")
    return "\n".join(lines)


@contextmanager
def _raw_keyboard():
    """Read one key at a time without adding a terminal dependency."""
    if os.name == "nt":
        yield None
        return
    import termios
    import tty

    file_descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(file_descriptor)
    try:
        tty.setcbreak(file_descriptor)
        yield None
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, previous)


def _read_key() -> str:
    if os.name == "nt":
        import msvcrt

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
        return {"\r": "enter", "\x03": "ctrl-c"}.get(key, key)
    key = sys.stdin.read(1)
    if key == "\x03":
        return "ctrl-c"
    if key == "\x1b":
        sequence = sys.stdin.read(2)
        return {"[A": "up", "[B": "down"}.get(sequence, "")
    return {"\n": "enter", "\r": "enter"}.get(key, key)


def _choose_option(question: dict[str, Any]) -> str:
    options = question.get("options", [])
    recommended = question.get("recommended")
    selected = recommended if isinstance(recommended, int) and 0 <= recommended < len(options) else 0
    rendered = _render_question(question, selected=selected, interactive=True)
    line_count = rendered.count("\n") + 1
    sys.stdout.write(rendered)
    sys.stdout.flush()
    with _raw_keyboard():
        while True:
            key = _read_key()
            if key == "ctrl-c":
                raise KeyboardInterrupt
            if key in {"up", "down"}:
                selected = (selected + (1 if key == "down" else -1)) % len(options)
                replacement = _render_question(question, selected=selected, interactive=True)
                cleared = "\n".join("\x1b[2K\r" + line for line in replacement.splitlines())
                sys.stdout.write(f"\x1b[{line_count - 1}A" + cleared)
                sys.stdout.flush()
                continue
            if key == "enter":
                sys.stdout.write("\n")
                sys.stdout.flush()
                return str(selected + 1)
            if len(key) == 1 and key.isprintable():
                # Restore the terminal before input() so the user can type a
                # complete free-form answer. Preserve the first character.
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "__free_text__" + key


def _answer_from_option(answer: str, question: dict[str, Any]) -> str:
    options = question.get("options", [])
    if not isinstance(options, list) or not answer.strip().isdigit():
        return answer
    index = int(answer.strip()) - 1
    if not 0 <= index < len(options):
        return answer
    option = options[index]
    if isinstance(option, str):
        return f"{answer.strip()}. {option}"
    if isinstance(option, dict) and isinstance(option.get("label"), str):
        return f"{answer.strip()}. {option['label']}"
    return answer


def _is_free_input_option(answer: str, question: dict[str, Any]) -> bool:
    options = question.get("options", [])
    if not isinstance(options, list) or not answer.strip().isdigit():
        return False
    index = int(answer.strip()) - 1
    if not 0 <= index < len(options):
        return False
    option = options[index]
    if isinstance(option, dict) and option.get("type") == "free_input":
        return True
    label = option if isinstance(option, str) else option.get("label", "") if isinstance(option, dict) else ""
    return isinstance(label, str) and any(marker in label for marker in (
        "其他", "自行輸入", "自己輸入", "自由輸入", "直接輸入", "直接在對話中輸入",
        "自訂問題", "自己描述", "type your own",
    ))


def _is_free_label(option: Any) -> bool:
    if isinstance(option, dict) and option.get("type") == "free_input":
        return True
    label = option if isinstance(option, str) else option.get("label", "") if isinstance(option, dict) else ""
    return isinstance(label, str) and any(marker in label for marker in (
        "其他", "自行輸入", "自己輸入", "自由輸入", "直接輸入", "自訂問題", "自己描述",
        "Write my own", "type your own",
    ))


def _ask_interruptibly(agent: PlanAgent, transcript: list[dict[str, str]],
                       progress: Callable[[float], None] | None = None,
                       progress_interval: float = 10.0) -> dict[str, Any]:
    """Wait for the model without making Ctrl+C depend on the SDK's blocking call."""
    finished = Event()
    outcome: dict[str, Any] = {}

    def invoke():
        try:
            outcome["result"] = agent.ask(transcript)
        except BaseException as error:
            outcome["error"] = error
        finally:
            finished.set()

    context = copy_context()
    Thread(target=context.run, args=(invoke,), daemon=True).start()
    started = monotonic()
    next_progress = started + progress_interval
    try:
        while not finished.wait(0.1):
            now = monotonic()
            if progress is not None and now >= next_progress:
                progress(now - started)
                next_progress = now + progress_interval
            if os.name == "nt":
                import msvcrt

                while msvcrt.kbhit():
                    if msvcrt.getwch() == "\x03":
                        raise KeyboardInterrupt
    except KeyboardInterrupt:
        # The model SDK call is isolated in a daemon thread. Do not join it:
        # Ctrl+C must return control to the CLI immediately.
        raise
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]
