"""Before/after of the parser prompt the model receives (PLAN-prompt-dynamic-30sep R6).

BEFORE = the `production` version, rendered as a turn renders it today.
AFTER  = the wording layer of that same text (`chatbot_prompt_vars.wording_layer`),
         rendered with every registry variable filled from the tables as they stand.

Read-only: computes both in memory, publishes nothing, moves no label. Run it on the
prod-copy database and attach the output to the PR:

    venv/bin/python -m scripts.prompt_dynamic_render_diff > render-diff.txt

The first block is the transform's own report (which lists became which variable, and
which stayed literal because the registry does not hold every value the hand list did).
"""
from __future__ import annotations

import difflib
import sys


def main() -> int:
    import app.main  # noqa: F401  registers every model
    from app.database import SessionLocal
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services import chatbot_prompt_vars as pv
    from app.services.ai_prompt_registry import _substitute, extract_tokens

    db = SessionLocal()
    try:
        prod = (
            db.query(AIPromptVersion)
            .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
            .filter(AIPromptLabel.name == pv.PROMPT_KEY, AIPromptLabel.label == "production")
            .first()
        )
        if prod is None:
            print("no production version of chatbot_semantic_parser", file=sys.stderr)
            return 1
        date = "{{current_date}}"
        before_vars = {n: pv.render_value(db, n) for n in extract_tokens(prod.template) & set(pv.VARIABLE_NAMES)}
        before = _substitute(prod.template, before_vars)
        wording, report = pv.wording_layer(prod.template, db)
        after_vars = {n: pv.render_value(db, n) for n in extract_tokens(wording) & set(pv.VARIABLE_NAMES)}
        after = _substitute(wording, after_vars)
        print(f"# production v{prod.version}, {len(before)} chars rendered before, {len(after)} after")
        print(f"# current_date left as {date} on both sides")
        for line in report:
            print(f"# {line}")
        print()
        sys.stdout.writelines(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"v{prod.version} rendered (before)",
                tofile="wording layer rendered (after)",
                n=1,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
