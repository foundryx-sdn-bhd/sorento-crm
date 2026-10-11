"""Fail unless the alembic migration graph has exactly one head (CI fast gate).

Two branches each merge main and add their own revision; each is green alone,
and the second to land gives main two heads, at which point `alembic upgrade
head` refuses and every database-touching job dies at bootstrap. This parses
the version files with the stdlib only (no pip install: alembic's env.py
imports the whole app), builds the revision/down_revision graph, and exits
non-zero unless exactly one head remains.

Run by .github/workflows/deploy.yml: the `check-migration-heads` job (push,
release, merge queue) and a step of the PR gate (CI-COST round 2).

Usage: python3 scripts/ci_alembic_heads.py [versions dir]
"""
from __future__ import annotations

import ast
import pathlib
import sys


def heads(versions: pathlib.Path) -> tuple[int, list[str]]:
    revs: set[str] = set()
    downs: set[str] = set()
    for f in sorted(versions.glob("*.py")):
        # encoding is explicit: several migrations hold non-ASCII literals, and
        # a bare read_text() decodes with the runner's locale, which is ASCII
        # whenever LANG is unset.
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                name, value = node.target.id, node.value
            else:
                continue
            if name not in ("revision", "down_revision"):
                continue
            val = ast.literal_eval(value)
            if name == "revision":
                revs.add(val)
            elif val is not None:
                downs.update([val] if isinstance(val, str) else val)
    return len(revs), sorted(revs - downs)


def main() -> int:
    versions = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "sorento_crm_backend/alembic/versions")
    count, found = heads(versions)
    print(f"{count} revisions, {len(found)} head(s): {found}")
    if len(found) != 1:
        print("the migration graph must have exactly one head: merge main and add a merge revision joining these heads",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
