# Reconciliation: the resolver is asked for the hinted kind first; a miss with exactly
# one other kind hitting rewrites the kind; two kinds hitting asks (PLAN-chatbot-turn-
# rearch.md "APPLY contract", AC-1527). `resolved` is a plain dict, built by hand in
# tests - `{raw: {kind: hit_count, ...}, ...}` - the real resolver seam lands in S3.
from __future__ import annotations

from typing import Any

from app.services.chatbot.turn.state import token_key as _key


def hits_for_token(resolved: dict[str, dict[str, int]] | None, raw: Any) -> dict[str, int] | None:
    """The resolver's own `{kind: hit count}` for the token a customer typed.

    Hand pass 12 round 4, R7: the map (`turn_runtime.resolve_kinds`'s own `by_token`)
    is keyed by the token the RESOLVER echoes back, which is the FOLDED one -
    `resolve_gate.resolve_entity_body` sends every entity through its own `[-\\s]+`
    fold before the resolver sees it, so "ZZT-CONT-9F" comes back as "ZZTCONT9F"
    (`turn_runtime._token_key`'s own docstring, measured on turn bb921451). Looking it
    up by the raw text a customer typed therefore found NOTHING for any token carrying
    a dash or a space, so a mishinted dashed record key was never rewritten to its
    resolved kind at all - the same class of silent-join failure R1 fixed for
    `unresolved_tokens`, in the one place that still had it.

    Exact text first (a hand-built map, and the common no-separator case), then the
    folded join. `setdefault` on the folded index so the FIRST key wins rather than a
    later one clobbering it, for the rare pair of typed tokens that fold alike.
    """
    if not resolved or raw is None:
        return None
    exact = resolved.get(raw) if isinstance(raw, str) else None
    if exact:
        return exact
    key = _key(raw)
    if not key:
        return None
    folded: dict[str, dict[str, int]] = {}
    for token, hits in resolved.items():
        folded.setdefault(_key(token), hits)
    return folded.get(key)


class ReconcileResult:
    def __init__(self) -> None:
        self.entities: list[dict[str, Any]] = []
        self.reconciled: list[tuple[str, str, str]] = []
        self.kind_pick_options: list[dict[str, Any]] | None = None
        # #1262 slice 8 (F1b siblings), owner ruling 6: "several ambiguous tokens
        # are asked one pick at a time, first token first" - the FIRST ambiguous
        # token's options are `kind_pick_options` (unchanged shape, so a caller with
        # only one ambiguous token never sees a new field); every SUBSEQUENT one's
        # options queue here instead of overwriting the first, so a later token is
        # asked in turn rather than silently lost.
        self.queued_kind_picks: list[list[dict[str, Any]]] = []


def apply_reconciliation(
    entities: list[dict[str, Any]], resolved: dict[str, dict[str, int]] | None
) -> ReconcileResult:
    result = ReconcileResult()
    if not resolved:
        result.entities = list(entities)
        return result

    for e in entities:
        raw = e.get("raw")
        hits = hits_for_token(resolved, raw)
        if not hits:
            result.entities.append(e)
            continue
        matched = [k for k, count in hits.items() if count and count > 0]
        # #1262 slice 7 (F1b): the resolver's own hit-dict order is an accident of
        # however its callers happened to run, not a contract - the SAME ambiguity
        # (customer/transporter on "Sorento") armed a DIFFERENT numbered pick turn
        # to turn depending on which resolver ran first. Fix lane round 2, N1: the
        # stated rule is the resolver's own hit strength - the kind with the most
        # matches first, ties alphabetical - so the same tie always numbers its
        # options the same way without a priority tuned to one transcript.
        if len(matched) > 1:
            matched = sorted(matched, key=lambda k: (-hits[k], k))
        if len(matched) == 0:
            result.entities.append(e)
        elif len(matched) == 1:
            new_kind = matched[0]
            old_kind = e.get("hint")
            if new_kind != old_kind:
                result.reconciled.append((raw, old_kind, new_kind))
                rewritten = dict(e)
                rewritten["hint"] = new_kind
                result.entities.append(rewritten)
            else:
                result.entities.append(e)
        else:
            options = [
                {
                    "position": i + 1,
                    "label": f"{raw} ({k.replace('_', ' ')})",
                    # #1262 slice 7 (F1b): the label above is display only - `apply.py`'s
                    # pick arm falls back to it when neither `code` nor a uuid is on the
                    # option, which is how the PRINTED "Sorento (customer)" ended up
                    # stored as the customer's `raw`/`canonical_code`. Carrying the raw
                    # token here is what the pick arm resolves the entity from instead.
                    "raw": raw,
                    "code": raw,
                    "uuid": None,
                    "uuids": [],
                    "entity_type": k,
                    "payload": {"kind": k},
                }
                for i, k in enumerate(matched)
            ]
            if result.kind_pick_options is None:
                result.kind_pick_options = options
            else:
                result.queued_kind_picks.append(options)
            result.entities.append(e)

    return result
