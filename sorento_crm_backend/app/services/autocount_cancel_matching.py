"""Order-independent matching of key-less AutoCount cancel lines (AC-CANCEL-CRM, R2 steps 4-6).

Pure: no database, so the ingest and `scripts/autocount_cancel_match_report.py` (which measures
the owner's 2026 numbers from an extract) run the SAME rules. The answer is a function of the
whole group (one document + one item) and never of the order lines arrived in (owner, Q2 a).

For each cancel line, its candidates are the group's lines, narrowed by Location when that leaves
any. Then:
1. One candidate: matched (`item`).
2. Several (Q2 a): a candidate qualifies when its unexplained transfer (TransferedQty minus DO /
   GRN qty linked to it minus cancel qty matched to it by line key; the caller computes it) is at
   least the cancel qty. Exactly one qualifier is a tentative pick. Picks that together exceed a
   candidate's unexplained transfer do not stand. Standing picks: matched (`tie_break`).
3. Fallback (Q3, no person involved) for the rest, cancel lines in (DocDate, DocNo, DtlKey) order:
   the lowest-Seq candidate whose REMAINING unexplained transfer takes the whole qty: matched
   (`fallback`). None can: unmatched (`no_fit`). Never a split, never above what a line explains.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

REASON_ITEM = "item"
REASON_TIE_BREAK = "tie_break"
REASON_FALLBACK = "fallback"
REASON_NO_FIT = "no_fit"
REASON_ITEM_NOT_ON_ORDER = "item_not_on_order"


@dataclass(frozen=True)
class CancelIn:
    id: str
    qty: Decimal
    location: Optional[str]
    order: tuple  # (doc_date, doc_no, dtl_key): the fallback's fixed order


@dataclass(frozen=True)
class CandidateIn:
    id: str
    seq: tuple  # lowest first
    location: Optional[str]
    unexplained: Decimal


@dataclass(frozen=True)
class Outcome:
    target_id: Optional[str]
    reason: str
    candidates: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return self.target_id is not None


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _candidates_for(cancel: CancelIn, candidates: list[CandidateIn]) -> list[CandidateIn]:
    if len(candidates) > 1 and cancel.location:
        narrowed = [c for c in candidates if _norm(c.location) == _norm(cancel.location)]
        if narrowed:
            return narrowed
    return candidates


def resolve_group(cancels: list[CancelIn], candidates: list[CandidateIn]) -> dict[str, Outcome]:
    """Every cancel line of one group, matched or not, with the reason."""
    ordered_candidates = sorted(candidates, key=lambda c: (c.seq, c.id))
    out: dict[str, Outcome] = {}
    sets: dict[str, list[CandidateIn]] = {}
    for cancel in cancels:
        cset = _candidates_for(cancel, ordered_candidates)
        sets[cancel.id] = cset
        ids = tuple(c.id for c in cset)
        if not cset:
            out[cancel.id] = Outcome(None, REASON_ITEM_NOT_ON_ORDER, ())
        elif len(cset) == 1:
            out[cancel.id] = Outcome(cset[0].id, REASON_ITEM, ids)

    # Q2 a: tentative single-qualifier picks, then drop every pick on an oversubscribed line.
    tentative: dict[str, str] = {}
    for cancel in cancels:
        if cancel.id in out:
            continue
        qualifiers = [c for c in sets[cancel.id] if c.unexplained >= cancel.qty]
        if len(qualifiers) == 1:
            tentative[cancel.id] = qualifiers[0].id
    by_cancel = {c.id: c for c in cancels}
    unexplained = {c.id: c.unexplained for c in candidates}
    load: dict[str, Decimal] = {}
    for cancel_id, target in tentative.items():
        load[target] = load.get(target, Decimal("0")) + by_cancel[cancel_id].qty
    for cancel_id, target in tentative.items():
        if load[target] <= unexplained[target]:
            out[cancel_id] = Outcome(target, REASON_TIE_BREAK,
                                     tuple(c.id for c in sets[cancel_id]))

    # Remaining capacity after every standing pick (single-candidate picks included).
    remaining = dict(unexplained)
    for cancel_id, outcome in out.items():
        if outcome.matched:
            remaining[outcome.target_id] -= by_cancel[cancel_id].qty

    # Q3 fallback, in a fixed order so the result is the same whatever arrived first.
    for cancel in sorted((c for c in cancels if c.id not in out), key=lambda c: (c.order, c.id)):
        cset = sets[cancel.id]
        pick = next((c for c in cset if remaining[c.id] >= cancel.qty), None)
        ids = tuple(c.id for c in cset)
        if pick is None:
            out[cancel.id] = Outcome(None, REASON_NO_FIT, ids)
        else:
            remaining[pick.id] -= cancel.qty
            out[cancel.id] = Outcome(pick.id, REASON_FALLBACK, ids)
    return out


def as_decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))
