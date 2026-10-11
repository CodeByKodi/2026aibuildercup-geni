from workpilot import enterprise_search
from workpilot.models import SourceChunk
from workpilot.retrieval import (
    RETRIEVAL_ACTIVE,
    RETRIEVAL_FALLBACK,
    EnterpriseRetriever,
    LocalRetriever,
    chunk_sections,
    hit_in_workspace,
    missing_identifiers,
    source_key,
    workspace_source_keys,
)


def test_chunks_follow_headings_and_sentences():
    doc = (
        "# Report\n\n**Date:** 8 October 2026\n\n## Security\n\n"
        "SEC-217 is open. Mei owns the fix. It must close before approval.\n\n"
        "## Schedule\n\n| Milestone | Date |\n| --- | --- |\n| Go-live | 18 October 2026 |\n"
    )
    sections = chunk_sections(doc, size=60)
    names = [name for name, _ in sections]
    assert names[0] == "Report" and "Security" in names and "Schedule" in names
    security = [text for name, text in sections if name == "Security"]
    assert all(t[0].isupper() for t in security)  # every chunk starts at a sentence
    assert all(t.rstrip().endswith(".") for t in security)
    schedule = next(text for name, text in sections if name == "Schedule")
    assert "|" not in schedule and "Milestone: Go-live · Date: 18 October 2026" in schedule
    assert "**" not in " ".join(t for _, t in sections)


def test_out_of_scope_identifier_returns_not_found(sample_docs):
    r = EnterpriseRetriever(remote=_Remote("ok"))
    r.build(sample_docs)
    assert r.search("What is the DR budget for v13?") == []
    assert r.not_found.startswith("Not found in workspace sources") and "v13" in r.not_found
    assert missing_identifiers("Who owns SEC-217 for v12.4?", r.local.corpus) == []


def test_local_retriever_drops_noise_scores(sample_docs):
    retriever = LocalRetriever()
    retriever.build(sample_docs)
    assert retriever.search("zebra quantum marmalade") == []
    hits = retriever.search("SEC-217 session token", k=3)
    assert hits and all(h.section for h in hits) and hits[0].score >= 0.06


def test_local_retriever_finds_relevant_source(sample_docs):
    retriever = LocalRetriever()
    retriever.build(sample_docs)
    hits = retriever.search("cache optimization latency", k=5)
    assert hits[0].page >= 1
    assert hits and hits[0].source in {
        "performance_test_report.md",
        "architecture_note.md",
        "release_status_report.md",
        "issue_tracker.md",
        "uat_meeting_notes.md",
        "security_assessment.md",
    }
    assert any("cach" in h.text.lower() for h in hits)
    assert hits[0].score >= hits[-1].score


def test_local_retriever_empty_corpus():
    retriever = LocalRetriever()
    retriever.build({})
    assert retriever.search("anything") == []


class _Remote:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def available(self):
        return self.behaviour != "unavailable"

    def search(self, query, k=6):
        if self.behaviour == "ok":
            return [SourceChunk("security_assessment.md", "SEC-217 remote evidence", 1.0)]
        if self.behaviour == "safe_id":
            return [SourceChunk(enterprise_search._safe_id("security_assessment.md"), "SEC-217 remote evidence", 1.0)]
        if self.behaviour == "stale":
            return [SourceChunk("project_status.md", "Phoenix Release payment gateway", 1.0)]
        if self.behaviour == "empty":
            return []
        raise RuntimeError("engine still indexing")


def test_source_key_matches_titles_to_filenames_exactly():
    assert source_key("security_assessment.md") == source_key("Security Assessment")
    assert source_key("meeting_notes.md") != source_key("uat_meeting_notes.md")


def test_workspace_filter_accepts_title_less_vertex_ids():
    keys = workspace_source_keys(["security_assessment.md"])
    assert hit_in_workspace(enterprise_search._safe_id("security_assessment.md"), keys)
    assert hit_in_workspace("Security Assessment", keys)
    assert not hit_in_workspace("project_status.md", keys)
    assert not hit_in_workspace(enterprise_search._safe_id("meeting_notes.md"), keys)


def test_enterprise_retriever_accepts_title_less_remote_ids(sample_docs):
    r = EnterpriseRetriever(remote=_Remote("safe_id"))
    r.build(sample_docs)
    hits = r.search("release blockers")
    assert hits
    assert r.last_mode == RETRIEVAL_ACTIVE


def test_enterprise_retriever_uses_remote_when_it_returns_evidence(sample_docs):
    r = EnterpriseRetriever(remote=_Remote("ok"))
    r.build(sample_docs)
    hits = r.search("release blockers")
    assert hits[0].source == "security_assessment.md"
    assert r.last_mode == RETRIEVAL_ACTIVE


def test_enterprise_retriever_ignores_stale_remote_sources(sample_docs):
    r = EnterpriseRetriever(remote=_Remote("stale"))
    r.build(sample_docs)
    hits = r.search("release blockers")
    assert hits
    assert all(h.source != "project_status.md" for h in hits)
    assert r.last_mode == RETRIEVAL_FALLBACK
    assert "outside this workspace" in r.last_error


def test_enterprise_retriever_falls_back_on_error_and_empty(sample_docs):
    for behaviour in ("error", "empty", "unavailable"):
        r = EnterpriseRetriever(remote=_Remote(behaviour))
        r.build(sample_docs)
        hits = r.search("release blockers")
        assert hits, behaviour
        assert r.last_mode == RETRIEVAL_FALLBACK, behaviour
        if behaviour == "error":
            assert "indexing" in r.last_error


def test_vertex_search_status_truthful_in_demo_mode():
    label, detail = enterprise_search.status()
    assert label == "FALLBACK"
    assert enterprise_search.configured() is False
    assert enterprise_search.search("x") == []
