"""Knowledge Tool: grounded enterprise evidence through Vertex AI Search (local fallback labelled)."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from .. import presentation, workflow
from .run import AgentRun


def make(run: AgentRun) -> List[Callable[..., Dict[str, Any]]]:
    def search_knowledge(query: str) -> dict:
        """Search the enterprise workspace and return ranked, cited evidence passages.

        Call this first. Every decision and action must be grounded in what it returns.

        Args:
            query: the question or search phrase, in plain language.
        """
        hits = run.retriever.search(query)
        run.question = run.question or query
        run.hits = list(hits)
        run.retrieval_mode = run.retriever.last_mode
        run.not_found = getattr(run.retriever, "not_found", "") or ""
        provider = presentation.provider_badge(run.retrieval_mode)
        workflow._emit(
            "search_performed",
            {"question": query, "retrieval": run.retrieval_mode, "hits": len(hits), "origin": "agent"},
            source_count=len({h.source for h in hits}),
        )
        if not hits:
            return {
                "found": False,
                "provider": provider,
                "summary": run.not_found or "Not found in workspace sources",
                "instruction": "Do not answer or record a decision; tell the user nothing in the workspace answers this.",
            }
        evidence = [
            {
                "id": f"E{i}",
                "source": h.source,
                "section": h.section,
                "excerpt": h.text[:1500],  # the full segment; the short snippet is only for display
            }
            for i, h in enumerate(hits, start=1)
        ]
        return {
            "found": True,
            "provider": provider,
            "summary": f"{len(evidence)} passages from {len({h.source for h in hits})} sources via {provider}",
            "evidence": evidence,
        }

    def get_document(source: str) -> dict:
        """Read the full text of one workspace document when a passage is not enough context.

        Args:
            source: the document filename exactly as search_knowledge returned it.
        """
        text = run.documents.get(source)
        if text is None:
            return {"error": f"No workspace document named {source!r}", "available": sorted(run.documents)}
        return {"source": source, "summary": f"{source}: {len(text.split())} words", "text": text[:8000]}

    return [search_knowledge, get_document]
