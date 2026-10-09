"""The topic audit: a sample of labelled asks a person judges (chat analytics CA-2).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Topic audit".

**The draw** takes `SAMPLE_SIZE` asks labelled under one taxonomy version: at least
`MIN_PER_SUBJECT` of every subject that has asks (all of them when it has fewer), the rest in
proportion to each subject's volume, half of that rest from the lowest label confidence and
half at random. Fewer labelled asks than the sample size: all of them.

**The customer's words are read, never stored.** `rows` pulls the message off
`chatbot.turns.envelope` for each audit row at request time; the audit table has no column
for it.

**Rates cover the whole sample**, whatever filter the list shows. Subject right is
(right + wrong_topic) / judged, topic right is right / judged, product right is right / rows
with a products verdict; each is None while nothing is judged.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.chat_facts import ChatAskFact, ChatAskItem, ChatLabelAudit, ChatTopic
from app.models.chatbot_turn import ChatbotTurn
from app.models.product import Product
from app.models.user import User
from app.schemas.chat_topic_audit import AuditResponse
from app.services.chatbot.analytics.labeller import _message_text
from app.services.error_handler import AppException

SAMPLE_SIZE = 300
MIN_PER_SUBJECT = 10
TARGETS = {"subject": 0.9, "topic": 0.8, "product": 0.95}
SHOWS = ("to_review", "reviewed", "all")

_FACTS = ChatAskFact.__table__
_ITEMS = ChatAskItem.__table__
_TOPICS = ChatTopic.__table__
_PRODUCTS = Product.__table__
_USERS = User.__table__

#: A fact with no domain is the `general` subject (the report's rule too).
_SUBJECT = sa.func.coalesce(ChatAskFact.domain, sa.literal("general"))


def current_version(db: Session) -> int:
    """The highest version of an active taxonomy row (0 when there is no taxonomy)."""
    return int(db.query(sa.func.max(ChatTopic.version)).filter(ChatTopic.status == "active").scalar() or 0)


def versions(db: Session) -> list[int]:
    """Every taxonomy version, plus any a sample was drawn for, newest first."""
    found = {v for (v,) in db.query(ChatTopic.version).distinct()}
    found |= {v for (v,) in db.query(ChatLabelAudit.taxonomy_version).distinct()}
    return sorted(found, reverse=True)


# ------------------------------------------------------------------------------ draw


def _allocate(pools: dict[str, int], volume: dict[str, int], rest: int) -> dict[str, int]:
    """Split `rest` over the subjects by volume, never past what a subject has left."""
    quota = {subject: 0 for subject in pools}
    left = rest
    while left > 0:
        open_ = [s for s in pools if quota[s] < pools[s]]
        if not open_:
            break
        weight = sum(volume[s] for s in open_)
        shares = {s: left * volume[s] / weight for s in open_}
        gave = 0
        for subject in open_:
            add = min(int(shares[subject]), pools[subject] - quota[subject])
            quota[subject] += add
            gave += add
        if gave == 0:
            for subject in sorted(open_, key=lambda s: shares[s] - int(shares[s]), reverse=True)[:left]:
                quota[subject] += 1
                gave += 1
        left -= gave
    return quota


def choose(candidates: list[tuple[str, str, Optional[float]]], rng: random.Random) -> list[str]:
    """The ask ids to audit, from (id, subject, confidence) triples."""
    if len(candidates) <= SAMPLE_SIZE:
        return [c[0] for c in candidates]
    by_subject: dict[str, list[tuple[str, Optional[float]]]] = {}
    for ask_id, subject, confidence in candidates:
        by_subject.setdefault(subject, []).append((ask_id, confidence))
    picked: list[str] = []
    pools: dict[str, list[tuple[str, Optional[float]]]] = {}
    for subject, rows in by_subject.items():
        rng.shuffle(rows)
        picked += [r[0] for r in rows[:MIN_PER_SUBJECT]]
        pools[subject] = rows[MIN_PER_SUBJECT:]
    quota = _allocate(
        {s: len(p) for s, p in pools.items()},
        {s: len(r) for s, r in by_subject.items()},
        max(0, SAMPLE_SIZE - len(picked)),
    )
    for subject, pool in pools.items():
        n = quota[subject]
        if not n:
            continue
        # An unscored ask sorts last: it is no evidence of a weak label.
        pool.sort(key=lambda r: (r[1] is None, r[1] if r[1] is not None else 0.0))
        low = pool[: n // 2]
        rest = pool[n // 2 :]
        rng.shuffle(rest)
        picked += [r[0] for r in low] + [r[0] for r in rest[: n - len(low)]]
    return picked


def draw(db: Session, version: int, *, rng: Optional[random.Random] = None) -> int:
    """Write the sample for `version`; 409 when one exists. Returns how many were drawn."""
    taken = db.query(ChatLabelAudit.id).filter(ChatLabelAudit.taxonomy_version == version).first()
    if taken is not None:
        raise AppException(
            status_code=409,
            message="A sample was already drawn for that taxonomy version.",
            code="AUDIT_SAMPLE_EXISTS",
        )
    candidates = [
        (str(ask_id), subject, confidence)
        for ask_id, subject, confidence in db.query(ChatAskFact.id, _SUBJECT, ChatAskFact.label_confidence).filter(
            ChatAskFact.topic_code.isnot(None), ChatAskFact.taxonomy_version == version
        )
    ]
    ids = choose(candidates, rng or random.Random())
    db.add_all([ChatLabelAudit(ask_id=ask_id, taxonomy_version=version) for ask_id in ids])
    db.commit()
    return len(ids)


# ------------------------------------------------------------------------------ read


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return numerator / denominator if denominator else None


def _rates(db: Session, version: int) -> tuple[int, int, dict[str, Optional[float]]]:
    counts = dict(
        db.query(ChatLabelAudit.verdict, sa.func.count())
        .filter(ChatLabelAudit.taxonomy_version == version, ChatLabelAudit.verdict.isnot(None))
        .group_by(ChatLabelAudit.verdict)
        .all()
    )
    judged = sum(counts.values())
    right = counts.get("right", 0)
    products = dict(
        db.query(ChatLabelAudit.products_verdict, sa.func.count())
        .filter(ChatLabelAudit.taxonomy_version == version, ChatLabelAudit.products_verdict.isnot(None))
        .group_by(ChatLabelAudit.products_verdict)
        .all()
    )
    sample = db.query(sa.func.count()).select_from(ChatLabelAudit).filter(
        ChatLabelAudit.taxonomy_version == version
    ).scalar()
    return int(sample), judged, {
        "subject": _rate(right + counts.get("wrong_topic", 0), judged),
        "topic": _rate(right, judged),
        "product": _rate(products.get("right", 0), sum(products.values())),
    }


def _labels(db: Session, codes: set[str]) -> dict[str, str]:
    if not codes:
        return {}
    return dict(db.query(ChatTopic.code, ChatTopic.label).filter(ChatTopic.code.in_(codes)).all())


def _product_codes(db: Session, ask_ids: list[str]) -> dict[str, list[str]]:
    if not ask_ids:
        return {}
    out: dict[str, set[str]] = {}
    for ask_id, code in db.execute(
        sa.select(_ITEMS.c.ask_id, _PRODUCTS.c.product_code)
        .select_from(_ITEMS.join(_PRODUCTS, sa.cast(_PRODUCTS.c.id, sa.Text) == _ITEMS.c.ref_id))
        .where(_ITEMS.c.ask_id.in_(ask_ids), _ITEMS.c.kind == "product", _ITEMS.c.role.in_(("named", "asked")))
    ):
        out.setdefault(str(ask_id), set()).add(code)
    return {k: sorted(v) for k, v in out.items()}


def _names(db: Session, user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    return {
        str(uid): name
        for uid, name in db.execute(sa.select(_USERS.c.id, _USERS.c.name).where(_USERS.c.id.in_(user_ids)))
    }


def _rows(db: Session, audits: list[tuple[ChatLabelAudit, ChatAskFact, Optional[ChatbotTurn]]]) -> list[dict[str, Any]]:
    codes: set[str] = set()
    for audit, fact, _turn in audits:
        codes |= {fact.domain or "general", fact.topic_code or "", audit.corrected_topic or ""}
    labels = _labels(db, {c for c in codes if c})
    products = _product_codes(db, [str(a.ask_id) for a, _f, _t in audits])
    names = _names(db, {a.reviewed_by for a, _f, _t in audits if a.reviewed_by})
    rows = []
    for audit, fact, turn in audits:
        subject = fact.domain or "general"
        rows.append(
            {
                "id": str(audit.id),
                "asked_at": fact.asked_at,
                "message": _message_text(turn) if turn is not None else "",
                "subject": subject,
                "subject_label": labels.get(subject, subject),
                "topic": fact.topic_code or "",
                "topic_label": labels.get(fact.topic_code or "", fact.topic_code or ""),
                "confidence": fact.label_confidence,
                "products": products.get(str(audit.ask_id), []),
                "verdict": audit.verdict,
                "corrected_topic": audit.corrected_topic,
                "corrected_topic_label": labels.get(audit.corrected_topic) if audit.corrected_topic else None,
                "products_verdict": audit.products_verdict,
                "reviewed_by_name": names.get(audit.reviewed_by) if audit.reviewed_by else None,
            }
        )
    return rows


def _query(db: Session):
    return (
        db.query(ChatLabelAudit, ChatAskFact, ChatbotTurn)
        .join(ChatAskFact, ChatAskFact.id == ChatLabelAudit.ask_id)
        .outerjoin(ChatbotTurn, ChatbotTurn.id == ChatAskFact.turn_id)
    )


def listing(db: Session, *, version: Optional[int], show: str, subject: Optional[str]) -> AuditResponse:
    chosen = version if version is not None else current_version(db)
    query = _query(db).filter(ChatLabelAudit.taxonomy_version == chosen)
    if show == "to_review":
        query = query.filter(ChatLabelAudit.verdict.is_(None))
    elif show == "reviewed":
        query = query.filter(ChatLabelAudit.verdict.isnot(None))
    if subject:
        query = query.filter(_SUBJECT == subject)
    audits = query.order_by(ChatAskFact.asked_at.desc(), ChatLabelAudit.id.asc()).all()
    sample_size, reviewed, rates = _rates(db, chosen)
    return AuditResponse(
        version=chosen,
        versions=versions(db),
        sample_size=sample_size,
        reviewed=reviewed,
        rates=rates,
        targets=TARGETS,
        rows=_rows(db, audits),
    )


# --------------------------------------------------------------------------- verdict


def _unprocessable(message: str) -> AppException:
    return AppException(status_code=422, message=message, code="AUDIT_VERDICT_INVALID")


def judge(
    db: Session,
    audit_id: str,
    *,
    verdict: str,
    corrected_topic: Optional[str],
    products_verdict: Optional[str],
    reviewer_id: str,
) -> dict[str, Any]:
    found = _query(db).filter(ChatLabelAudit.id == audit_id).first()
    if found is None:
        raise AppException(status_code=404, message="That audit row does not exist.", code="NOT_FOUND")
    audit, fact, _turn = found
    corrected: Optional[str] = None
    if verdict in ("wrong_topic", "wrong_subject"):
        leaf = db.query(ChatTopic).filter(ChatTopic.code == corrected_topic).first() if corrected_topic else None
        if leaf is None or leaf.parent_code is None or leaf.status != "active":
            raise _unprocessable("Choose the topic this ask belongs to.")
        if verdict == "wrong_topic" and leaf.parent_code != (fact.domain or "general"):
            raise _unprocessable("Choose a topic under the ask's own subject.")
        corrected = leaf.code
    audit.verdict = verdict
    audit.corrected_topic = corrected
    if products_verdict is not None:
        audit.products_verdict = products_verdict
    audit.reviewed_by = str(reviewer_id)
    audit.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return _rows(db, [_query(db).filter(ChatLabelAudit.id == audit_id).one()])[0]
