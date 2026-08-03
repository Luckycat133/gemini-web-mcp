"""Deterministic contracts for collected upstream stream normalization."""

from __future__ import annotations

from types import SimpleNamespace

from src.domain import StreamChunkSemantics, StreamDelivery
from src.services.streams import StreamTextAccumulator


def test_cumulative_text_chunks_emit_only_new_suffixes() -> None:
    collector = StreamTextAccumulator()

    pieces = [
        collector.consume(SimpleNamespace(text="H")),
        collector.consume(SimpleNamespace(text="He")),
        collector.consume(SimpleNamespace(text="Hello")),
    ]

    assert pieces == ["H", "e", "llo"]
    assert collector.text == "Hello"
    assert collector.metadata.chunk_semantics is StreamChunkSemantics.CUMULATIVE
    assert collector.metadata.delivery is StreamDelivery.COLLECTED
    assert collector.metadata.chunk_count == 3
    assert collector.metadata.duplicate_chunk_count == 0


def test_explicit_delta_chunks_can_repeat_without_being_deduplicated() -> None:
    collector = StreamTextAccumulator()

    collector.consume(SimpleNamespace(text_delta="ha"))
    collector.consume(SimpleNamespace(text_delta="ha"))

    assert collector.text == "haha"
    assert collector.metadata.chunk_semantics is StreamChunkSemantics.DELTA


def test_empty_text_delta_falls_back_to_cumulative_text() -> None:
    collector = StreamTextAccumulator()

    collector.consume(SimpleNamespace(text_delta=None, text="Hello"))
    collector.consume(SimpleNamespace(text_delta="", text="Hello world"))

    assert collector.text == "Hello world"
    assert collector.metadata.chunk_semantics is StreamChunkSemantics.CUMULATIVE


def test_duplicate_and_stale_cumulative_chunks_do_not_duplicate_output() -> None:
    collector = StreamTextAccumulator()

    pieces = [
        collector.consume(SimpleNamespace(text="Hello")),
        collector.consume(SimpleNamespace(text="Hello")),
        collector.consume(SimpleNamespace(text="Hell")),
        collector.consume(SimpleNamespace(text="Hello!")),
    ]

    assert pieces == ["Hello", "", "", "!"]
    assert collector.text == "Hello!"
    assert collector.metadata.duplicate_chunk_count == 2


def test_full_text_corrects_a_conflicting_delta_without_duplication() -> None:
    collector = StreamTextAccumulator()

    collector.consume(SimpleNamespace(text="Hel"))
    piece = collector.consume(SimpleNamespace(text_delta="lolo", text="Hello"))

    assert piece == "lo"
    assert collector.text == "Hello"
    assert collector.metadata.chunk_semantics is StreamChunkSemantics.MIXED


def test_stale_full_text_rejects_a_conflicting_repeated_delta() -> None:
    collector = StreamTextAccumulator()

    collector.consume(SimpleNamespace(text="Hello"))
    piece = collector.consume(SimpleNamespace(text_delta="lo", text="Hello"))

    assert piece == ""
    assert collector.text == "Hello"
    assert collector.metadata.duplicate_chunk_count == 1
    assert collector.metadata.chunk_semantics is StreamChunkSemantics.MIXED


def test_unlabelled_delta_after_cumulative_chunk_is_recorded_as_mixed() -> None:
    collector = StreamTextAccumulator()

    collector.consume(SimpleNamespace(text="Hello"))
    collector.consume(SimpleNamespace(text=", "))
    collector.consume(SimpleNamespace(text="world"))

    assert collector.text == "Hello, world"
    assert collector.metadata.chunk_semantics is StreamChunkSemantics.MIXED
