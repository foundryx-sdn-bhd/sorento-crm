"""SCM M5 Part B - market research service (advisory-only web-search signals).

Two halves, with a hard line between them:

- **Fully real + verifiable (no external key needed):** topic CRUD, signal reads
  (joined to their topic for the human label), and the ``market_research_run``
  observability row. These persist and serialize deterministically.
- **Key-gated (Anthropic web search):** ``_web_search_topic`` is the ONLY part
  that reaches the network. Without ``ANTHROPIC_API_KEY`` it returns ``[]`` and
  ``run_research`` records a ``status='failed'`` run with a clear error - never a
  crash. Tests monkeypatch ``_web_search_topic`` to inject synthetic signals and
  exercise the whole persistence path with no network.

LLM boundary (AC-M5.8): this service writes ONLY the ``scm.market_signal`` table
(+ its run log). It NEVER writes a numeric field on ``reorder_recommendation`` - 
signals are their own advisory-only table; the deterministic engine never reads
them.

TODO (M5 fast-follow): a ``scheduled_task`` (``scm_market_research``) should call
``run_research`` on each topic's cadence. Kept synchronous + manual for now.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.scm import MarketResearchRun, MarketResearchTopic, MarketSignal
from app.schemas.scm_market import MarketResearchTopicWrite
from app.services.error_handler import AppException
from app.services.scm import explainer_service

logger = logging.getLogger(__name__)

# The honest degrade message surfaced on the run row when no web-search key is set.
NO_KEY_ERROR = "Anthropic web-search not configured (set ANTHROPIC_API_KEY)"

# Model + web-search server-tool verified live 2026-07-17 (Haiku 4.5 executed a real
# `web_search_20250305` call and returned cited 2026 trend text). Re-confirm the tool
# version string against current Anthropic docs on any major SDK bump.
_ANTHROPIC_MODEL = "claude-haiku-4-5"
_WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
_SEARCH_MAX_TOKENS = 1024
_EXTRACT_MAX_TOKENS = 400
_VALID_TRENDS = {"up", "down", "flat"}


# ---------------------------------------------------------------------------
# key access (single choke-point so tests + the endpoint agree)
# ---------------------------------------------------------------------------

def _anthropic_api_key(db: Session) -> Optional[str]:
    """Anthropic key for the market web search - DB-configured (assistant config,
    same place the OpenAI key lives), falling back to the env only if the DB is
    empty. NOT an env-only setting (user preference)."""
    from app.models.ai_assistant import AIAssistantConfig

    row = (
        db.query(AIAssistantConfig)
        .order_by(AIAssistantConfig.created_at.asc())
        .first()
    )
    if row and row.anthropic_api_key_ciphertext:
        return row.anthropic_api_key_ciphertext
    return getattr(settings, "anthropic_api_key", None) or None


# ---------------------------------------------------------------------------
# serializers (no UUID in any display field - topic_label, not topic_id)
# ---------------------------------------------------------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _topic_out(t: MarketResearchTopic) -> dict:
    return {
        "id": t.id,
        "label": t.label,
        "category_ref": t.category_ref,
        "currency": t.currency,
        "search_prompt": t.search_prompt or "",
        "cadence": t.cadence or "manual",
        "is_active": bool(t.is_active),
    }


def _signal_out(s: MarketSignal, topic_label: str) -> dict:
    return {
        "id": s.id,
        "topic_label": topic_label,
        "category_ref": s.category_ref,
        "currency": s.currency,
        "value": float(s.value) if s.value is not None else None,
        "trend": s.trend,
        "summary": s.summary or "",
        "source_url": s.source_url,
        "captured_at": _iso(s.captured_at) or _iso(s.created_at) or "",
    }


def _run_out(r: MarketResearchRun) -> dict:
    return {
        "id": r.id,
        "status": r.status,
        "started_at": _iso(r.started_at),
        "finished_at": _iso(r.finished_at),
        "topic_count": int(r.topic_count or 0),
        "signal_count": int(r.signal_count or 0),
        "error": r.error_text,
    }


# ---------------------------------------------------------------------------
# topic CRUD (fully real)
# ---------------------------------------------------------------------------

def list_topics(db: Session) -> list[dict]:
    rows = (
        db.query(MarketResearchTopic)
        .order_by(MarketResearchTopic.created_at.desc())
        .all()
    )
    return [_topic_out(t) for t in rows]


def _get_topic(db: Session, topic_id: str) -> MarketResearchTopic:
    t = (
        db.query(MarketResearchTopic)
        .filter(MarketResearchTopic.id == topic_id)
        .first()
    )
    if not t:
        raise AppException(status_code=404, message="Research topic not found.")
    return t


def create_topic(db: Session, body: MarketResearchTopicWrite) -> dict:
    t = MarketResearchTopic(
        label=body.label,
        category_ref=body.category_ref,
        currency=body.currency,
        search_prompt=body.search_prompt or "",
        cadence=body.cadence or "manual",
        is_active=body.is_active,
        source_system="manual",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _topic_out(t)


def update_topic(db: Session, topic_id: str, body: MarketResearchTopicWrite) -> dict:
    t = _get_topic(db, topic_id)
    t.label = body.label
    t.category_ref = body.category_ref
    t.currency = body.currency
    t.search_prompt = body.search_prompt or ""
    t.cadence = body.cadence or "manual"
    t.is_active = body.is_active
    db.commit()
    db.refresh(t)
    return _topic_out(t)


def delete_topic(db: Session, topic_id: str) -> None:
    t = _get_topic(db, topic_id)
    db.delete(t)  # hard delete; signals cascade via FK ON DELETE CASCADE
    db.commit()


# ---------------------------------------------------------------------------
# signal reads (fully real)
# ---------------------------------------------------------------------------

def list_signals(db: Session) -> list[dict]:
    """All cached signals, newest-captured first, each carrying its topic's human
    label (LEFT JOIN so a signal outlives nothing but degrades to a placeholder
    label if the topic FK is somehow null)."""
    rows = (
        db.query(MarketSignal, MarketResearchTopic.label)
        .outerjoin(
            MarketResearchTopic, MarketResearchTopic.id == MarketSignal.topic_id
        )
        .order_by(
            MarketSignal.captured_at.desc().nullslast(),
            MarketSignal.created_at.desc(),
        )
        .all()
    )
    return [_signal_out(s, label or "-") for s, label in rows]


def get_run(db: Session, run_id: str) -> dict:
    r = db.query(MarketResearchRun).filter(MarketResearchRun.id == run_id).first()
    if not r:
        raise AppException(status_code=404, message="Research run not found.")
    return _run_out(r)


# ---------------------------------------------------------------------------
# research run (persistence real; web search key-gated)
# ---------------------------------------------------------------------------

def run_research(db: Session, actor: Optional[str] = None) -> dict:
    """Create a run, search each ACTIVE topic, persist extracted signals, and log
    the run. No Anthropic key → a valid ``status='failed'`` run row (0 signals) for
    observability, never a crash. Per-topic failures are trapped so one bad topic
    doesn't sink the whole run (they're appended to the run's error text)."""
    started = datetime.utcnow()
    run = MarketResearchRun(status="running", started_at=started, source_system="manual")
    db.add(run)
    db.flush()  # assign run.id

    topics = (
        db.query(MarketResearchTopic)
        .filter(MarketResearchTopic.is_active.is_(True))
        .all()
    )
    run.topic_count = len(topics)

    if not _anthropic_api_key(db):
        run.status = "failed"
        run.error_text = NO_KEY_ERROR
        run.signal_count = 0
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        return _run_out(run)

    signal_count = 0
    errors: list[str] = []
    for topic in topics:
        try:
            extracted = _web_search_topic(db, topic)
        except Exception as exc:  # one bad topic must not sink the run
            logger.exception("market research: topic %s failed", topic.id)
            errors.append(f"{topic.label}: {exc}")
            continue
        for item in extracted or []:
            summary = (item.get("summary") or "").strip()
            if not summary:
                continue  # a signal with no readable summary is not worth caching
            trend = item.get("trend")
            if trend not in _VALID_TRENDS:
                trend = None
            db.add(
                MarketSignal(
                    topic_id=topic.id,
                    category_ref=topic.category_ref,
                    currency=topic.currency,
                    value=_coerce_num(item.get("value")),
                    trend=trend,
                    summary=summary,
                    source_url=item.get("source_url"),
                    sources=item.get("sources"),
                    captured_at=datetime.utcnow(),
                    source_system="web_search",
                )
            )
            signal_count += 1

    run.signal_count = signal_count
    # failed = every topic errored and nothing landed; otherwise completed (any
    # per-topic errors are still logged on the run's error_text for observability).
    run.status = "failed" if (errors and signal_count == 0) else "completed"
    run.finished_at = datetime.utcnow()
    if errors:
        run.error_text = "; ".join(errors)[:2000]
    db.commit()
    db.refresh(run)
    return _run_out(run)


def search_adhoc(
    db: Session,
    query: str,
    category_ref: Optional[str] = None,
    currency: Optional[str] = None,
    actor: Optional[str] = None,
) -> dict:
    """One-off market web search fired from the planning flow (AC-M6.8). Runs the
    Anthropic web search for a free-text ``query``, caches 0+ extracted signals under
    a reuse-or-create ad-hoc topic (``is_active=False`` so scheduled runs skip it),
    logs a run, and returns the new signals + the run row. No key → an honest
    ``status='failed'`` run (0 signals), never a crash. Advisory-only: writes ONLY
    the signal table, never a recommendation field (AC-M6.12)."""
    query = (query or "").strip()
    if not query:
        raise AppException(status_code=422, message="A search query is required.")
    label = query[:200]
    started = datetime.utcnow()
    run = MarketResearchRun(status="running", started_at=started, source_system="adhoc")
    db.add(run)
    db.flush()  # assign run.id
    run.topic_count = 1

    if not _anthropic_api_key(db):
        run.status = "failed"
        run.error_text = NO_KEY_ERROR
        run.signal_count = 0
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        return {"signals": [], "run": _run_out(run)}

    topic = (
        db.query(MarketResearchTopic)
        .filter(
            MarketResearchTopic.source_system == "adhoc",
            MarketResearchTopic.label == label,
            MarketResearchTopic.category_ref == category_ref,
        )
        .first()
    )
    if topic is None:
        topic = MarketResearchTopic(
            label=label,
            category_ref=category_ref,
            currency=currency,
            search_prompt=query,
            cadence="manual",
            is_active=False,  # ad-hoc: never picked up by a scheduled/topic sweep
            source_system="adhoc",
        )
        db.add(topic)
        db.flush()
    else:
        topic.currency = currency
        topic.search_prompt = query

    try:
        extracted = _web_search_topic(db, topic)
    except Exception as exc:  # network/SDK failure → an honest failed run, not a 500
        logger.exception("ad-hoc market search failed: %s", query)
        run.status = "failed"
        run.error_text = str(exc)[:2000]
        run.signal_count = 0
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        return {"signals": [], "run": _run_out(run)}

    new_signals: list[MarketSignal] = []
    for item in extracted or []:
        summary = (item.get("summary") or "").strip()
        if not summary:
            continue
        trend = item.get("trend")
        if trend not in _VALID_TRENDS:
            trend = None
        sig = MarketSignal(
            topic_id=topic.id,
            category_ref=topic.category_ref,
            currency=topic.currency,
            value=_coerce_num(item.get("value")),
            trend=trend,
            summary=summary,
            source_url=item.get("source_url"),
            sources=item.get("sources"),
            captured_at=datetime.utcnow(),
            source_system="web_search",
        )
        db.add(sig)
        db.flush()
        new_signals.append(sig)

    run.signal_count = len(new_signals)
    run.status = "completed"  # a search that found nothing is still a completed search
    run.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return {
        "signals": [_signal_out(s, topic.label) for s in new_signals],
        "run": _run_out(run),
    }


def _coerce_num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# KEY-GATED: Anthropic web search + schema-forced extraction (isolated so tests
# monkeypatch this one function)
# ---------------------------------------------------------------------------

def _web_search_topic(db: Session, topic: MarketResearchTopic) -> list[dict]:
    """Search one topic and return 0+ extracted signal dicts
    ``{value, trend, summary, source_url}``.

    KEY-GATED: returns ``[]`` when no Anthropic key is configured. Tests monkeypatch
    this whole function; the rest of ``run_research`` is exercised without network.
    """
    key = _anthropic_api_key(db)
    if not key:
        return []
    search_text, sources = _anthropic_web_search(topic.search_prompt or topic.label, key)
    if not search_text:
        return []
    signals = _extract_signals(db, topic, search_text)
    # Attach the harvested citation list to EVERY signal from this search (they share the
    # same reading). Merge in the LLM-extracted primary source_url if it isn't already
    # listed, so the card always has at least that one link (M8-F: prove it is factual).
    for item in signals:
        merged = list(sources)
        primary = (item.get("source_url") or "").strip()
        if primary and not any(s.get("url") == primary for s in merged):
            merged.insert(0, {"url": primary, "title": None})
        item["sources"] = merged or None
    return signals


def _anthropic_web_search(prompt: str, api_key: str) -> tuple[str, list[dict]]:
    """Raw Anthropic SDK call with the web-search SERVER tool. Returns the model's
    concatenated text output (which cites the figures/URLs it found) AND the list of
    citation sources ``[{url, title}]`` harvested from the response - both the text
    blocks' inline citations and the web_search_tool_result blocks - deduped by url.

    ⚠ The tool version string + model id are pinned to authoring-time values and MUST
    be re-confirmed against current Anthropic docs before enabling in prod.
    """
    import anthropic  # local import: optional dep, only needed on the key-gated path

    from app.services import llm_cassette

    llm_cassette.refuse_uncovered("anthropic")
    client = anthropic.Anthropic(api_key=api_key)
    instruction = (
        "\n\nUsing web search, report the single most relevant current figure for the "
        "above (a price, index level, %, or rate), whether it is trending up, down or "
        "flat, a one-line plain-language summary a supply-chain planner can act on, and "
        "the source URL you took the figure from. Cite several sources where possible. "
        "Be concise."
    )
    resp = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=_SEARCH_MAX_TOKENS,
        tools=[_WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": f"{prompt}{instruction}"}],
    )
    parts: list[str] = []
    sources: list[dict] = []
    seen: set[str] = set()

    def _add(url, title) -> None:
        u = (url or "").strip()
        if not u or u in seen:
            return
        seen.add(u)
        sources.append({"url": u, "title": (title or None)})

    for block in getattr(resp, "content", None) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(getattr(block, "text", "") or "")
            # inline citations on the text block point at the sources it used
            for cit in getattr(block, "citations", None) or []:
                _add(getattr(cit, "url", None), getattr(cit, "title", None))
        elif btype == "web_search_tool_result":
            # the raw result set the model searched over
            content = getattr(block, "content", None) or []
            for item in content:
                _add(getattr(item, "url", None), getattr(item, "title", None))
    return "".join(parts).strip(), sources


_EXTRACT_SYSTEM = (
    "You extract STRUCTURED market signals from web-search result text for a supply-"
    "chain tool. Return ONLY JSON of the form "
    '{"signals": [{"value": <number|null>, "trend": "up"|"down"|"flat"|null, '
    '"summary": <one-line string>, "source_url": <url|null>}]}. '
    "Use only figures that appear in the given text - never invent or compute a number. "
    "If the text has no usable figure, still emit a signal with value=null and a "
    "qualitative summary. Output at most 2 signals."
)


def _extract_signals(db: Session, topic: MarketResearchTopic, search_text: str) -> list[dict]:
    """Second pass: schema-forced JSON extraction from the web-search prose. Uses the
    shared assistant provider (OpenAI is fine for extraction). Never fabricates a
    number - it only lifts figures already present in ``search_text``."""
    provider, model = explainer_service._provider_and_model(db)
    if provider is None:
        return []
    user_block = (
        "Web-search result text to extract from:\n"
        f"{search_text}\n\n"
        "Emit the signals JSON now."
    )
    result = provider.chat(
        [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": user_block},
        ],
        temperature=0.0,
        model=model,
        max_tokens=_EXTRACT_MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    raw = (result.content or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    signals = parsed.get("signals") if isinstance(parsed, dict) else None
    if not isinstance(signals, list):
        # Tolerate a bare object as a single signal.
        signals = [parsed] if isinstance(parsed, dict) else []
    return [s for s in signals if isinstance(s, dict)]
