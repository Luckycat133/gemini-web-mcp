import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain.artifacts import ArtifactState
from src.domain.task_files import TaskFile
from src.infrastructure.task_files import parse_task_files
from src.services.task_file_artifacts import save_task_file


def streaming_client(*, chunks, content_type="text/csv", status=200):
    async def contents():
        for chunk in chunks:
            yield chunk
    response = SimpleNamespace(status_code=status, headers={"content-type": content_type}, aiter_content=contents)
    @asynccontextmanager
    async def stream(*args, **kwargs):
        assert args[0] == "GET" and kwargs["allow_redirects"] is False
        yield response
    return SimpleNamespace(stream=stream)


def body(*, status=2):
    card = ["file-id", None, [None, 3, "synthetic.csv", None, None, "opaque", None,
                              ["preview", "https://contribution.usercontent.google.com/download?x=test", "other"],
                              None, None, None, "text/csv"]]
    candidate = ["rc_one", None, None, None, None, None, None, None, [status], None, None, None,
                 [{"59": [[card]]}]]
    return [[[['c_one', 'r_one'], None, None, [[candidate]]]]]


def test_exact_completed_candidate_file_card():
    parsed = parse_task_files(body(), rid="r_one", rcid="rc_one")
    assert parsed.status == "success"
    assert parsed.value[0].name == "synthetic.csv"
    assert "download?" not in repr(parsed.value[0])


def test_empty_and_unknown_candidate_are_distinct():
    data = body()
    data[0][0][3][0][0][12] = [{}]
    assert parse_task_files(data, rid="r_one", rcid="rc_one").status == "empty"
    assert parse_task_files(body(), rid="other", rcid="rc_one").status == "changed_shape"
    assert parse_task_files(body(status=1), rid="r_one", rcid="rc_one").status == "changed_shape"


def test_changed_file_shape_and_external_endpoint_are_rejected():
    data = body()
    card = data[0][0][3][0][0][12][0]["59"][0][0]
    card[2][7][1] = "https://attacker.invalid/download"
    assert parse_task_files(data, rid="r_one", rcid="rc_one").status == "changed_shape"
    card[2] = []
    assert parse_task_files(data, rid="r_one", rcid="rc_one").status == "changed_shape"


def test_unknown_rich_container_is_not_empty_evidence():
    data = body()
    candidate = data[0][0][3][0][0]
    candidate[12] = candidate[12][0]
    assert parse_task_files(data, rid='r_one', rcid='rc_one').status == 'changed_shape'


def test_save_verifies_bytes_and_is_stable(tmp_path: Path):
    item = parse_task_files(body(), rid="r_one", rcid="rc_one").value[0]
    client = streaming_client(chunks=[b"code,value\n", b"a,1\n"], content_type="text/csv; charset=utf-8")
    async def run():
        a = await save_task_file(client, item, output_dir=tmp_path, chat_id="c_one", rid="r_one", rcid="rc_one")
        b = await save_task_file(client, item, output_dir=tmp_path, chat_id="c_one", rid="r_one", rcid="rc_one")
        return a, b
    first, second = asyncio.run(run())
    assert first.id == second.id and first.state is ArtifactState.LOCAL
    assert Path(first.local_path).read_bytes() == b"code,value\na,1\n"
    assert first.verification.details["sha256"]


def test_download_failure_does_not_create_artifact(tmp_path: Path):
    item = TaskFile("id", "file.csv", "text/csv", "https://contribution.usercontent.google.com/download?x=a")
    client = streaming_client(chunks=[b"login page"], content_type="text/html")
    with pytest.raises(ValueError, match="content type"):
        asyncio.run(save_task_file(client, item, output_dir=tmp_path, chat_id="c_one", rid="r_one", rcid="rc_one"))
    assert not list(tmp_path.iterdir())


def test_stream_exceeding_limit_removes_partial_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.services.task_file_artifacts.MAX_TASK_FILE_BYTES", 4)
    item = TaskFile("id", "file.csv", "text/csv", "https://contribution.usercontent.google.com/download?x=a")
    with pytest.raises(ValueError, match="exceeds"):
        asyncio.run(save_task_file(streaming_client(chunks=[b"123", b"45"]), item,
                                   output_dir=tmp_path, chat_id="c_one", rid="r_one", rcid="rc_one"))
    assert not list(tmp_path.iterdir())


def test_stream_aborts_on_account_change_and_removes_partial(tmp_path: Path):
    checks = iter([True, True, True, False])
    item = TaskFile('id', 'file.csv', 'text/csv', 'https://contribution.usercontent.google.com/download?x=a')
    with pytest.raises(ValueError, match='account changed'):
        asyncio.run(save_task_file(streaming_client(chunks=[b'first', b'second']), item,
            output_dir=tmp_path, chat_id='c_one', rid='r_one', rcid='rc_one', account_matches=lambda: next(checks)))
    assert not list(tmp_path.iterdir())
