"""After the tier pick on a SUBJECT ask ("kitchen sink promo"), the reply presents the
subject-filtered rows. The subject word is never name-matched against promotion names
(no 'Couldn't pin down ... closest matches' did-you-mean). Fake names only."""
from __future__ import annotations

import json

import pytest

from tests.chatbot.test_promo_subject_filter import _ask_qf, _pick_qf, _say, _seed

SINK_NAMES_WITHOUT_THE_WORDS = ["ZZT COMBINE PROMO DEALER", "ZZT HONEYCOMB OFFER DEALER", "ZZT SINK COMBO DEALER"]
WITH_THE_WORDS = ["ZZT KITCHEN SINK PROMO DEALER", "ZZT COMBINE PROMO DEALER", "ZZT SINK COMBO DEALER"]


def _rows(names):
    """The promotions route's own row shape (description + a linked file), pushed through the
    REAL MCP presenter exactly as the live tool answers (`view=render`)."""
    from tests.chatbot.test_outstanding_lane import _present_response

    data = [
        {
            "id": f"00000000-0000-0000-0000-00000000000{i}",
            "description": n,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
            "access_levels": ["sorento_dealer"],
            "is_expired": False,
            "attachments": [
                {
                    "id": f"00000000-0000-0000-0000-0000000001{i:02d}",
                    "attachment": {
                        "id": f"00000000-0000-0000-0000-0000000002{i:02d}",
                        "original_filename": f"{n}.pdf",
                        "stored_filename": f"zzt-{i}.pdf",
                        "file_path": f"https://cdn.example.test/zzt-{i}.pdf",
                        "mime_type": "application/pdf",
                    },
                }
                for _ in [0]
            ],
        }
        for i, n in enumerate(names)
    ]
    body = {"data": data, "pagination": {"total": len(data), "page": 1, "limit": 10}, "empty": False, "fallback_used": False}
    return _present_response()("crm_marketing_promotions_list", json.dumps(body))


@pytest.mark.parametrize("names", [SINK_NAMES_WITHOUT_THE_WORDS, WITH_THE_WORDS], ids=["names-lack-the-words", "one-name-has-them"])
def test_pick_reply_presents_the_subject_filtered_rows_not_a_did_you_mean(session_factory, monkeypatch, names):
    _seed(session_factory)
    behaviour = lambda n, a: _rows(names)  # noqa: E731
    _say(session_factory, monkeypatch, _ask_qf("kitchen sink"), "kitchen sink promo", "zzt-pr-ask", behaviour)
    reply, captured, _ = _say(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-pr-pick", behaviour)
    print("REPLY", repr(reply))
    for bad in ("pin down", "closest matches", "Couldn't find"):
        assert bad.lower() not in reply.lower(), reply
    for name in names:
        assert name in reply, f"{name!r} missing from: {reply!r}"
