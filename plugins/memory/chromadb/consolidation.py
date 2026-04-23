"""ChromaDB Memory Plugin — Memory Consolidation.

Auto-manages the flat-file memory budget by scoring entries by importance,
finding similar entries for merging via ChromaDB embeddings, and overflowing
low-priority entries to ChromaDB vector storage.

Ported from tools/memory_consolidation.py.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENTRY_DELIMITER = "\n§\n"
DELIMITER_LEN = len(ENTRY_DELIMITER)  # 3 chars


# ---------------------------------------------------------------------------
# Importance scoring patterns — ordered by priority (first match wins)
# ---------------------------------------------------------------------------

_IMPORTANCE_RULES: List[Tuple[float, List[str]]] = [
    # 1.0 — User corrections, preferences, explicit "remember this"
    (1.0, [
        r'\bremember\s+(this|that)\b',
        r'\balways\s+(use|prefer|do|avoid)\b',
        r'\bnever\s+(use|do|say)\b',
        r'\bcorrection\b',
        r'\bprefer(s|ence)?\b.*\b(over|instead|rather)\b',
        r'\bdon\'t\s+(like|want|use)\b',
        r'\bi\s+(like|want|prefer|need|hate|dislike)\b',
        r'\bimportant\s*:\s*',
    ]),
    # 0.9 — User personal details
    (0.9, [
        r'\bmy\s+name\s+is\b',
        r'\bname\s*[:=]\s*',
        r'\btimezone\s*[:=]\s*',
        r'\blocated?\s+in\b',
        r'\brole\s*[:=]\s*',
        r'\bjob\s+title\b',
        r'\bworks?\s+(at|for)\b',
        r'\bemail\s*[:=]\s*',
        r'\buser\s+(is|name)\b',
    ]),
    # 0.8 — Environment/infra facts
    (0.8, [
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',  # IP addresses
        r'\b(ssh|scp)\s+',
        r'\bserver\s+(at|is|runs?)\b',
        r'\bversion\s*[:=]?\s*\d',
        r'\bpath\s*[:=]\s*[/~]',
        r'\bport\s*[:=]?\s*\d{2,5}\b',
        r'\bhost(name)?\s*[:=]\s*',
        r'\bOS\s*[:=]\s*',
        r'\b(python|node|ruby|go|java)\s+\d+\.\d+',
        r'\binfra(structure)?\b',
        r'\bGPU\b',
        r'\b(venv|virtualenv|conda)\b.*\bpath\b',
    ]),
    # 0.7 — API quirks, tool behavior
    (0.7, [
        r'\bAPI\b',
        r'\btool\s+(quirk|behavior|bug|issue|note)\b',
        r'\bworkaround\b',
        r'\bgotcha\b',
        r'\bcaveat\b',
        r'\brate\s*limit',
        r'\btimeout\b.*\b(set|increase|is)\b',
        r'\bheader\b.*\brequire',
        r'\bendpoint\b',
        r'\bauth(entication)?\s+(requires?|uses?|is)\b',
    ]),
    # 0.6 — Project conventions, workflow notes
    (0.6, [
        r'\bconvention\b',
        r'\bworkflow\b',
        r'\bpattern\b',
        r'\bstyle\s+(guide|rule)\b',
        r'\bbranch(ing)?\s+(strategy|model|naming)\b',
        r'\bcommit\s+(message|format|style)\b',
        r'\bcode\s+review\b',
        r'\bproject\s+(uses?|structure)\b',
        r'\bmonorepo\b',
        r'\blint(ing|er)?\b',
        r'\bformat(ting|ter)?\s+(is|uses?)\b',
    ]),
    # 0.5 — Procedural knowledge
    (0.5, [
        r'\bstep\s+\d\b',
        r'\bfirst\b.*\bthen\b',
        r'\bprocedure\b',
        r'\bhow\s+to\b',
        r'\bprocess\s+(for|to|is)\b',
        r'\brun\s+(this|the)\b',
        r'\bcommand\s*:\s*',
        r'\bscript\b.*\b(runs?|does)\b',
    ]),
]

_DEFAULT_SCORE = 0.4  # General notes that don't match any pattern


class MemoryConsolidator:
    """Consolidates memory entries to fit within a character budget.

    Scores entries by importance, finds similar entries for merging,
    and overflows low-priority entries to ChromaDB vector storage.
    """

    def importance_score(self, content: str) -> float:
        """Score a memory entry's importance (0.0-1.0) using keyword/pattern matching."""
        if not content or not content.strip():
            return 0.0

        content_lower = content.lower()
        for score, patterns in _IMPORTANCE_RULES:
            for pattern in patterns:
                if re.search(pattern, content_lower, re.IGNORECASE):
                    return score

        return _DEFAULT_SCORE

    def find_similar_entries(
        self,
        entries: List[str],
        threshold: float = 0.85,
        vector_provider=None,
    ) -> List[Tuple[int, int, float]]:
        """Find pairs of entries that are semantically similar (merge candidates).

        Uses the underlying ChromaDB collection to compute pairwise similarity
        via embedding distance. Returns empty list without ChromaDB (graceful degradation).
        """
        if not entries or len(entries) < 2 or vector_provider is None:
            return []

        if not getattr(vector_provider, '_available', False):
            return []

        similar_pairs: List[Tuple[int, int, float]] = []

        try:
            collection = vector_provider._get_collection("memory")
            if collection is None:
                return []

            # Store entries temporarily for comparison
            temp_ids = []
            temp_docs = []
            for i, entry in enumerate(entries):
                doc_id = f"_consolidate_tmp_{i}"
                temp_ids.append(doc_id)
                temp_docs.append(entry)

            # Compute explicit embeddings via the safe provider path
            # (Forge -> OpenRouter fallback). Never use ChromaDB auto-embedding
            # which can produce 384-dim vectors incompatible with 1024-dim collections.
            temp_embeddings = vector_provider._embed(temp_docs)

            # Upsert all entries with explicit embeddings
            collection.upsert(
                ids=temp_ids,
                documents=temp_docs,
                embeddings=temp_embeddings,
                metadatas=[{"target": "memory", "temp": True}] * len(entries),
            )

            # Query each entry against all others using pre-computed embeddings
            for i, entry in enumerate(entries):
                results = collection.query(
                    query_embeddings=[temp_embeddings[i]],
                    n_results=min(len(entries), 10),
                    include=["distances"],
                )

                if not results or not results.get("ids"):
                    continue

                result_ids = results["ids"][0]
                distances = results["distances"][0]

                for j_pos, (rid, dist) in enumerate(zip(result_ids, distances)):
                    if not rid.startswith("_consolidate_tmp_"):
                        continue
                    j = int(rid.split("_")[-1])
                    if j <= i:
                        continue  # skip self and already-seen pairs

                    # Convert L2 distance to similarity: 1/(1+d)
                    similarity = 1.0 / (1.0 + dist)
                    if similarity >= threshold:
                        similar_pairs.append((i, j, similarity))

            # Clean up temporary entries
            try:
                collection.delete(ids=temp_ids)
            except Exception:
                pass

        except Exception as e:
            logger.warning("find_similar_entries failed: %s", e)
            return []

        return similar_pairs

    @staticmethod
    def _merge_entries(entry_a: str, entry_b: str) -> str:
        """Merge two similar entries, keeping unique information from both."""
        lines_a = [l.strip() for l in entry_a.strip().splitlines() if l.strip()]
        lines_b = [l.strip() for l in entry_b.strip().splitlines() if l.strip()]

        a_lower = [l.lower() for l in lines_a]
        a_joined_lower = " ".join(a_lower)

        new_lines = []
        for line_b in lines_b:
            lb_lower = line_b.lower()
            if lb_lower in a_joined_lower:
                continue
            if any(al in lb_lower for al in a_lower if len(al) > 10):
                continue
            new_lines.append(line_b)

        if new_lines:
            merged = entry_a.strip() + " | " + "; ".join(new_lines)
        else:
            merged = entry_a.strip()

        return merged

    def consolidate(
        self,
        entries: List[str],
        char_budget: int = 2200,
        vector_provider=None,
    ) -> Dict[str, Any]:
        """Consolidate memory entries to fit within the character budget.

        Pipeline:
          1. Score each entry by importance
          2. Find similar pairs and merge them
          3. Sort by importance (descending)
          4. Select entries that fit within budget
          5. Overflow the rest to ChromaDB (if vector_provider available)
        """
        if not entries:
            return {"kept": [], "merged": [], "overflowed": [], "total_chars": 0}

        # Step 1: Score all entries
        scored: List[Tuple[float, int, str]] = []
        for i, entry in enumerate(entries):
            score = self.importance_score(entry)
            scored.append((score, i, entry))

        # Step 2: Find and merge similar pairs
        similar_pairs = self.find_similar_entries(entries, threshold=0.85, vector_provider=vector_provider)
        merged_records: List[Tuple[str, str, str]] = []
        merged_indices: set = set()

        for idx1, idx2, sim in sorted(similar_pairs, key=lambda x: x[2], reverse=True):
            if idx1 in merged_indices or idx2 in merged_indices:
                continue

            score1 = scored[idx1][0]
            score2 = scored[idx2][0]
            entry1 = entries[idx1]
            entry2 = entries[idx2]

            if score1 >= score2:
                merged_text = self._merge_entries(entry1, entry2)
                merge_score = score1
            else:
                merged_text = self._merge_entries(entry2, entry1)
                merge_score = score2

            merged_records.append((entry1, entry2, merged_text))
            merged_indices.add(idx1)
            merged_indices.add(idx2)
            scored.append((merge_score, len(scored), merged_text))

        # Step 3: Build candidate list (exclude merged originals)
        candidates: List[Tuple[float, str]] = []
        for score, idx, entry in scored:
            if idx < len(entries) and idx in merged_indices:
                continue
            candidates.append((score, entry))

        candidates.sort(key=lambda x: (-x[0], len(x[1])))

        # Step 4: Select within budget
        kept: List[str] = []
        total_chars = 0
        overflow_candidates: List[str] = []

        for score, entry in candidates:
            needed = len(entry) + (DELIMITER_LEN if kept else 0)
            if total_chars + needed <= char_budget:
                kept.append(entry)
                total_chars += needed
            else:
                overflow_candidates.append(entry)

        # Step 5: Store overflow in ChromaDB
        overflowed: List[str] = []
        for entry in overflow_candidates:
            if vector_provider is not None:
                try:
                    importance = self.importance_score(entry)
                    vector_provider.store_memory(
                        entry, "memory", {"importance": importance, "source": "consolidation_overflow"}
                    )
                    overflowed.append(entry)
                except Exception as e:
                    logger.warning("Failed to store overflow entry in ChromaDB: %s", e)
                    overflowed.append(entry)
            else:
                overflowed.append(entry)

        return {
            "kept": kept,
            "merged": merged_records,
            "overflowed": overflowed,
            "total_chars": total_chars,
        }

    def extract_facts_from_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract important facts from conversation messages before compression.

        Simple extraction: looks for assistant messages that contain memory-worthy
        patterns (IP addresses, paths, versions, user preferences, etc.).

        Returns a list of extracted fact strings.
        """
        facts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue

            # Only look at user and assistant messages
            if role not in ("user", "assistant"):
                continue

            # Check each line for importance
            for line in content.split("\n"):
                line = line.strip()
                if not line or len(line) < 10 or len(line) > 300:
                    continue
                score = self.importance_score(line)
                if score >= 0.7:
                    facts.append(line)

        # Deduplicate
        seen = set()
        unique = []
        for f in facts:
            key = f.lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(f)

        return unique[:20]  # Cap at 20 facts
