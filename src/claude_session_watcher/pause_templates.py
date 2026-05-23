from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PAUSE_MESSAGE = (
    "Pause after the current safe checkpoint. Do not start new work. "
    "Wait until I send continue."
)


@dataclass(frozen=True, slots=True)
class PauseTemplate:
    key: str
    label: str
    message: str


PAUSE_TEMPLATES: dict[str, PauseTemplate] = {
    "minimal": PauseTemplate(
        key="minimal",
        label="Minimal",
        message=DEFAULT_PAUSE_MESSAGE,
    ),
    "worklog": PauseTemplate(
        key="worklog",
        label="Worklog",
        message=(
            "Pause after the current safe checkpoint. Before pausing, update WORKLOG.md "
            "with the current objective, changed files, completed work, open questions, "
            "the next exact step, and any commands or tests that should run after resume. "
            "Do not start new implementation. Wait until I send continue."
        ),
    ),
    "handoff": PauseTemplate(
        key="handoff",
        label="Handoff",
        message=(
            "Pause after the current safe checkpoint. Prepare a concise handoff in "
            "WORKLOG.md with current task state, assumptions, modified files, remaining "
            "work, risks, and a ready-to-use prompt for another coding agent. Do not "
            "start new implementation. Wait until I send continue."
        ),
    ),
}

CUSTOM_TEMPLATE = "custom"


def pause_template_options() -> list[PauseTemplate]:
    return list(PAUSE_TEMPLATES.values())


def normalize_pause_template(value: str | None) -> str:
    if not value:
        return CUSTOM_TEMPLATE
    value = value.strip().lower()
    if value in PAUSE_TEMPLATES or value == CUSTOM_TEMPLATE:
        return value
    return CUSTOM_TEMPLATE


def render_pause_message(template: str | None, custom_message: str) -> str:
    key = normalize_pause_template(template)
    if key == CUSTOM_TEMPLATE:
        return custom_message
    return PAUSE_TEMPLATES[key].message
