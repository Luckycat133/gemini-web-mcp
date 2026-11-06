"""Bounded Task browser screenshots and explicit, non-replayed inputs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
from dataclasses import dataclass, replace
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
from typing import Any, cast

from ..domain import DomainErrorCode, DomainResult, DomainWarning
from ..domain.artifacts import Artifact, ArtifactKind, ArtifactState, ArtifactVerification, ArtifactVerificationStatus
from ..domain.task_browser import TaskBrowserObservation
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.browser_action_store import BrowserActionStore
from .artifacts import artifact_from_local_path
from .deadlines import await_before_deadline
from .task_browser_sessions import pooled_task_browser_connection

_CID = re.compile(r'c_[A-Za-z0-9_-]{1,254}\Z')
_RID = re.compile(r'r_[A-Za-z0-9_-]{1,254}\Z')
_RCID = re.compile(r'rc_[A-Za-z0-9_-]{1,253}\Z')
_ACTION = re.compile(r'[A-Za-z0-9_-]{20,128}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_MAX_FRAME_BYTES = 20 * 1024 * 1024
_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
_KEYS = frozenset({'Enter', 'Tab', 'Escape', 'Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp',
                   'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown', 'Space', 'Ctrl+l', 'Ctrl+a', 'Ctrl+r',
                   'Alt+Left', 'Alt+Right'})


def _validate_input(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get('kind') not in {'click', 'scroll', 'key', 'type'}:
        raise ValueError('browser_input.kind must be click, scroll, key or type.')
    kind = value['kind']
    allowed = {'click': {'kind', 'x', 'y', 'button'}, 'scroll': {'kind', 'x', 'y', 'steps'},
               'key': {'kind', 'key'}, 'type': {'kind', 'text'}}[kind]
    if not set(value) <= allowed:
        raise ValueError('Unknown or inapplicable browser input fields.')
    if kind in {'click', 'scroll'}:
        if any(type(value.get(key)) is not int or not 0 <= value[key] < 4096 for key in ('x', 'y')):
            raise ValueError('Browser coordinates must be exact integers inside the screenshot.')
    if kind == 'click' and value.get('button', 'left') not in {'left', 'right', 'middle'}:
        raise ValueError('Unknown mouse button.')
    if kind == 'scroll' and (type(value.get('steps')) is not int or not 1 <= abs(value['steps']) <= 10):
        raise ValueError('scroll steps must be a nonzero integer between -10 and 10.')
    if kind == 'key' and value.get('key') not in _KEYS:
        raise ValueError('Unknown browser key.')
    if kind == 'type' and (not isinstance(value.get('text'), str) or not 1 <= len(value['text']) <= 2048
                           or any(ord(char) < 32 and char not in '\n\t' or 0xD800 <= ord(char) <= 0xDFFF for char in value['text'])):
        raise ValueError('Browser text must contain 1 to 2048 valid characters.')
    return value


def _save_frame(data: bytes, chat_id: str, rid: str, root: Path) -> tuple[Artifact, str]:
    from PIL import Image

    if not data or len(data) > _MAX_FRAME_BYTES:
        raise ValueError('The browser frame size is invalid.')
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if image.format != 'PNG' or not 1 <= width <= 4096 or not 1 <= height <= 4096:
            raise ValueError('The browser frame is not a bounded PNG.')
        image.verify()
    digest = hashlib.sha256(data).hexdigest()
    identity = hashlib.sha256((chat_id + '|' + rid).encode()).hexdigest()[:24]
    directory = root / 'artifacts' / 'task-browser' / identity
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (digest + '.png')
    if not path.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('The saved browser frame did not verify.')
    artifact = artifact_from_local_path(ArtifactKind.IMAGE, str(path), title='Task browser', source_chat_id=chat_id)
    if artifact.state is not ArtifactState.LOCAL:
        raise ValueError('The browser frame could not be saved.')
    return replace(artifact, width=width, height=height, verification=ArtifactVerification(
        ArtifactVerificationStatus.VERIFIED, methods=('rfb_complete_frame', 'png_decoded', 'sha256_checked'),
        details={'sha256': digest})), digest


@dataclass(frozen=True)
class TaskBrowserDependencies:
    client_initializer: Callable[[], Awaitable[Any]]
    client_provider: Callable[[], Any]
    read_turn: Callable[..., Awaitable[dict[str, Any] | None]]
    read_task: Callable[[Any, str], Awaitable[dict[str, Any] | None]]
    connection: Any = pooled_task_browser_connection
    account_key: Callable[[], str | None] = current_account_key


class TaskBrowserService:
    def __init__(self, dependencies: TaskBrowserDependencies, *, store: BrowserActionStore | None = None):
        self.dependencies = dependencies
        self.store = store or BrowserActionStore()

    async def observe(self, chat_id: str, **arguments: Any) -> DomainResult[TaskBrowserObservation]:
        owner = self.dependencies.account_key()
        bound_owner: list[str] = []

        async def observe_before_dispatch() -> DomainResult[TaskBrowserObservation]:
            for attempt in range(3):
                if bound_owner and self.dependencies.account_key() != bound_owner[0]:
                    return self._account_failure()
                result = await self._observe(chat_id, _bound_owner=bound_owner, **arguments)
                diagnostics = result.meta.details
                transient = (not result.ok and result.data is not None and result.data.action_state is None
                             and result.data.input_sent is False
                             and diagnostics.get('phase') in {'connect', 'capture_frame'}
                             and diagnostics.get('error_type') in {'IncompleteReadError', 'TimeoutError', 'ConnectionError'})
                if not transient or attempt == 2:
                    return result
                # Like the native viewer, retry connection setup only. No action
                # was claimed or dispatched, and every attempt refreshes identity.
                await asyncio.sleep(2)
            raise AssertionError('unreachable browser observation retry')

        try:
            result = await await_before_deadline(observe_before_dispatch(), timeout=150)
        except TimeoutError:
            result = DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED,
                'The browser deadline expired; a claimed action must not be repeated.', verification_status='browser_unverified')
        owner = bound_owner[0] if bound_owner else owner
        if owner is not None and self.dependencies.account_key() != owner:
            return self._account_failure()
        return cast(DomainResult[TaskBrowserObservation], result)

    async def _observe(self, chat_id: str, *, response_id: str | None = None, candidate_id: str | None = None,
                      browser_input: dict[str, Any] | None = None, action_id: str | None = None,
                      expected_frame_sha256: str | None = None,
                      known_frame_sha256: str | None = None,
                      _bound_owner: list[str] | None = None) -> DomainResult[TaskBrowserObservation]:
        if not isinstance(chat_id, str) or not _CID.fullmatch(chat_id):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'An exact Task c_ ID is required.')
        if (response_id is not None and (not isinstance(response_id, str) or not _RID.fullmatch(response_id))
                or candidate_id is not None and (not isinstance(candidate_id, str) or not _RCID.fullmatch(candidate_id))):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Invalid Task response or candidate ID.')
        if bool(response_id) != bool(candidate_id):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Provide both exact response and candidate IDs.')
        if known_frame_sha256 is not None and (
            browser_input is not None or not isinstance(known_frame_sha256, str) or not _HASH.fullmatch(known_frame_sha256)
        ):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
                                        'known_frame_sha256 must be a SHA-256 digest for observation without input.')
        if browser_input is not None:
            try:
                _validate_input(browser_input)
                if not response_id or not candidate_id or not isinstance(action_id, str) or not _ACTION.fullmatch(action_id):
                    raise ValueError('Browser input requires the screenshot response/candidate and a unique action_id.')
                if not isinstance(expected_frame_sha256, str) or not _HASH.fullmatch(expected_frame_sha256):
                    raise ValueError('Browser input requires the screenshot frame hash.')
            except (ValueError, TypeError) as exc:
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, str(exc))
        elif action_id is not None or expected_frame_sha256 is not None:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Input metadata requires browser_input.')
        owner = self.dependencies.account_key()
        claimed = False
        input_sent: bool | None = False
        observed: dict[str, Any] | None = None
        phase = 'initialize'
        try:
            await await_before_deadline(self.dependencies.client_initializer(), timeout=60)
            now = self.dependencies.account_key()
            if not now or owner is not None and owner != now:
                return self._account_failure()
            owner = now
            if _bound_owner is not None:
                _bound_owner.append(owner)
            client = self.dependencies.client_provider()
            phase = 'action_lookup'
            if browser_input is not None and action_id is not None:
                previous = self.store.read(owner, action_id)
                if previous is not None:
                    if previous.get('candidate_id') is not None and previous['candidate_id'] != candidate_id:
                        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'The action ID belongs to another candidate.')
                    if (previous['chat_id'], previous['response_id'], previous['action'], previous['expected_frame_sha256']) != (
                            chat_id, response_id, browser_input['kind'], expected_frame_sha256):
                        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'The browser action ID belongs to another request.')
                    return DomainResult.success(TaskBrowserObservation(
                        chat_id, response_id or '', candidate_id or '', 'duplicate', action_id=action_id,
                        action_state=previous['state'], input_sent=False),
                        warnings=(DomainWarning(code='browser_action_consumed', message='This action ID was already consumed; this call sent no input. Its previous outcome applies only to the original request.'),),
                        verification_status='browser_input_not_repeated')
            phase = 'read_task'
            task = await await_before_deadline(self.dependencies.read_task(client, chat_id), timeout=30)
            if self.dependencies.account_key() != owner:
                return self._account_failure()
            status = task.get('upstream_status') if isinstance(task, dict) and task.get('chat_id') == chat_id else None
            if browser_input is not None and (type(status) is not int or status not in {3, 5}):
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                            'Input requires a Task verified as completed or waiting for input; exclusive takeover is unavailable.',
                                            verification_status='browser_task_not_quiescent')
            phase = 'read_turn'
            observed = await await_before_deadline(self.dependencies.read_turn(
                client, chat_id, response_id, candidate_id), timeout=60)
            if self.dependencies.account_key() != owner:
                return self._account_failure()
            if not observed or observed.get('chat_id') != chat_id:
                raise ValueError('The exact Task browser response is unavailable.')
            candidate = observed.get('candidate')
            rid = observed.get('response_id')
            if (not isinstance(candidate, list) or not candidate or not isinstance(rid, str)
                    or response_id is not None and rid != response_id
                    or candidate_id is not None and candidate[0] != candidate_id):
                raise ValueError('The browser response identity did not match.')
            response_id, candidate_id = rid, candidate[0]
            phase = 'connect'
            async with self.dependencies.connection(client, observed) as rfb:
                phase = 'capture_frame'
                before = await await_before_deadline(rfb.screenshot_png(), timeout=30)
                if self.dependencies.account_key() != owner:
                    return self._account_failure()
                if browser_input is not None:
                    if hashlib.sha256(before).hexdigest() != expected_frame_sha256:
                        artifact, digest = _save_frame(before, chat_id, rid, self.store.path.resolve().parent)
                        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED,
                            'The browser frame changed; inspect the new screenshot before sending a new action.',
                            data=TaskBrowserObservation(chat_id, rid, candidate_id or '', 'frame_changed', digest, (artifact,)),
                            verification_status='browser_frame_changed')
                    if browser_input['kind'] in {'click', 'scroll'} and (browser_input['x'] >= rfb.width or browser_input['y'] >= rfb.height):
                        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'The coordinates are outside the current frame.')
                    phase = 'read_task_before_input'
                    fresh = await await_before_deadline(self.dependencies.read_task(client, chat_id), timeout=30)
                    if self.dependencies.account_key() != owner:
                        return self._account_failure()
                    if not fresh or fresh.get('chat_id') != chat_id or type(fresh.get('upstream_status')) is not int or fresh['upstream_status'] != status:
                        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, 'The Task state changed before input.')
                    phase = 'claim'
                    claim = self.store.claim(account_key=owner, chat_id=chat_id, response_id=rid, action_id=action_id or '',
                                             action=browser_input['kind'], expected_frame_sha256=expected_frame_sha256 or '',
                                             candidate_id=candidate_id)
                    if not claim['new']:
                        return DomainResult.success(TaskBrowserObservation(chat_id, rid, candidate_id or '', 'duplicate',
                            action_id=action_id, action_state=claim['state'], input_sent=False), verification_status='browser_input_not_repeated')
                    claimed = True
                    phase = 'dispatch'
                    self.store.finish(owner, action_id or '', state='dispatched')
                    input_sent = None
                    kind = browser_input['kind']
                    if kind == 'click':
                        rfb.click(browser_input['x'], browser_input['y'], browser_input.get('button', 'left'))
                    elif kind == 'scroll':
                        rfb.scroll(browser_input['x'], browser_input['y'], browser_input['steps'])
                    elif kind == 'key':
                        rfb.key(browser_input['key'])
                    else:
                        rfb.type_text(browser_input['text'])
                    input_sent = True
                    phase = 'capture_after_input'
                    frame = await await_before_deadline(rfb.screenshot_png(), timeout=30)
                    if self.dependencies.account_key() != owner:
                        return self._account_failure()
                else:
                    frame = before
                    if (known_frame_sha256 is not None and len(frame) <= _MAX_FRAME_BYTES
                            and frame.startswith(_PNG_SIGNATURE)
                            and hashlib.sha256(frame).hexdigest() == known_frame_sha256):
                        return DomainResult.success(TaskBrowserObservation(
                            chat_id, rid, candidate_id or '', 'frame_unchanged',
                            frame_sha256=known_frame_sha256, upstream_status=status,
                        ), verification_status='browser_frame_hash_matched')
                phase = 'save_frame'
                artifact, digest = _save_frame(frame, chat_id, rid, self.store.path.resolve().parent)
                if self.dependencies.account_key() != owner:
                    return self._account_failure()
                if claimed:
                    self.store.finish(owner, action_id or '', state='verified', artifact_id=artifact.id)
                return DomainResult.success(TaskBrowserObservation(chat_id, rid, candidate_id or '', 'input_sent' if claimed else 'frame_ready',
                    digest, (artifact,), action_id, 'verified' if claimed else None, input_sent, status),
                    warnings=(DomainWarning(code='browser_effect_unverified', message='Input was dispatched and a subsequent frame saved; website effects need independent verification.'),) if claimed else (),
                    verification_status='browser_input_sent_frame_observed' if claimed else 'browser_frame_verified')
        except Exception as exc:
            if owner and self.dependencies.account_key() != owner:
                return self._account_failure()
            if claimed and owner:
                self.store.finish(owner, action_id or '', state='outcome_unverified')
            data = TaskBrowserObservation(chat_id, response_id or '', candidate_id or '', 'outcome_unverified',
                action_id=action_id, action_state='outcome_unverified' if claimed else None, input_sent=input_sent)
            code = DomainErrorCode.CAPABILITY_UNAVAILABLE if getattr(exc, 'code', '') == 'CAPABILITY_UNAVAILABLE' else DomainErrorCode.VERIFICATION_FAILED
            message = ('Install gemini-web-mcp[browser-control] for remote browser access.' if getattr(exc, 'code', '') == 'CAPABILITY_UNAVAILABLE'
                       else 'The Task browser observation or input could not be verified; do not repeat a claimed action.')
            diagnostics: dict[str, Any] = {'phase': phase, 'error_type': type(exc).__name__}
            if isinstance(exc, asyncio.IncompleteReadError):
                diagnostics.update(read_expected_bytes=exc.expected, read_received_bytes=len(exc.partial))
            return DomainResult.failure(code, message, data=data, verification_status='browser_unverified', details=diagnostics)

    @staticmethod
    def _account_failure() -> DomainResult[TaskBrowserObservation]:
        return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, 'The Task browser account changed or is unavailable.',
                                    verification_status='account_mismatch')
