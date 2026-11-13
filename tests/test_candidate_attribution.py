from gemini_webapi.types import Candidate, ModelOutput

from src.infrastructure.rpc_parsers import (
    CandidateSourceEvidence, attribution_matches_answer, parse_candidate_attribution_sources,
)
from src.services.search import (
    GroundingState,
    ObservedSource,
    _classify_grounding,
    observed_sources_from_frames,
)
from src.thinking_client import ThinkingLevelGeminiClient, _candidate_evidence


def candidate(url="https://www.python.org/downloads/release/python-3140/"):
    snippet = ["A release description", None, None, [[0, 12], [0, 12]]]
    record = [url, "Python release", "https://www.gstatic.com/favicon", "details", [snippet]]
    return ["candidate", ["A release description"], [None, [[snippet, [1], [record], "other"]]]]


def test_attribution_extracts_only_structured_source_record():
    result = parse_candidate_attribution_sources(candidate())
    assert len(result) == 1
    assert result[0].url == "https://www.python.org/downloads/release/python-3140/"
    assert result[0].title == "Python release"


def test_attribution_rejects_empty_changed_shape_image_and_unassociated_records():
    assert parse_candidate_attribution_sources([]) == ()
    assert parse_candidate_attribution_sources([None, ["https://example.com"], None]) == ()
    assert parse_candidate_attribution_sources(candidate("https://example.org/picture.jpg")) == ()
    assert parse_candidate_attribution_sources(candidate("https://example.org/favicon")) == ()
    changed = candidate()
    changed[2][1][0][2][0][4] = [["unrelated"]]
    assert parse_candidate_attribution_sources(changed) == ()
    assert parse_candidate_attribution_sources([None, None, [None, [{"changed": True}]]]) == ()


def test_candidate_bridge_and_selected_reply_identity():
    client = ThinkingLevelGeminiClient.__new__(ThinkingLevelGeminiClient)
    token = _candidate_evidence.set({})
    try:
        client._parse_candidate(candidate(), "cid", "rid", "candidate")
        evidence = _candidate_evidence.get()
        assert evidence is not None and evidence["candidate"]
    finally:
        _candidate_evidence.reset(token)
    selected = Candidate(rcid="candidate", text="answer")
    object.__setattr__(selected, "_retrieved_sources", evidence["candidate"])
    early = ModelOutput(metadata=["cid", "rid"], candidates=[selected])
    final = ModelOutput(metadata=["cid", "rid"], candidates=[Candidate(rcid="candidate", text="answer")])
    sources = observed_sources_from_frames((early,), final)
    assert sources == (ObservedSource(url="https://www.python.org/downloads/release/python-3140/", title="Python release", role="retrieved_only"),)
    assert _classify_grounding("answer", sources) == GroundingState.ANSWER_ONLY
    mismatch = ModelOutput(metadata=["cid", "another"], candidates=final.candidates)
    assert observed_sources_from_frames((early,), mismatch) == ()


def test_attribution_span_verifies_codepoints_utf16_and_rejects_mismatch():
    text = "🐍Python release"
    by_codepoint = CandidateSourceEvidence("https://python.org", None, "Python", ((1, 7),))
    by_utf16 = CandidateSourceEvidence("https://python.org", None, "Python", ((2, 8),))
    bad = CandidateSourceEvidence("https://python.org", None, "Python", ((0, 100),))
    assert attribution_matches_answer(by_codepoint, text)
    assert attribution_matches_answer(by_utf16, text)
    assert not attribution_matches_answer(bad, text)
    assert not attribution_matches_answer(by_codepoint, "Different answer")


def test_attribution_requires_span_to_match_both_selected_frame_and_final_answer():
    source = CandidateSourceEvidence("https://python.org/", "Python", "Python", ((0, 6),))
    selected = Candidate(rcid="selected", text="Python release")
    object.__setattr__(selected, "_retrieved_sources", (source,))
    early = ModelOutput(metadata=["cid", "rid"], candidates=[selected])
    same = ModelOutput(metadata=["cid", "rid"], candidates=[Candidate(rcid="selected", text="Python release")])
    changed = ModelOutput(metadata=["cid", "rid"], candidates=[Candidate(rcid="selected", text="A different release")])
    verified = observed_sources_from_frames((early,), same)
    assert verified[0].role == "source_attribution"
    assert _classify_grounding("Python release", verified) == GroundingState.GROUNDED
    assert observed_sources_from_frames((early,), changed)[0].role == "retrieved_only"
