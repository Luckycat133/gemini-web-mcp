"""Normalize text returned by Gemini Web's upstream streaming APIs."""

from __future__ import annotations

from typing import Any

from ..domain import (
    StreamChunkSemantics,
    StreamCollectionMetadata,
    StreamDelivery,
)


class StreamTextAccumulator:
    """Collect delta, cumulative, and mixed upstream chunks without duplication."""

    def __init__(self) -> None:
        self._text = ""
        self._chunk_count = 0
        self._emitted_piece_count = 0
        self._duplicate_chunk_count = 0
        self._observed_semantics: set[StreamChunkSemantics] = set()

    @property
    def text(self) -> str:
        return self._text

    @property
    def metadata(self) -> StreamCollectionMetadata:
        semantics = StreamChunkSemantics.EMPTY
        if len(self._observed_semantics) > 1:
            semantics = StreamChunkSemantics.MIXED
        elif self._observed_semantics:
            semantics = next(iter(self._observed_semantics))
        return StreamCollectionMetadata(
            delivery=StreamDelivery.COLLECTED,
            chunk_semantics=semantics,
            chunk_count=self._chunk_count,
            emitted_piece_count=self._emitted_piece_count,
            duplicate_chunk_count=self._duplicate_chunk_count,
            text_length=len(self._text),
        )

    def consume(self, response: Any) -> str:
        """Consume one upstream response and return only newly observed text."""
        self._chunk_count += 1
        delta = self._text_value(response, "text_delta")
        full_text = self._text_value(response, "text")

        if delta:
            piece = self._consume_explicit_delta(delta, full_text)
        elif full_text:
            piece = self._consume_unlabelled_text(full_text)
        else:
            piece = ""

        if piece:
            self._text += piece
            self._emitted_piece_count += 1
        else:
            self._duplicate_chunk_count += 1
        return piece

    def _consume_explicit_delta(self, delta: str, full_text: str) -> str:
        if full_text and full_text == self._text and full_text != delta:
            self._observe(StreamChunkSemantics.CUMULATIVE)
            self._observe(StreamChunkSemantics.DELTA)
            return ""
        if full_text and full_text.startswith(self._text):
            cumulative_suffix = full_text[len(self._text) :]
            if cumulative_suffix and cumulative_suffix != delta:
                self._observe(StreamChunkSemantics.CUMULATIVE)
                self._observe(StreamChunkSemantics.DELTA)
                return cumulative_suffix

        self._observe(StreamChunkSemantics.DELTA)
        return delta

    def _consume_unlabelled_text(self, value: str) -> str:
        if not self._text:
            self._observe(StreamChunkSemantics.CUMULATIVE)
            return value
        if value.startswith(self._text):
            self._observe(StreamChunkSemantics.CUMULATIVE)
            return value[len(self._text) :]
        if self._text.startswith(value):
            self._observe(StreamChunkSemantics.CUMULATIVE)
            return ""

        self._observe(StreamChunkSemantics.DELTA)
        return value

    def _observe(self, semantics: StreamChunkSemantics) -> None:
        self._observed_semantics.add(semantics)

    @staticmethod
    def _text_value(response: Any, attribute: str) -> str:
        value = getattr(response, attribute, "")
        return value if isinstance(value, str) else ""
