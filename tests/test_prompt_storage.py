"""Offline safety and multi-process behavior of the shared Prompt owner."""

import asyncio
import json
import multiprocessing
import zipfile
from pathlib import Path

import pytest

from src import skill_server
from src.services.prompts import PromptLibrary
from src.skill_server import PromptManager as CompactPromptManager
from src.tools.prompts import PromptManager


def _create_in_process(path, ready, start, compact):
    manager = CompactPromptManager(Path(path)) if compact else PromptManager(path)
    ready.put(True)
    if not start.wait(10):
        raise RuntimeError("Prompt writer did not receive start signal")
    if compact:
        manager.create("Compact", "from compact")
    else:
        manager.create_prompt("Primary", "from primary")


def _initialize_with_wait(path, default_file, seed_ready, writer_ready):
    library = PromptLibrary(path)
    write = library._atomic_write

    def delayed_write(document=None):
        seed_ready.set()
        if not writer_ready.wait(15):
            raise RuntimeError("Prompt writer did not start")
        write(document)

    library._atomic_write = delayed_write
    assert library.seed_if_absent(default_file) is True


def _first_write_during_seed(path, writer_ready):
    manager = CompactPromptManager(Path(path))
    writer_ready.set()
    manager.create("First user Prompt", "keep this user entry")


def test_cross_process_writes_keep_both_surfaces_updates(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    target = tmp_path / "library.json"
    writers = [context.Process(target=_create_in_process, args=(str(target), ready, start, compact)) for compact in (True, False)]
    try:
        for writer in writers:
            writer.start()
        assert ready.get(timeout=15) is True
        assert ready.get(timeout=15) is True
        start.set()
        for writer in writers:
            writer.join(timeout=15)
            assert writer.exitcode == 0
        assert {prompt["name"] for prompt in PromptLibrary(target).list()} == {"Primary", "Compact"}
    finally:
        for writer in writers:
            if writer.is_alive():
                writer.terminate()
                writer.join(timeout=5)
        ready.close()


def test_first_initialization_and_first_crud_keep_complete_library(tmp_path):
    target = tmp_path / "library.json"
    defaults_file = tmp_path / "defaults.json"
    default = {"id": "stable-default", "name": "Bundled", "content": "preserved default content"}
    defaults_file.write_text(json.dumps({"prompts": {default["id"]: default}}), encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    seed_ready = context.Event()
    writer_ready = context.Event()
    initializer = context.Process(target=_initialize_with_wait, args=(str(target), str(defaults_file), seed_ready, writer_ready))
    writer = context.Process(target=_first_write_during_seed, args=(str(target), writer_ready))
    try:
        initializer.start()
        assert seed_ready.wait(15)
        writer.start()
        for process in (initializer, writer):
            process.join(timeout=20)
            assert process.exitcode == 0
        stored = json.loads(target.read_text(encoding="utf-8"))["prompts"]
        assert stored[default["id"]] == default
        assert {prompt["name"] for prompt in stored.values()} == {"Bundled", "First user Prompt"}
    finally:
        for process in (initializer, writer):
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


def test_initializer_rechecks_existence_after_first_crud(tmp_path):
    target = tmp_path / "library.json"
    initializer = PromptLibrary(target)
    manager = PromptManager(str(target))
    prompt_id = manager.create_prompt("First", "keep")
    saved = target.read_bytes()
    # Once a user library exists the template need not be read at all.
    assert initializer.seed_if_absent(tmp_path / "not-needed.json") is False
    assert target.read_bytes() == saved
    assert initializer.get(prompt_id)["content"] == "keep"


def test_seed_preserves_complete_default_metadata_until_crud(tmp_path):
    defaults_file = tmp_path / "defaults.json"
    default_document = {
        "version": "1.0", "updated_at": "2026-05-15", "package_metadata": {"origin": "bundled"},
        "prompts": {"default": {"id": "default", "name": "Default", "content": "keep"}},
    }
    defaults_file.write_text(json.dumps(default_document), encoding="utf-8")
    target = tmp_path / "library.json"
    library = PromptLibrary(target)
    assert library.seed_if_absent(defaults_file) is True
    assert json.loads(target.read_text(encoding="utf-8")) == default_document
    library.create("New", "new content")
    after_crud = json.loads(target.read_text(encoding="utf-8"))
    assert after_crud["updated_at"] != default_document["updated_at"]
    assert after_crud["prompts"]["default"] == default_document["prompts"]["default"]


def test_initializer_preserves_source_that_became_corrupt(tmp_path):
    target = tmp_path / "library.json"
    initializer = PromptLibrary(target)
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="原文件已保留"):
        initializer.seed_if_absent(tmp_path / "defaults.json")
    assert target.read_text(encoding="utf-8") == "{broken"


def test_initialization_accepts_packaged_traversable_resource(tmp_path):
    archive_file = tmp_path / "defaults.zip"
    default = {"id": "bundled", "name": "Bundled", "content": "keep"}
    with zipfile.ZipFile(archive_file, "w") as archive:
        archive.writestr("defaults.json", json.dumps({"prompts": {default["id"]: default}}))
    library = PromptLibrary(tmp_path / "library.json")
    with zipfile.ZipFile(archive_file, "r") as archive:
        assert library.seed_if_absent(zipfile.Path(archive, "defaults.json")) is True
    assert library.get("bundled") == default


def test_compact_default_initialization_propagates_partial_write(tmp_path, monkeypatch):
    defaults_file = tmp_path / "defaults.json"
    defaults_file.write_text(json.dumps({"prompts": {"d": {"id": "d", "name": "Default", "content": "keep"}}}), encoding="utf-8")
    target = tmp_path / "library.json"
    monkeypatch.setattr(skill_server, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(skill_server, "PROMPTS_FILE", target)
    monkeypatch.setattr(skill_server, "DEFAULT_PROMPTS_FILE", defaults_file)

    def partial_dump(_payload, file, **_kwargs):
        file.write("{partial")
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", partial_dump)
    with pytest.raises(OSError, match="disk full"):
        skill_server._init_default_prompts()
    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_stale_instances_reload_before_update_and_delete(tmp_path):
    target = tmp_path / "library.json"
    primary = PromptManager(str(target))
    compact = CompactPromptManager(target)
    first = primary.create_prompt("Primary", "one")
    second = compact.create("Compact", "two")
    assert primary.update_prompt(first, content="updated") is True
    assert compact.get_by_name("Primary")["content"] == "updated"
    assert compact.delete("Primary") is True
    assert primary.delete_prompt(second) is True
    assert PromptLibrary(target).list() == []


def test_compact_distinct_names_have_independent_ids(tmp_path):
    manager = CompactPromptManager(tmp_path / "library.json")
    first = manager.create("A B", "one")
    second = manager.create("A_B", "two")
    assert first != second
    assert manager.get_by_name("A B")["content"] == "one"
    assert manager.get_by_name("A_B")["content"] == "two"
    with pytest.raises(ValueError, match="already exists"):
        manager.create("a b", "overwrite")
    assert manager.get_by_name("A B")["content"] == "one"


@pytest.mark.parametrize("compact", [False, True])
def test_bad_source_after_load_blocks_mutation_and_preserves_bytes(tmp_path, compact):
    target = tmp_path / "library.json"
    manager = CompactPromptManager(target) if compact else PromptManager(str(target))
    if compact:
        manager.create("Existing", "keep")
    else:
        manager.create_prompt("Existing", "keep")
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="原文件已保留"):
        if compact:
            manager.create("New", "discard")
        else:
            manager.create_prompt("New", "discard")
    assert target.read_text(encoding="utf-8") == "{broken"


def test_compact_partial_serialization_preserves_disk_and_memory(tmp_path, monkeypatch):
    target = tmp_path / "library.json"
    manager = CompactPromptManager(target)
    prompt_id = manager.create("Existing", "keep")
    saved = target.read_bytes()

    def partial_dump(_payload, file, **_kwargs):
        file.write("{partial")
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", partial_dump)
    monkeypatch.setattr(skill_server, "get_prompts", lambda: manager)
    text = asyncio.run(skill_server.prompts("create", name="New", content="discard"))[0].text
    assert text.startswith("Error:") and "disk full" in text
    assert target.read_bytes() == saved
    assert list(manager._data) == [prompt_id]
    assert not list(tmp_path.glob("*.tmp"))
