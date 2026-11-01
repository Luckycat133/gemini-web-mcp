"""Offline Task browser observations and at-most-once input boundaries."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat

from PIL import Image
import pytest

from src.domain import DomainErrorCode
from src.infrastructure.account_identity import current_account_key
from src.infrastructure.browser_action_store import BrowserActionStore
from src.services.task_browser import TaskBrowserDependencies, TaskBrowserService

CID, RID, RCID = "c_synthetic", "r_synthetic", "rc_synthetic"
ACTION = "action_" + "a" * 32
VM = "synthetic-private-vm-identity"
TOKEN = "synthetic-private-relay-token"
RAW = "synthetic-private-native-response"
TYPED = "synthetic-private-typed-text"


def _png(color):
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(stream, format="PNG")
    return stream.getvalue()


class FakeRFB:
    width, height = 32, 24

    def __init__(self, harness):
        self.harness = harness
        self.before = _png("navy")
        self.after = _png("orange")
        self.captures = 0
        self.inputs = []
        self.fail_capture = None
        self.partial_type = False

    async def screenshot_png(self):
        self.captures += 1
        await self.harness.pause("frame_before" if self.captures == 1 else "frame_after")
        if self.captures == self.fail_capture:
            raise ConnectionError(f"lost {VM} {TOKEN} {RAW}")
        return self.before if self.captures == 1 else self.after

    def click(self, x, y, button):
        self.inputs.append(("click", x, y, button))

    def scroll(self, x, y, steps):
        self.inputs.append(("scroll", x, y, steps))

    def key(self, key):
        self.inputs.append(("key", key))

    def type_text(self, text):
        if self.partial_type:
            self.inputs.append(("partial_type", text[:2]))
            raise ConnectionError(f"partial input {TOKEN} {text}")
        self.inputs.append(("type", text))


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.monkeypatch = monkeypatch
        self.store = BrowserActionStore(tmp_path / "state" / "operations.sqlite3")
        self.client = object()
        self.statuses = [3, 3]
        self.turn = {"chat_id": CID, "response_id": RID, "candidate": [RCID, {"vm_id": VM, "token": TOKEN}],
                     "raw_response": RAW}
        self.stages = []
        self.switch_at = None
        self.bind_on_initialize = False
        self.initializations = 0
        self.turn_reads = []
        self.task_reads = []
        self.connections = []
        self.rfb = FakeRFB(self)
        self.dependencies = TaskBrowserDependencies(
            client_initializer=self.initialize, client_provider=lambda: self.client,
            read_turn=self.read_turn, read_task=self.read_task, connection=self.connection,
        )
        self.service = TaskBrowserService(self.dependencies, store=self.store)

    async def pause(self, stage):
        self.stages.append(stage)
        await asyncio.sleep(0)
        if self.switch_at == stage:
            self.monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-account-b")

    async def initialize(self):
        self.initializations += 1
        await self.pause("initialize")
        if self.bind_on_initialize:
            self.monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-account-a")

    async def read_turn(self, client, cid, rid, rcid):
        self.turn_reads.append((client, cid, rid, rcid))
        await self.pause("turn")
        return self.turn

    async def read_task(self, client, cid):
        index = len(self.task_reads)
        self.task_reads.append((client, cid))
        await self.pause("task_before" if index == 0 else "task_fresh")
        return {"chat_id": cid, "upstream_status": self.statuses[min(index, len(self.statuses) - 1)]}

    @asynccontextmanager
    async def connection(self, client, observed):
        self.connections.append((client, observed))
        await self.pause("enter")
        try:
            yield self.rfb
        finally:
            await self.pause("exit")

    def input_arguments(self, **overrides):
        values = {"response_id": RID, "candidate_id": RCID, "browser_input": {"kind": "click", "x": 4, "y": 5},
                  "action_id": ACTION, "expected_frame_sha256": hashlib.sha256(self.rfb.before).hexdigest()}
        values.update(overrides)
        return values

    def action(self):
        return self.store.read(current_account_key(), ACTION)

    def action_count(self):
        if not self.store.path.exists():
            return 0
        with sqlite3.connect(self.store.path) as db:
            table = db.execute("SELECT name FROM sqlite_master WHERE name='browser_actions'").fetchone()
            return db.execute("SELECT COUNT(*) FROM browser_actions").fetchone()[0] if table else 0


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-account-a")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "false")
    return Harness(tmp_path, monkeypatch)


def _assert_private(result, harness):
    public = json.dumps(result.to_dict())
    persisted = harness.store.path.read_bytes() if harness.store.path.exists() else b""
    for private in (VM, TOKEN, RAW, TYPED, "synthetic-browser-account-a"):
        assert private not in public
        assert private.encode() not in persisted


def _assert_frame(data, expected):
    assert len(data.artifacts) == 1
    artifact = data.artifacts[0]
    path = Path(artifact.local_path)
    assert path.read_bytes() == expected
    assert data.frame_sha256 == hashlib.sha256(expected).hexdigest()
    assert artifact.verification.status.value == "verified"
    assert artifact.verification.details["sha256"] == data.frame_sha256
    assert (artifact.width, artifact.height) == (32, 24)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_screenshot_returns_a_verified_local_frame_without_claiming_or_sending_input(harness):
    result = asyncio.run(harness.service.observe(CID))

    assert result.ok and result.data.state == "frame_ready"
    assert (result.data.chat_id, result.data.response_id, result.data.candidate_id) == (CID, RID, RCID)
    assert result.data.input_sent is False and result.data.action_id is None
    assert not result.data.exclusive_control and not result.data.external_effect_verified
    _assert_frame(result.data, harness.rfb.before)
    assert harness.turn_reads == [(harness.client, CID, None, None)]
    assert len(harness.connections) == 1 and harness.rfb.captures == 1
    assert not harness.rfb.inputs and harness.action_count() == 0
    _assert_private(result, harness)


@pytest.mark.parametrize("overrides", [
    {"response_id": None}, {"candidate_id": None}, {"response_id": "wrong"}, {"candidate_id": "wrong"},
    {"action_id": None}, {"action_id": "short"}, {"action_id": True},
    {"expected_frame_sha256": None}, {"expected_frame_sha256": "x" * 64},
    {"browser_input": {}}, {"browser_input": {"kind": "drag", "x": 1, "y": 1}},
    {"browser_input": {"kind": "click", "x": True, "y": 1}},
    {"browser_input": {"kind": "click", "x": -1, "y": 1}},
    {"browser_input": {"kind": "key", "key": "invented-key"}},
    {"browser_input": {"kind": "scroll", "x": 1, "y": 1, "steps": 0}},
    {"browser_input": {"kind": "type", "text": ""}},
    {"browser_input": {"kind": "type", "text": "\x00"}},
    {"browser_input": {"kind": "type", "text": "x" * 2049}},
    {"browser_input": {"kind": "type", "text": "safe", "unexpected": "field"}},
])
def test_invalid_input_is_rejected_before_initialization_or_any_remote_observation(harness, overrides):
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments(**overrides)))

    assert not result.ok and result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert harness.initializations == 0 and not harness.turn_reads and not harness.task_reads
    assert not harness.connections and not harness.rfb.inputs and harness.action_count() == 0


@pytest.mark.parametrize("chat_id", [None, True, "", "c_", "not_a_chat", "c_bad/path"])
def test_input_requires_an_exact_chat_id_before_initialization(harness, chat_id):
    result = asyncio.run(harness.service.observe(chat_id, **harness.input_arguments()))
    assert not result.ok and result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert harness.initializations == 0 and not harness.turn_reads and not harness.connections
    assert not harness.rfb.inputs and harness.action_count() == 0


@pytest.mark.parametrize("mismatch", ["chat", "response", "candidate", "empty_candidate"])
def test_returned_turn_must_match_all_requested_identities_before_connection(harness, mismatch):
    if mismatch == "chat":
        harness.turn["chat_id"] = "c_other"
    elif mismatch == "response":
        harness.turn["response_id"] = "r_other"
    else:
        harness.turn["candidate"] = ["rc_other"] if mismatch == "candidate" else []
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments()))
    assert not result.ok and result.error_code == DomainErrorCode.VERIFICATION_FAILED.value
    assert harness.task_reads == [(harness.client, CID)]
    assert not harness.connections and not harness.rfb.inputs
    assert harness.action_count() == 0
    _assert_private(result, harness)


@pytest.mark.parametrize("status", [3, 5])
@pytest.mark.parametrize("browser_input,expected", [
    ({"kind": "click", "x": 4, "y": 5}, ("click", 4, 5, "left")),
    ({"kind": "scroll", "x": 4, "y": 5, "steps": -2}, ("scroll", 4, 5, -2)),
    ({"kind": "key", "key": "Enter"}, ("key", "Enter")),
    ({"kind": "type", "text": TYPED}, ("type", TYPED)),
])
def test_explicit_input_requires_stable_quiescent_state_and_does_not_verify_website_effects(harness, status, browser_input, expected):
    harness.statuses = [status, status]
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments(browser_input=browser_input)))

    assert result.ok and result.data.state == "input_sent" and result.data.input_sent is True
    assert result.data.action_state == "verified" and result.data.upstream_status == status
    assert not result.data.external_effect_verified and not result.data.exclusive_control
    assert result.meta.verification_status == "browser_input_sent_frame_observed"
    assert any(w.code == "browser_effect_unverified" for w in result.warnings)
    assert harness.rfb.inputs == [expected] and harness.rfb.captures == 2
    _assert_frame(result.data, harness.rfb.after)
    assert harness.action()["state"] == "verified"
    assert harness.action()["artifact_id"] == result.data.artifacts[0].id
    _assert_private(result, harness)


@pytest.mark.parametrize("status", [2, 0, 4, None, True])
def test_active_or_unverified_task_status_refuses_input_before_connecting(harness, status):
    harness.statuses = [status]
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments()))
    assert not result.ok and result.error_code == DomainErrorCode.CAPABILITY_UNAVAILABLE.value
    assert not harness.connections and not harness.rfb.inputs and harness.action_count() == 0


def test_stale_frame_returns_the_new_verified_image_without_consuming_the_action(harness):
    arguments = harness.input_arguments(expected_frame_sha256=hashlib.sha256(harness.rfb.after).hexdigest())
    result = asyncio.run(harness.service.observe(CID, **arguments))

    assert not result.ok and result.meta.verification_status == "browser_frame_changed"
    assert result.data.state == "frame_changed" and result.data.input_sent is False
    _assert_frame(result.data, harness.rfb.before)
    assert harness.rfb.captures == 1 and not harness.rfb.inputs and harness.action() is None


@pytest.mark.parametrize("statuses", [[3, 2], [5, 2], [3, 5], [5, 3]])
def test_state_change_between_capture_and_input_refuses_the_action(harness, statuses):
    harness.statuses = statuses
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments()))
    assert not result.ok and result.error_code == DomainErrorCode.CAPABILITY_UNAVAILABLE.value
    assert harness.rfb.captures == 1 and not harness.rfb.inputs and harness.action() is None


def test_coordinates_must_fit_the_actual_frame_before_the_action_is_claimed(harness):
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments(
        browser_input={"kind": "click", "x": harness.rfb.width, "y": 1})))
    assert not result.ok and result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert not harness.rfb.inputs and harness.action() is None


@pytest.mark.parametrize("failure", ["screenshot_corrupt", "before_input_corrupt", "after_input_corrupt"])
def test_corrupt_frames_never_become_verified_artifacts(harness, failure):
    arguments = harness.input_arguments() if failure != "screenshot_corrupt" else {}
    if failure == "after_input_corrupt":
        harness.rfb.after = b"not a PNG"
    else:
        harness.rfb.before = b"not a PNG"
    result = asyncio.run(harness.service.observe(CID, **arguments))
    assert not result.ok and not result.data.artifacts
    assert result.data.state == "outcome_unverified"
    if failure == "after_input_corrupt":
        assert len(harness.rfb.inputs) == 1 and harness.action()["state"] == "outcome_unverified"
    else:
        assert not harness.rfb.inputs and harness.action_count() == 0
    _assert_private(result, harness)


def test_existing_corrupt_frame_is_not_overwritten_or_reported_verified(harness):
    first = asyncio.run(harness.service.observe(CID))
    assert first.ok
    path = Path(first.data.artifacts[0].local_path)
    corrupt = b"changed saved screenshot"
    path.write_bytes(corrupt)
    harness.rfb.captures = 0

    second = asyncio.run(harness.service.observe(CID))

    assert not second.ok and not second.data.artifacts
    assert path.read_bytes() == corrupt
    assert not harness.rfb.inputs and harness.action_count() == 0


def test_consumed_action_id_never_reconnects_or_sends_changed_text_again(harness):
    arguments = harness.input_arguments(browser_input={"kind": "type", "text": TYPED})
    first = asyncio.run(harness.service.observe(CID, **arguments))
    assert first.ok
    reads = (len(harness.turn_reads), len(harness.task_reads), harness.rfb.captures)
    reopened = TaskBrowserService(harness.dependencies, store=BrowserActionStore(harness.store.path))

    duplicate = asyncio.run(reopened.observe(CID, **{**arguments, "browser_input": {"kind": "type", "text": "different text"}}))

    assert duplicate.ok and duplicate.data.state == "duplicate"
    assert duplicate.data.action_state == "verified" and duplicate.data.input_sent is False
    assert any(w.code == "browser_action_consumed" for w in duplicate.warnings)
    assert duplicate.meta.verification_status == "browser_input_not_repeated"
    assert not duplicate.data.artifacts and not duplicate.data.external_effect_verified
    assert len(harness.connections) == 1 and harness.rfb.inputs == [("type", TYPED)]
    assert reads == (len(harness.turn_reads), len(harness.task_reads), harness.rfb.captures)
    _assert_private(duplicate, harness)


@pytest.mark.parametrize("changes", [
    {"chat_id": "c_other"}, {"response_id": "r_other"}, {"candidate_id": "rc_other"},
    {"browser_input": {"kind": "key", "key": "Enter"}}, {"expected_frame_sha256": "0" * 64},
])
def test_action_id_bound_to_different_metadata_is_refused_without_reconnection(harness, changes):
    arguments = harness.input_arguments()
    assert asyncio.run(harness.service.observe(CID, **arguments)).ok
    cid = changes.get("chat_id", CID)
    changed = {key: value for key, value in changes.items() if key != "chat_id"}

    result = asyncio.run(harness.service.observe(cid, **{**arguments, **changed}))

    assert not result.ok and result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert len(harness.connections) == 1 and len(harness.rfb.inputs) == 1
    assert harness.action()["state"] == "verified"


@pytest.mark.parametrize("failure", ["partial_type", "post_input_frame_lost"])
def test_partial_or_unobserved_claimed_input_stays_unknown_and_is_never_replayed(harness, failure):
    harness.rfb.partial_type = failure == "partial_type"
    harness.rfb.fail_capture = 2 if failure == "post_input_frame_lost" else None
    arguments = harness.input_arguments(browser_input={"kind": "type", "text": TYPED})
    first = asyncio.run(harness.service.observe(CID, **arguments))

    assert not first.ok and first.data.state == "outcome_unverified"
    assert first.data.action_state == "outcome_unverified" and not first.data.artifacts
    assert first.data.input_sent is (None if failure == "partial_type" else True)
    assert harness.action()["state"] == "outcome_unverified"
    original_inputs = list(harness.rfb.inputs)
    _assert_private(first, harness)
    reopened = TaskBrowserService(harness.dependencies, store=BrowserActionStore(harness.store.path))
    duplicate = asyncio.run(reopened.observe(CID, **arguments))
    assert duplicate.ok and duplicate.data.state == "duplicate"
    assert duplicate.data.action_state == "outcome_unverified" and duplicate.data.input_sent is False
    assert any(w.code == "browser_action_consumed" for w in duplicate.warnings)
    assert len(harness.connections) == 1 and harness.rfb.inputs == original_inputs


@pytest.mark.parametrize("input_requested,stage", [
    (False, "initialize"), (False, "turn"), (False, "task_before"), (False, "enter"),
    (False, "frame_before"), (False, "exit"),
    (True, "initialize"), (True, "turn"), (True, "task_before"), (True, "enter"),
    (True, "frame_before"), (True, "task_fresh"), (True, "frame_after"), (True, "exit"),
])
def test_account_change_at_each_await_scrubs_all_browser_data(harness, input_requested, stage):
    harness.switch_at = stage
    arguments = harness.input_arguments() if input_requested else {}
    result = asyncio.run(harness.service.observe(CID, **arguments))

    assert stage in harness.stages
    assert not result.ok and result.error_code == DomainErrorCode.AUTH_REQUIRED.value
    assert result.data is None and result.to_dict()["data"] is None
    if not input_requested or stage not in {"frame_after", "exit"}:
        assert not harness.rfb.inputs
    assert len(harness.rfb.inputs) <= 1
    _assert_private(result, harness)


def test_account_bound_during_initialization_is_still_fenced_after_connection_exit(harness, monkeypatch):
    monkeypatch.delenv("GEMINI_PSID")
    harness.bind_on_initialize = True
    harness.switch_at = "exit"

    result = asyncio.run(harness.service.observe(CID))

    assert "exit" in harness.stages
    assert not result.ok and result.error_code == DomainErrorCode.AUTH_REQUIRED.value
    assert result.data is None and not harness.rfb.inputs
