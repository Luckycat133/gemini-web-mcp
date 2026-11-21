"""Public Task browser observations; no VM identity or credentials."""

from dataclasses import dataclass

from .artifacts import Artifact


@dataclass(frozen=True)
class TaskBrowserObservation:
    chat_id: str
    response_id: str
    candidate_id: str
    state: str
    frame_sha256: str | None = None
    artifacts: tuple[Artifact, ...] = ()
    action_id: str | None = None
    action_state: str | None = None
    input_sent: bool | None = False
    upstream_status: int | None = None
    exclusive_control: bool = False
    external_effect_verified: bool = False
