from workpilot import enterprise_search, state_store
from workpilot.enterprise_search import result_to_chunk


def test_result_to_chunk_prefers_extractive_segments():
    doc = {
        "id": "project-status-md-1",
        "struct_data": {"title": "project_status.md"},
        "derived_struct_data": {
            "snippets": [{"snippet": "# Phoenix <b>Release</b>&nbsp;...", "snippet_status": "SUCCESS"}],
            "extractive_segments": [{"content": "Payment gateway certification is behind schedule."}],
            "extractive_answers": [{"content": "Certification is behind schedule."}],
        },
    }
    chunk = result_to_chunk(doc, rank=1)
    assert chunk.source == "project_status.md"
    assert chunk.text.startswith("Payment gateway certification")
    assert "Certification is behind schedule." in chunk.text
    assert chunk.score == 0.9


def test_result_to_chunk_falls_back_to_clean_snippet():
    doc = {"id": "x", "derivedStructData": {"title": "meeting_notes.md", "snippets": [{"snippet": "Priya: <b>evidence</b>&nbsp;pack", "snippetStatus": "SUCCESS"}]}}
    chunk = result_to_chunk(doc)
    assert chunk.text == "Priya: evidence pack"
    assert chunk.source == "meeting_notes.md"


def test_result_to_chunk_returns_none_without_text():
    assert result_to_chunk({"id": "empty", "derived_struct_data": {}, "struct_data": {}}) is None


def test_status_not_configured_without_engine(set_config):
    set_config(demo_mode=False, vertex_search_enabled=True, vertex_search_engine_id="")
    label, detail = enterprise_search.status()
    assert label == "NOT CONFIGURED"
    assert "VERTEX_SEARCH_ENGINE_ID" in detail


def test_status_fallback_when_engine_lookup_fails(set_config, monkeypatch):
    set_config(demo_mode=False, vertex_search_enabled=True, vertex_search_engine_id="missing-engine", project_id="p")
    monkeypatch.setattr(enterprise_search, "_VERIFIED", None)

    def fail(force=False):
        return False, "NotFound: engine"

    monkeypatch.setattr(enterprise_search, "verify", fail)
    label, detail = enterprise_search.status()
    assert label == "FALLBACK" and "NotFound" in detail


def test_corpus_fingerprint_changes_when_content_changes():
    first = enterprise_search.corpus_fingerprint({"a.md": "hello"})
    same = enterprise_search.corpus_fingerprint({"a.md": "hello"})
    changed = enterprise_search.corpus_fingerprint({"a.md": "hello world"})
    assert first == same
    assert first != changed


def test_ensure_index_skips_when_not_configured():
    assert enterprise_search.ensure_index({"a.md": "x"})["status"] == "skipped"


def test_vertex_hit_displays_extractive_answer_and_keeps_segment_for_gemini():
    doc = {
        "id": "security-assessment-md-1a9328d882",
        "derived_struct_data": {
            "title": "security_assessment.md",
            "extractive_segments": [{"content": "# Security Assessment\n\nWhole document text ..."}],
            "extractive_answers": [{"content": "### Release impact SEC-217 **must be validated** before production approval."}],
            "snippets": [{"snippet": "<b>SEC</b>-217 snippet", "snippet_status": "SUCCESS"}],
        },
    }
    chunk = enterprise_search.result_to_chunk(doc, 0)
    assert chunk.text.startswith("# Security Assessment")  # full context for Gemini
    assert chunk.snippet.startswith("### Release impact SEC-217")
    from workpilot import presentation

    card = presentation.evidence_cards([chunk])[0]
    assert card["excerpt"] == "Release impact SEC-217 must be validated before production approval."


def test_vertex_hits_map_to_local_names_and_sections(sample_docs):
    from workpilot.models import SourceChunk
    from workpilot.retrieval import RETRIEVAL_ACTIVE, EnterpriseRetriever

    class _Remote:
        def available(self):
            return True

        def search(self, query, k=6):
            return [
                SourceChunk(
                    enterprise_search._safe_id("security_assessment.md"),
                    "whole doc",
                    1.0,
                    snippet="SEC-217 must be validated before production approval. It is a go-live condition",
                )
            ]

    r = EnterpriseRetriever(remote=_Remote())
    r.build(sample_docs)
    hit = r.search("SEC-217 sign-off")[0]
    assert r.last_mode == RETRIEVAL_ACTIVE
    assert hit.source == "security_assessment.md"
    assert hit.section == "Release impact"


def test_ensure_index_skips_when_fingerprint_matches(monkeypatch):
    enterprise_search.reset_index_sync_state()
    docs = {"security_assessment.md": "SEC-217"}
    fingerprint = enterprise_search.corpus_fingerprint(docs)
    monkeypatch.setattr(enterprise_search, "configured", lambda: True)
    monkeypatch.setattr(state_store, "get_search_index_state", lambda: {"fingerprint": fingerprint})

    def boom(documents):
        raise RuntimeError("should not sync")

    monkeypatch.setattr(enterprise_search, "sync_documents", boom)
    result = enterprise_search.ensure_index(docs)
    assert result["status"] == "current"
