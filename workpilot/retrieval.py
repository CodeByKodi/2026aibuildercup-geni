"""Knowledge retrieval.

    KnowledgeRetriever (protocol)
        ├── VertexSearchRetriever  (Vertex AI Search / Agent Search)
        └── LocalRetriever         (TF-IDF over in-session documents)

`EnterpriseRetriever` is the facade used by the app: Vertex Search when it is
available and returns evidence, otherwise local retrieval. `last_mode` is
always one of RETRIEVAL_ACTIVE / RETRIEVAL_FALLBACK so the UI shows the truth.
"""
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Set, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import enterprise_search
from .models import SourceChunk

log = logging.getLogger("workpilot.retrieval")

RETRIEVAL_ACTIVE = "Vertex AI Search (ACTIVE)"
RETRIEVAL_FALLBACK = "Local TF-IDF (FALLBACK)"
NOT_FOUND = "Not found in workspace sources"

# TF-IDF cosine below this is noise, not evidence.
MIN_LOCAL_SCORE = 0.06


def source_key(name: str) -> str:
    """Compare Vertex titles and local filenames without treating 'meeting notes' as UAT notes."""
    stem = Path(str(name or "").split("/")[-1]).stem
    stem = re.sub(r"-[a-f0-9]{8,}$", "", stem)
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def workspace_source_keys(sources: Iterable[str]) -> Set[str]:
    keys: Set[str] = set()
    for name in sources:
        key = source_key(name)
        if key:
            keys.add(key)
        safe_id = enterprise_search._safe_id(name)
        keys.add(safe_id)
        keys.add(source_key(safe_id))
    return {k for k in keys if k}


def hit_in_workspace(source: str, keys: Set[str]) -> bool:
    raw = str(source or "").split("/")[-1]
    if not raw:
        return False
    if raw in keys or source_key(raw) in keys:
        return True
    return enterprise_search._safe_id(raw) in keys


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_BULLET = re.compile(r"^([-*+]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_EMPHASIS = re.compile(r"\*\*|__|`")


def _split_long(unit: str, size: int) -> List[str]:
    """Split a unit longer than `size` on word boundaries."""
    words, parts, current = unit.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > size:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        parts.append(current)
    return parts


def _section_units(lines: List[str]) -> List[str]:
    """Sentences, list items and table rows of one section, each a whole unit.

    Table rows are rewritten as "Header: value · ..." so a row carries its own
    column names and never displays as raw pipes.
    """
    units: List[str] = []
    paragraph: List[str] = []
    header: Optional[List[str]] = None

    def flush() -> None:
        if paragraph:
            text = " ".join(paragraph)
            paragraph.clear()
            units.extend(s.strip() for s in _SENTENCE_END.split(text) if s.strip())

    for raw in lines:
        line = _EMPHASIS.sub("", raw.strip())
        if line.startswith("|"):
            flush()
            if _TABLE_RULE.match(line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if header is None:
                header = cells
                continue
            pairs = [f"{h}: {v}" if h else v for h, v in zip(header, cells) if v]
            units.append(" · ".join(pairs) + ".")
            continue
        header = None
        if not line:
            flush()
            continue
        if _BULLET.match(line):
            flush()
            paragraph.append(re.sub(r"^[-*+]\s+", "", line))
            flush()
            continue
        paragraph.append(line)
    flush()
    return units


def chunk_sections(text: str, size: int = 900) -> List[Tuple[str, str]]:
    """Chunk markdown on headings, then pack whole sentences up to `size`.

    Returns (section heading, chunk text). A chunk never starts mid-word; the
    last sentence of a chunk is repeated at the start of the next chunk in
    the same section so context carries over.
    """
    sections: List[Tuple[str, List[str]]] = []
    heading, body = "", []
    for line in str(text or "").splitlines():
        match = _HEADING.match(line)
        if match:
            if body:
                sections.append((heading, body))
            heading, body = _EMPHASIS.sub("", match.group(1)).strip(), []
        else:
            body.append(line)
    if body:
        sections.append((heading, body))

    out: List[Tuple[str, str]] = []
    for name, lines in sections:
        units: List[str] = []
        for unit in _section_units(lines):
            units.extend(_split_long(unit, size) if len(unit) > size else [unit])
        current: List[str] = []
        for unit in units:
            if current and len(" ".join(current + [unit])) > size:
                out.append((name, " ".join(current)))
                carry = current[-1]
                current = [carry] if len(carry) + 1 + len(unit) <= size and len(carry) < size // 3 else []
            current.append(unit)
        if current:
            out.append((name, " ".join(current)))
    return out


_IDENTIFIER = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")
# Ordinary words that mix digits and letters: ordinals (18th) and numbers with a unit (5s, 14min, 3pm).
_NOT_AN_IDENTIFIER = re.compile(
    r"^\d+(st|nd|rd|th|s|ms|sec|secs|m|min|mins|h|hr|hrs|d|am|pm|x|k|kb|mb|gb|tb|pct)$", re.I
)


def missing_identifiers(query: str, corpus: str) -> List[str]:
    """Identifiers in the question (v13, SEC-999, PAY-1) that no workspace source mentions.

    A question about a release, ticket or version the workspace has never seen
    is out of scope, however many generic words it shares with the sources.
    """
    haystack = corpus.lower()
    missing = []
    for token in _IDENTIFIER.findall(query or ""):
        if _NOT_AN_IDENTIFIER.match(token):
            continue
        if not re.search(rf"(?<![a-z0-9]){re.escape(token.lower())}(?![a-z0-9])", haystack):
            missing.append(token)
    return missing


class KnowledgeRetriever(Protocol):
    def search(self, query: str, k: int = 6) -> List[SourceChunk]: ...


class LocalRetriever:
    def __init__(self):
        self.chunks: List[SourceChunk] = []
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        self.matrix = None
        self.corpus = ""

    def build(self, documents: Dict[str, str]):
        self.chunks = []
        self.corpus = "\n".join(documents.values())
        for source, text in documents.items():
            for index, (section, chunk) in enumerate(chunk_sections(text), start=1):
                self.chunks.append(SourceChunk(source=source, text=chunk, page=index, section=section))
        if self.chunks:
            self.matrix = self.vectorizer.fit_transform([f"{c.section}. {c.text}" for c in self.chunks])
        else:
            self.matrix = None

    def search(self, query: str, k: int = 5, min_score: float = MIN_LOCAL_SCORE) -> List[SourceChunk]:
        if self.matrix is None or not self.chunks:
            return []
        q = self.vectorizer.transform([query])
        scores = cosine_similarity(q, self.matrix).flatten()
        ranked = [i for i in scores.argsort()[::-1][:k] if scores[i] >= min_score]
        return [
            SourceChunk(self.chunks[i].source, self.chunks[i].text, float(scores[i]), self.chunks[i].page, self.chunks[i].section)
            for i in ranked
        ]

    def locate(self, source: str, text: str) -> Tuple[str, int]:
        """Best-matching local section for a remote snippet of `source`."""
        words = set(re.findall(r"[a-z0-9]+", str(text or "").lower()))
        best, best_overlap = ("", 1), 0
        key = source_key(source)
        for chunk in self.chunks:
            if source_key(chunk.source) != key:
                continue
            overlap = len(words & set(re.findall(r"[a-z0-9]+", chunk.text.lower())))
            if overlap > best_overlap:
                best, best_overlap = (chunk.section, chunk.page), overlap
        return best


class VertexSearchRetriever:
    """Thin retriever over the Discovery Engine adapter."""

    def available(self) -> bool:
        return enterprise_search.configured()

    def search(self, query: str, k: int = 6) -> List[SourceChunk]:
        if not self.available():
            return []
        return enterprise_search.search(query, k=k)


class EnterpriseRetriever:
    """Vertex AI Search first, local retrieval as the resilience fallback."""

    def __init__(self, remote: KnowledgeRetriever = None, local: LocalRetriever = None):
        self.remote = remote if remote is not None else VertexSearchRetriever()
        self.local = local if local is not None else LocalRetriever()
        self.last_mode = RETRIEVAL_FALLBACK
        self.last_error = ""
        self.not_found = ""

    def build(self, documents: Dict[str, str]):
        self.local.build(documents)

    def _local_name(self, source: str) -> str:
        key = source_key(source)
        for chunk in self.local.chunks:
            if source_key(chunk.source) == key or enterprise_search._safe_id(chunk.source) == source.split("/")[-1]:
                return chunk.source
        return source

    def search(self, query: str, k: int = 6) -> List[SourceChunk]:
        self.not_found = ""
        missing = missing_identifiers(query, getattr(self.local, "corpus", ""))
        if missing:
            self.not_found = f"{NOT_FOUND}: {', '.join(missing)} does not appear in any source"
            self.last_error = ""
            log.info("Out-of-scope question (%s): %s", ", ".join(missing), query[:120])
            return []
        keys = workspace_source_keys(c.source for c in self.local.chunks)
        available = getattr(self.remote, "available", lambda: True)()
        if available:
            try:
                remote = self.remote.search(query, k=max(k, 8))
                scoped = [hit for hit in remote if hit_in_workspace(hit.source, keys)] if keys else list(remote)
                if scoped:
                    self.last_mode = RETRIEVAL_ACTIVE
                    self.last_error = ""
                    for hit in scoped:
                        hit.source = self._local_name(hit.source)
                        if not hit.section:
                            hit.section, located_page = self.local.locate(hit.source, hit.snippet or hit.text)
                            hit.page = hit.page if hit.page > 1 else located_page
                    return scoped[:k]
                if remote:
                    self.last_error = "Vertex AI Search returned documents outside this workspace; using local retrieval"
                else:
                    self.last_error = "Vertex AI Search returned no results (index may still be building)"
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            log.warning("Vertex AI Search fell back to local retrieval: %s | query=%r", self.last_error, query[:120])
        else:
            self.last_error = "Vertex AI Search is not configured"
            log.info("Vertex AI Search not configured; local retrieval for query=%r", query[:120])
        self.last_mode = RETRIEVAL_FALLBACK
        hits = self.local.search(query, k=k)
        if not hits:
            self.not_found = NOT_FOUND
        return hits


class SessionRetriever(EnterpriseRetriever):
    """Workspace search plus the documents a person uploaded into this session.

    Vertex AI Search indexes the shared workspace only, so one person's upload never
    enters everyone's index. Uploads are searched locally and their passages are
    added after the workspace hits; scores from the two engines are not compared.
    """

    UPLOAD_HITS = 3

    def __init__(self, uploads: Optional[Dict[str, str]] = None, **kwargs):
        super().__init__(**kwargs)
        self.uploads = dict(uploads or {})
        self.upload_index = LocalRetriever()
        self.upload_hits = 0

    def build(self, documents: Dict[str, str]):
        super().build(documents)  # documents include the uploads: identifier checks and locate see them
        if self.uploads:
            self.upload_index.build(self.uploads)

    def search(self, query: str, k: int = 6) -> List[SourceChunk]:
        hits = super().search(query, k=k)
        self.upload_hits = 0
        if not self.uploads or (not hits and self.not_found and self.not_found != NOT_FOUND):
            return hits  # an identifier no source mentions: nothing to add
        seen = {(h.source, h.text[:120]) for h in hits}
        extra = [h for h in self.upload_index.search(query, k=self.UPLOAD_HITS) if (h.source, h.text[:120]) not in seen]
        if extra:
            self.upload_hits = len(extra)
            self.not_found = ""
        return hits + extra
