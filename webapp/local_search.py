"""
Intelligent local full-text search for pd_reloaded.

A pure-Python port of Stremio's local-search algorithm
(https://github.com/Stremio/local-search, MIT licence).

Implements:
  - Tokenisation (colon/comma → space, strip hyphens, lowercase split)
  - Inverted index with token occurrence counts
  - TF-IDF scoring
  - Levenshtein-distance fuzzy matching with edit-distance boost
  - Prefix matching with prefix-ratio boost
  - Per-document popularity boost
  - Score-threshold filtering (relative to top result)

Attribution:
    Search algorithm inspired by Stremio's local-search
    https://github.com/Stremio/local-search — MIT licence
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ── Defaults (mirrors Stremio's Rust constants) ────────────────────
DEFAULT_MAX_EDIT_DISTANCE = 1
DEFAULT_MAX_EDIT_DISTANCE_BOOST = 2.0
DEFAULT_MAX_PREFIX_BOOST = 1.5
DEFAULT_SCORE_THRESHOLD = 0.48


# ── Tokeniser ──────────────────────────────────────────────────────

_COLON_COMMA = re.compile(r"[,:]")
_HYPHEN = re.compile(r"-")


def tokenize(text: str) -> list[str]:
    """Tokenise text the same way Stremio does.

    Replace ``:`` and ``,`` with space, remove ``-``, lowercase, split
    on whitespace.

    >>> tokenize("Spider-Man: Far from Home")
    ['spiderman', 'far', 'from', 'home']
    """
    text = _COLON_COMMA.sub(" ", text)
    text = _HYPHEN.sub("", text)
    return [t for t in text.lower().split() if t]


# ── Levenshtein distance ──────────────────────────────────────────

def _levenshtein(a: str, b: str, max_dist: int) -> Optional[int]:
    """Compute Levenshtein distance, returning *None* if > *max_dist*.

    Uses the classic two-row DP with early exit.
    """
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return None

    if la > lb:
        a, b = b, a
        la, lb = lb, la

    prev = list(range(la + 1))
    for j in range(1, lb + 1):
        curr = [j] + [0] * la
        row_min = j
        for i in range(1, la + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[i] = min(curr[i - 1] + 1, prev[i] + 1, prev[i - 1] + cost)
            if curr[i] < row_min:
                row_min = curr[i]
        if row_min > max_dist:
            return None
        prev = curr

    return prev[la] if prev[la] <= max_dist else None


# ── Related-token data ─────────────────────────────────────────────

@dataclass
class _RelatedTokenData:
    distance: Optional[int] = None
    prefix_ratio: Optional[float] = None


# ── LocalSearch ────────────────────────────────────────────────────

class LocalSearch:
    """Client-side full-text search index.

    Usage::

        ls = LocalSearch(items, text_fn=lambda x: x["name"],
                         boost_fn=lambda x: x.get("popularity", 0) * 0.01)
        results = ls.search("jujutsu kisen", max_results=20)
        # → list of (item, score)
    """

    def __init__(
        self,
        documents: list[Any],
        text_fn: Callable[[Any], str],
        boost_fn: Callable[[Any], float] | None = None,
        *,
        max_edit_distance: int = DEFAULT_MAX_EDIT_DISTANCE,
        max_edit_distance_boost: float = DEFAULT_MAX_EDIT_DISTANCE_BOOST,
        max_prefix_boost: float = DEFAULT_MAX_PREFIX_BOOST,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    ):
        self.max_edit_distance = max_edit_distance
        self.max_edit_distance_boost = max_edit_distance_boost
        self.max_prefix_boost = max_prefix_boost
        self.score_threshold = score_threshold

        if boost_fn is None:
            boost_fn = lambda _: 1.0  # noqa: E731

        # doc_id → (document, token_count, doc_boost)
        self._docs: dict[int, tuple[Any, int, float]] = {}

        # token → {doc_id: occurrence_count}
        self._index: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

        # Sorted token list for prefix search
        self._sorted_tokens: list[str] = []

        self._build(documents, text_fn, boost_fn)

    # ── Build ──────────────────────────────────────────────────

    def _build(self, documents, text_fn, boost_fn):
        for doc_id, doc in enumerate(documents):
            text = text_fn(doc)
            tokens = tokenize(text)
            self._docs[doc_id] = (doc, len(tokens) or 1, boost_fn(doc))
            for tok in tokens:
                self._index[tok][doc_id] += 1

        self._sorted_tokens = sorted(self._index.keys())

    # ── Search ─────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 20) -> list[tuple[Any, float]]:
        """Search documents ordered by score descending."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        # Accumulate scores per doc_id
        scores: dict[int, float] = defaultdict(float)

        for qt in query_tokens:
            related = self._related_tokens(qt)
            for token, data in related.items():
                pairs = self._index.get(token)
                if not pairs:
                    continue
                num_docs_with_token = len(pairs)
                for doc_id, occ_count in pairs.items():
                    _, token_count, doc_boost = self._docs[doc_id]
                    tfidf = self._tf_idf(occ_count, token_count, num_docs_with_token)
                    score = self._score(tfidf, data, doc_boost)
                    scores[doc_id] += score

        if not scores:
            return []

        # Sort descending
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        ranked = ranked[:max_results]

        # Threshold filter
        highest = ranked[0][1] if ranked else 0
        threshold = highest * self.score_threshold

        results = []
        for doc_id, score in ranked:
            if score <= threshold:
                continue
            doc = self._docs[doc_id][0]
            results.append((doc, score))

        return results

    # ── Related tokens ─────────────────────────────────────────

    def _related_tokens(self, query_token: str) -> dict[str, _RelatedTokenData]:
        fuzzy_enabled = self.max_edit_distance > 0 and self.max_edit_distance_boost > 0
        prefix_enabled = self.max_prefix_boost > 0

        related: dict[str, _RelatedTokenData] = {}

        if fuzzy_enabled:
            self._add_tokens_in_distance(query_token, related)
        if prefix_enabled:
            self._add_tokens_with_prefix(query_token, related)
        if not fuzzy_enabled and not prefix_enabled:
            self._add_identical_tokens(query_token, related)

        return related

    def _add_tokens_in_distance(self, query_token: str, related: dict[str, _RelatedTokenData]):
        for token in self._index:
            dist = _levenshtein(query_token, token, self.max_edit_distance)
            if dist is not None:
                if token in related:
                    related[token].distance = dist
                else:
                    related[token] = _RelatedTokenData(distance=dist)

    def _add_tokens_with_prefix(self, query_token: str, related: dict[str, _RelatedTokenData]):
        qt_len = len(query_token)
        if qt_len == 0:
            return
        # Binary-search-style prefix scan over sorted tokens
        import bisect
        lo = bisect.bisect_left(self._sorted_tokens, query_token)
        for i in range(lo, len(self._sorted_tokens)):
            token = self._sorted_tokens[i]
            if not token.startswith(query_token):
                break
            ratio = qt_len / len(token)
            if token in related:
                related[token].prefix_ratio = ratio
            else:
                related[token] = _RelatedTokenData(prefix_ratio=ratio)

    def _add_identical_tokens(self, query_token: str, related: dict[str, _RelatedTokenData]):
        if query_token in self._index:
            related[query_token] = _RelatedTokenData()

    # ── Scoring ────────────────────────────────────────────────

    def _tf_idf(self, occ_count: int, token_count: int, num_docs_with_token: int) -> float:
        tf = occ_count / token_count
        n = len(self._docs)
        idf = math.log10(n / num_docs_with_token) if num_docs_with_token else 0
        return tf * idf

    def _score(self, tfidf: float, data: _RelatedTokenData, doc_boost: float) -> float:
        dist_boost = 1.0
        if data.distance is not None:
            diff = self.max_edit_distance - data.distance
            dist_boost = diff * self.max_edit_distance_boost + 1.0

        prefix_boost = 1.0
        if data.prefix_ratio is not None:
            prefix_boost = data.prefix_ratio * self.max_prefix_boost + 1.0

        return (1.0 + tfidf) * dist_boost * prefix_boost * doc_boost
