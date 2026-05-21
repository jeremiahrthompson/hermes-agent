#!/usr/bin/env python3
"""
Session Search Tool - Long-Term Conversation Recall

Searches past session transcripts in SQLite via FTS5, then summarizes the top
matching sessions using the configured auxiliary session_search model (same
pattern as web_extract). By default, auxiliary "auto" routing uses the main
chat provider/model unless the user overrides auxiliary.session_search.
Returns focused summaries of past conversations rather than raw transcripts,
keeping the main model's context window clean.

Flow:
  1. FTS5 search finds matching messages ranked by relevance
  2. Groups by session, takes the top N unique sessions (default 3)
  3. Loads each session's conversation, truncates to ~100k chars centered on matches
  4. Sends to the configured auxiliary model with a focused summarization prompt
  5. Returns per-session summaries with metadata
"""

import asyncio
import concurrent.futures
import json
import logging
import re
from typing import Dict, Any, List, Optional, Union, Callable

from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
MAX_SESSION_CHARS = 100_000
MAX_SUMMARY_TOKENS = 10000


def _get_session_search_max_concurrency(default: int = 3) -> int:
    """Read auxiliary.session_search.max_concurrency with sane bounds."""
    try:
        from hermes_cli.config import load_config
        config = load_config()
    except ImportError:
        return default
    aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
    task_config = aux.get("session_search", {}) if isinstance(aux, dict) else {}
    if not isinstance(task_config, dict):
        return default
    raw = task_config.get("max_concurrency")
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 5))


def _format_timestamp(ts: Union[int, float, str, None]) -> str:
    """Convert a Unix timestamp (float/int) or ISO string to a human-readable date.

    Returns "unknown" for None, str(ts) if conversion fails.
    """
    if ts is None:
        return "unknown"
    try:
        if isinstance(ts, (int, float)):
            from datetime import datetime
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%B %d, %Y at %I:%M %p")
        if isinstance(ts, str):
            if ts.replace(".", "").replace("-", "").isdigit():
                from datetime import datetime
                dt = datetime.fromtimestamp(float(ts))
                return dt.strftime("%B %d, %Y at %I:%M %p")
            return ts
    except (ValueError, OSError, OverflowError) as e:
        # Log specific errors for debugging while gracefully handling edge cases
        logging.debug("Failed to format timestamp %s: %s", ts, e, exc_info=True)
    except Exception as e:
        logging.debug("Unexpected error formatting timestamp %s: %s", ts, e, exc_info=True)
    return str(ts)


def _format_conversation(messages: List[Dict[str, Any]]) -> str:
    """Format session messages into a readable transcript for summarization."""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content") or ""
        tool_name = msg.get("tool_name")

        if role == "TOOL" and tool_name:
            # Truncate long tool outputs
            if len(content) > 500:
                content = content[:250] + "\n...[truncated]...\n" + content[-250:]
            parts.append(f"[TOOL:{tool_name}]: {content}")
        elif role == "ASSISTANT":
            # Include tool call names if present
            tool_calls = msg.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                tc_names = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        name = tc.get("name") or tc.get("function", {}).get("name", "?")
                        tc_names.append(name)
                if tc_names:
                    parts.append(f"[ASSISTANT]: [Called: {', '.join(tc_names)}]")
                if content:
                    parts.append(f"[ASSISTANT]: {content}")
            else:
                parts.append(f"[ASSISTANT]: {content}")
        else:
            parts.append(f"[{role}]: {content}")

    return "\n\n".join(parts)


def _truncate_around_matches(
    full_text: str, query: str, max_chars: int = MAX_SESSION_CHARS
) -> str:
    """
    Truncate a conversation transcript to *max_chars*, choosing a window
    that maximises coverage of positions where the *query* actually appears.

    Strategy (in priority order):
    1. Try to find the full query as a phrase (case-insensitive).
    2. If no phrase hit, look for positions where all query terms appear
       within a 200-char proximity window (co-occurrence).
    3. Fall back to individual term positions.

    Once candidate positions are collected the function picks the window
    start that covers the most of them.
    """
    if len(full_text) <= max_chars:
        return full_text

    text_lower = full_text.lower()
    query_lower = query.lower().strip()
    match_positions: list[int] = []

    # --- 1. Full-phrase search ------------------------------------------------
    phrase_pat = re.compile(re.escape(query_lower))
    match_positions = [m.start() for m in phrase_pat.finditer(text_lower)]

    # --- 2. Proximity co-occurrence of all terms (within 200 chars) -----------
    if not match_positions:
        terms = query_lower.split()
        if len(terms) > 1:
            # Collect every occurrence of each term
            term_positions: dict[str, list[int]] = {}
            for t in terms:
                term_positions[t] = [
                    m.start() for m in re.finditer(re.escape(t), text_lower)
                ]
            # Slide through positions of the rarest term and check proximity
            rarest = min(terms, key=lambda t: len(term_positions.get(t, [])))
            for pos in term_positions.get(rarest, []):
                if all(
                    any(abs(p - pos) < 200 for p in term_positions.get(t, []))
                    for t in terms
                    if t != rarest
                ):
                    match_positions.append(pos)

    # --- 3. Individual term positions (last resort) ---------------------------
    if not match_positions:
        terms = query_lower.split()
        for t in terms:
            for m in re.finditer(re.escape(t), text_lower):
                match_positions.append(m.start())

    if not match_positions:
        # Nothing at all — take from the start
        truncated = full_text[:max_chars]
        suffix = "\n\n...[later conversation truncated]..." if max_chars < len(full_text) else ""
        return truncated + suffix

    # --- Pick window that covers the most match positions ---------------------
    match_positions.sort()

    best_start = 0
    best_count = 0
    for candidate in match_positions:
        ws = max(0, candidate - max_chars // 4)  # bias: 25% before, 75% after
        we = ws + max_chars
        if we > len(full_text):
            ws = max(0, len(full_text) - max_chars)
            we = len(full_text)
        count = sum(1 for p in match_positions if ws <= p < we)
        if count > best_count:
            best_count = count
            best_start = ws

    start = best_start
    end = min(len(full_text), start + max_chars)

    truncated = full_text[start:end]
    prefix = "...[earlier conversation truncated]...\n\n" if start > 0 else ""
    suffix = "\n\n...[later conversation truncated]..." if end < len(full_text) else ""
    return prefix + truncated + suffix


async def _summarize_session(
    conversation_text: str, query: str, session_meta: Dict[str, Any]
) -> Optional[str]:
    """Summarize a single session conversation focused on the search query."""
    system_prompt = (
        "You are reviewing a past conversation transcript to help recall what happened. "
        "Summarize the conversation with a focus on the search topic. Include:\n"
        "1. What the user asked about or wanted to accomplish\n"
        "2. What actions were taken and what the outcomes were\n"
        "3. Key decisions, solutions found, or conclusions reached\n"
        "4. Any specific commands, files, URLs, or technical details that were important\n"
        "5. Anything left unresolved or notable\n\n"
        "Be thorough but concise. Preserve specific details (commands, paths, error messages) "
        "that would be useful to recall. Write in past tense as a factual recap."
    )

    source = session_meta.get("source", "unknown")
    started = _format_timestamp(session_meta.get("started_at"))

    user_prompt = (
        f"Search topic: {query}\n"
        f"Session source: {source}\n"
        f"Session date: {started}\n\n"
        f"CONVERSATION TRANSCRIPT:\n{conversation_text}\n\n"
        f"Summarize this conversation with focus on: {query}"
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await async_call_llm(
                task="session_search",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=MAX_SUMMARY_TOKENS,
            )
            content = extract_content_or_reasoning(response)
            if content:
                return content
            # Reasoning-only / empty — let the retry loop handle it
            logging.warning("Session search LLM returned empty content (attempt %d/%d)", attempt + 1, max_retries)
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
                continue
            return content
        except RuntimeError:
            logging.warning("No auxiliary model available for session summarization")
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
            else:
                logging.warning(
                    "Session summarization failed after %d attempts: %s",
                    max_retries,
                    e,
                    exc_info=True,
                )
                return None


# Sources that are excluded from session browsing/searching by default.
# Third-party integrations (Paperclip agents, etc.) tag their sessions with
# HERMES_SESSION_SOURCE=tool so they don't clutter the user's session history.
_HIDDEN_SESSION_SOURCES = ("tool",)


def _list_recent_sessions(db, limit: int, current_session_id: str = None) -> str:
    """Return metadata for the most recent sessions (no LLM calls)."""
    try:
        sessions = db.list_sessions_rich(
            limit=limit + 5,
            exclude_sources=list(_HIDDEN_SESSION_SOURCES),
            order_by_last_active=True,
        )  # fetch extra to skip current

        # Resolve current session lineage to exclude it
        current_root = None
        if current_session_id:
            try:
                sid = current_session_id
                visited = set()
                current_root = current_session_id
                while sid and sid not in visited:
                    visited.add(sid)
                    current_root = sid
                    s = db.get_session(sid)
                    parent = s.get("parent_session_id") if s else None
                    sid = parent if parent else None
            except Exception:
                current_root = current_session_id

        results = []
        for s in sessions:
            sid = s.get("id", "")
            if current_root and (sid == current_root or sid == current_session_id):
                continue
            # Skip child/delegation sessions (they have parent_session_id)
            if s.get("parent_session_id"):
                continue
            results.append({
                "session_id": sid,
                "title": s.get("title") or None,
                "source": s.get("source", ""),
                "started_at": s.get("started_at", ""),
                "last_active": s.get("last_active", ""),
                "message_count": s.get("message_count", 0),
                "preview": s.get("preview", ""),
            })
            if len(results) >= limit:
                break

        return json.dumps({
            "success": True,
            "mode": "recent",
            "results": results,
            "count": len(results),
            "message": f"Showing {len(results)} most recent sessions. Use a keyword query to search specific topics.",
        }, ensure_ascii=False)
    except Exception as e:
        logging.error("Error listing recent sessions: %s", e, exc_info=True)
        return tool_error(f"Failed to list recent sessions: {e}", success=False)


def session_search(
    query: str,
    role_filter: str = None,
    limit: int = 3,
    mode: str = "fast",
    db=None,
    current_session_id: str = None,
    semantic_search: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
) -> str:
    """
    Search past sessions and return matching conversations.

    Default mode is "fast": FTS5 only, no LLM calls, returns session IDs,
    metadata, and hit snippets immediately. Use mode="summary" when a deeper
    LLM-generated recap using the configured auxiliary session_search model is
    needed after the right session has been identified.
    The current session is excluded from results since the agent already has that context.
    """
    if db is None:
        try:
            from hermes_state import SessionDB

            db = SessionDB()
        except Exception:
            logging.debug("SessionDB unavailable for session_search", exc_info=True)
            from hermes_state import format_session_db_unavailable
            return tool_error(format_session_db_unavailable(), success=False)

    # Defensive: models (especially open-source) may send non-int limit values
    # (None when JSON null, string "int", or even a type object).  Coerce to a
    # safe integer before any arithmetic/comparison to prevent TypeError.
    if not isinstance(limit, int):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 3
    limit = max(1, min(limit, 5))  # Clamp to [1, 5]

    # Recent sessions mode: when query is empty, return metadata for recent sessions.
    # No LLM calls — just DB queries for titles, previews, timestamps.
    if not query or not query.strip():
        return _list_recent_sessions(db, limit, current_session_id)

    query = query.strip()
    mode = (mode or "fast").strip().lower()
    if mode not in {"fast", "hybrid", "semantic", "summary", "summaries", "full"}:
        mode = "fast"
    summarize = mode in {"summary", "summaries", "full"}
    hybrid = mode in {"hybrid", "semantic"}

    try:
        # Parse role filter
        role_list = None
        if role_filter and role_filter.strip():
            role_list = [r.strip() for r in role_filter.split(",") if r.strip()]

        # FTS5 search -- get matches ranked by relevance
        raw_results = db.search_messages(
            query=query,
            role_filter=role_list,
            exclude_sources=list(_HIDDEN_SESSION_SOURCES),
            limit=50,  # Get more matches to find unique sessions
            offset=0,
        )

        if not raw_results and not hybrid:
            return json.dumps({
                "success": True,
                "query": query,
                "results": [],
                "count": 0,
                "message": "No matching sessions found.",
            }, ensure_ascii=False)

        # Resolve child sessions to their parent — delegation stores detailed
        # content in child sessions, but the user's conversation is the parent.
        def _resolve_to_parent(session_id: str) -> str:
            """Walk delegation chain to find the root parent session ID."""
            visited = set()
            sid = session_id
            while sid and sid not in visited:
                visited.add(sid)
                try:
                    session = db.get_session(sid)
                    if not session:
                        break
                    parent = session.get("parent_session_id")
                    if parent:
                        sid = parent
                    else:
                        break
                except Exception as e:
                    logging.debug(
                        "Error resolving parent for session %s: %s",
                        sid,
                        e,
                        exc_info=True,
                    )
                    break
            return sid

        current_lineage_root = (
            _resolve_to_parent(current_session_id) if current_session_id else None
        )

        # Group by resolved (parent) session_id, dedup, skip the current
        # session lineage. Compression and delegation create child sessions
        # that still belong to the same active conversation.
        seen_sessions = {}
        for result in raw_results:
            raw_sid = result["session_id"]
            resolved_sid = _resolve_to_parent(raw_sid)
            # Skip the current session lineage — the agent already has that
            # context, even if older turns live in parent fragments.
            if current_lineage_root and resolved_sid == current_lineage_root:
                continue
            if current_session_id and raw_sid == current_session_id:
                continue
            if resolved_sid not in seen_sessions:
                result = dict(result)
                result["session_id"] = resolved_sid
                seen_sessions[resolved_sid] = result
            if len(seen_sessions) >= limit:
                break

        if hybrid:
            semantic_results = []
            semantic_error = ""
            if semantic_search is not None:
                try:
                    semantic_results = semantic_search(query, n_results=max(limit * 3, 5)) or []
                except Exception as e:
                    semantic_error = str(e)
            elif mode == "semantic":
                semantic_error = "Semantic search provider is not available."

            candidates: Dict[str, Dict[str, Any]] = {}

            def _session_meta(session_id: str) -> Dict[str, Any]:
                if not session_id:
                    return {}
                try:
                    return db.get_session(session_id) or {}
                except Exception:
                    return {}

            for rank, (session_id, match_info) in enumerate(seen_sessions.items(), start=1):
                meta = _session_meta(session_id)
                candidates[session_id] = {
                    "session_id": session_id,
                    "when": _format_timestamp(match_info.get("session_started") or meta.get("started_at")),
                    "source": meta.get("source") or match_info.get("source") or "unknown",
                    "title": meta.get("title") or None,
                    "model": match_info.get("model") or meta.get("model"),
                    "score": max(0.0, 0.72 - (rank - 1) * 0.03),
                    "match_reasons": ["fts"],
                    "fts": {
                        "rank": rank,
                        "snippet": match_info.get("snippet") or "",
                        "matched_role": match_info.get("role"),
                        "matched_at": _format_timestamp(match_info.get("timestamp")),
                        "context": match_info.get("context") or [],
                    },
                }

            for rank, sem in enumerate(semantic_results, start=1):
                metadata = sem.get("metadata") or {}
                sid = metadata.get("session_id") or sem.get("session_id")
                if not sid:
                    sid = f"semantic_only_{rank}"
                resolved_sid = _resolve_to_parent(sid) if sid and not sid.startswith("semantic_only_") else sid
                if current_lineage_root and resolved_sid == current_lineage_root:
                    continue
                meta = _session_meta(resolved_sid) if not resolved_sid.startswith("semantic_only_") else {}
                similarity = sem.get("similarity")
                if similarity is None:
                    similarity = sem.get("score", 0.0)
                try:
                    similarity_float = float(similarity or 0.0)
                except (TypeError, ValueError):
                    similarity_float = 0.0
                sem_score = min(max(similarity_float, 0.0), 0.75)
                content = sem.get("content") or ""
                semantic_payload = {
                    "rank": rank,
                    "similarity": round(similarity_float, 4),
                    "score": sem.get("score"),
                    "content_preview": content[:500],
                    "metadata": metadata,
                }
                if resolved_sid in candidates:
                    candidates[resolved_sid]["semantic"] = semantic_payload
                    if "semantic" not in candidates[resolved_sid]["match_reasons"]:
                        candidates[resolved_sid]["match_reasons"].append("semantic")
                    candidates[resolved_sid]["score"] = min(1.0, max(candidates[resolved_sid]["score"], sem_score) + 0.20)
                else:
                    candidates[resolved_sid] = {
                        "session_id": None if resolved_sid.startswith("semantic_only_") else resolved_sid,
                        "when": _format_timestamp(meta.get("started_at")),
                        "source": meta.get("source") or "unknown",
                        "title": meta.get("title") or None,
                        "model": meta.get("model"),
                        "score": sem_score,
                        "match_reasons": ["semantic"],
                        "semantic": semantic_payload,
                    }

            merged = list(candidates.values())
            merged.sort(key=lambda item: item.get("score", 0.0), reverse=True)
            merged = merged[:limit]
            for item in merged:
                item["score"] = round(item.get("score", 0.0), 4)
            return json.dumps({
                "success": True,
                "mode": "hybrid" if mode == "hybrid" else "semantic",
                "query": query,
                "results": merged,
                "count": len(merged),
                "sessions_searched": len(seen_sessions),
                "semantic_searched": len(semantic_results),
                "degraded": bool(semantic_error),
                **({"semantic_error": semantic_error} if semantic_error else {}),
            }, ensure_ascii=False)

        if not summarize:
            fast_results = []
            for session_id, match_info in seen_sessions.items():
                try:
                    session_meta = db.get_session(session_id) or {}
                except Exception:
                    session_meta = {}
                context = match_info.get("context") or []
                fast_results.append({
                    "session_id": session_id,
                    "when": _format_timestamp(match_info.get("session_started") or session_meta.get("started_at")),
                    "source": session_meta.get("source") or match_info.get("source") or "unknown",
                    "title": session_meta.get("title") or None,
                    "model": match_info.get("model") or session_meta.get("model"),
                    "matched_role": match_info.get("role"),
                    "matched_at": _format_timestamp(match_info.get("timestamp")),
                    "snippet": match_info.get("snippet") or "",
                    "context": context,
                })
            return json.dumps({
                "success": True,
                "mode": "fast",
                "query": query,
                "results": fast_results,
                "count": len(fast_results),
                "sessions_searched": len(seen_sessions),
                "message": "Fast FTS match list returned without LLM summarization. Re-run with mode='summary' for a deeper recap of selected sessions.",
            }, ensure_ascii=False)

        # Prepare all sessions for parallel summarization
        tasks = []
        for session_id, match_info in seen_sessions.items():
            try:
                messages = db.get_messages_as_conversation(session_id)
                if not messages:
                    continue
                session_meta = db.get_session(session_id) or {}
                conversation_text = _format_conversation(messages)
                conversation_text = _truncate_around_matches(conversation_text, query)
                tasks.append((session_id, match_info, conversation_text, session_meta))
            except Exception as e:
                logging.warning(
                    "Failed to prepare session %s: %s",
                    session_id,
                    e,
                    exc_info=True,
                )

        # Summarize all sessions in parallel
        async def _summarize_all() -> List[Union[str, Exception]]:
            """Summarize all sessions with bounded concurrency."""
            max_concurrency = min(_get_session_search_max_concurrency(), max(1, len(tasks)))
            semaphore = asyncio.Semaphore(max_concurrency)

            async def _bounded_summary(text: str, meta: Dict[str, Any]) -> Optional[str]:
                async with semaphore:
                    return await _summarize_session(text, query, meta)

            coros = [
                _bounded_summary(text, meta)
                for _, _, text, meta in tasks
            ]
            return await asyncio.gather(*coros, return_exceptions=True)

        try:
            # Use _run_async() which properly manages event loops across
            # CLI, gateway, and worker-thread contexts.  The previous
            # pattern (asyncio.run() in a ThreadPoolExecutor) created a
            # disposable event loop that conflicted with cached
            # AsyncOpenAI/httpx clients bound to a different loop,
            # causing deadlocks in gateway mode (#2681).
            from model_tools import _run_async
            results = _run_async(_summarize_all())
        except concurrent.futures.TimeoutError:
            logging.warning(
                "Session summarization timed out after 60 seconds",
                exc_info=True,
            )
            return json.dumps({
                "success": False,
                "error": "Session summarization timed out. Try a more specific query or reduce the limit.",
            }, ensure_ascii=False)

        summaries = []
        for (session_id, match_info, conversation_text, session_meta), result in zip(tasks, results):
            if isinstance(result, Exception):
                logging.warning(
                    "Failed to summarize session %s: %s",
                    session_id, result, exc_info=True,
                )
                result = None

            # Prefer resolved parent session metadata over FTS5 match metadata.
            # match_info carries source/model from the *child* session that contained
            # the FTS5 hit; after _resolve_to_parent() the session_id points to the
            # root, so session_meta has the authoritative platform/source for the
            # session the user actually cares about (#15909).
            entry = {
                "session_id": session_id,
                "when": _format_timestamp(
                    session_meta.get("started_at") or match_info.get("session_started")
                ),
                "source": session_meta.get("source") or match_info.get("source", "unknown"),
                "model": session_meta.get("model") or match_info.get("model"),
            }

            if result:
                entry["summary"] = result
            else:
                # Fallback: raw preview so matched sessions aren't silently
                # dropped when the summarizer is unavailable (fixes #3409).
                preview = (conversation_text[:500] + "\n…[truncated]") if conversation_text else "No preview available."
                entry["summary"] = f"[Raw preview — summarization unavailable]\n{preview}"

            summaries.append(entry)

        return json.dumps({
            "success": True,
            "query": query,
            "results": summaries,
            "count": len(summaries),
            "sessions_searched": len(seen_sessions),
        }, ensure_ascii=False)

    except Exception as e:
        logging.error("Session search failed: %s", e, exc_info=True)
        return tool_error(f"Search failed: {str(e)}", success=False)


def check_session_search_requirements() -> bool:
    """Requires SQLite state database and an auxiliary text model."""
    try:
        from hermes_state import DEFAULT_DB_PATH
        return DEFAULT_DB_PATH.parent.exists()
    except ImportError:
        return False


SESSION_SEARCH_SCHEMA = {
    "name": "session_search",
    "description": (
        "Search your long-term memory of past conversations, or browse recent sessions. This is your recall -- "
        "every past session is searchable.\n\n"
        "THREE MODES:\n"
        "1. Recent sessions (no query): Call with no arguments to see what was worked on recently. "
        "Returns titles, previews, and timestamps. Zero LLM cost, instant. "
        "Start here when the user asks what were we working on or what did we do recently.\n"
        "2. Fast keyword search (default): Search specific topics across all past sessions with FTS5 only. "
        "Returns session IDs, dates, titles, snippets, and local context without any LLM summarization. "
        "Use this first when the user asks to find or resume a previous session.\n"
        "3. Hybrid search (mode='hybrid'): Combines SQLite FTS exact matches with ChromaDB session-history semantic matches, "
        "merges by session_id, and returns ranked evidence without LLM summarization. Use when exact terms are uncertain.\n"
        "4. Summary search (mode='summary'): After fast or hybrid search identifies the right candidate, request "
        "LLM-generated summaries of matching sessions. Slower but more detailed.\n\n"
        "USE THIS PROACTIVELY when:\n"
        "- The user says 'we did this before', 'remember when', 'last time', 'as I mentioned'\n"
        "- The user asks about a topic you worked on before but don't have in current context\n"
        "- The user references a project, person, or concept that seems familiar but isn't in memory\n"
        "- You want to check if you've solved a similar problem before\n"
        "- The user asks 'what did we do about X?' or 'how did we fix Y?'\n\n"
        "Don't hesitate to search when it is actually cross-session -- it's fast and cheap. "
        "Better to search and confirm than to guess or ask the user to repeat themselves.\n\n"
        "Search syntax: keywords joined with OR for broad recall (elevenlabs OR baseten OR funding), "
        "phrases for exact match (\"docker networking\"), boolean (python NOT java), prefix (deploy*). "
        "IMPORTANT: Use OR between keywords for best results — FTS5 defaults to AND which misses "
        "sessions that only mention some terms. If a broad OR query returns nothing, try individual "
        "keyword searches in parallel. Returns summaries of the top matching sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — keywords, phrases, or boolean expressions to find in past sessions. Omit this parameter entirely to browse recent sessions instead (returns titles, previews, timestamps with no LLM cost).",
            },
            "mode": {
                "type": "string",
                "enum": ["fast", "hybrid", "semantic", "summary"],
                "description": "Search mode. Default 'fast' returns FTS snippets and session metadata with no LLM call. Use 'hybrid' when exact terms are uncertain; it combines FTS with semantic session-history vector search. Use 'summary' only after fast/hybrid search when you need deeper LLM-generated recaps.",
                "default": "fast",
            },
            "role_filter": {
                "type": "string",
                "description": "Optional: only search messages from specific roles (comma-separated). E.g. 'user,assistant' to skip tool outputs.",
            },
            "limit": {
                "type": "integer",
                "description": "Max sessions to summarize (default: 3, max: 5).",
                "default": 3,
            },
        },
        "required": [],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="session_search",
    toolset="session_search",
    schema=SESSION_SEARCH_SCHEMA,
    handler=lambda args, **kw: session_search(
        query=args.get("query") or "",
        role_filter=args.get("role_filter"),
        limit=args.get("limit", 3),
        mode=args.get("mode", "fast"),
        db=kw.get("db"),
        current_session_id=kw.get("current_session_id"),
        semantic_search=kw.get("semantic_search")),
    check_fn=check_session_search_requirements,
    emoji="🔍",
)
