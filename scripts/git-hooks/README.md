# scripts/git-hooks

Repo-owned git hooks (version-controlled, not `.git/hooks`).

`pre-push` mirrors CI's fast gates before a push leaves the machine: single
alembic head vs `origin/main`, `py_compile` of changed `.py` files under
Python 3.12, `vitest run` on touched `*.test.ts(x)` (+ `components/ui/*.inventory.test.ts`
if `components/ui/` changed), the em-dash/en-dash guard (which skips
`sorento_crm_backend/alembic/data/*.txt`, verbatim owner text a data migration loads), and a 200 KB cap on
any added or modified `*.png` vs `origin/main` (compress with `pngquant`
before pushing). Under 60s.

Install once per clone (worktrees share the main checkout's config):
`git config core.hooksPath scripts/git-hooks`

Bypass: `SKIP_PREPUSH=1 git push` or `git push --no-verify`.

`check_alembic_heads.py` ports CI's `check-migration-heads` ast logic as-is.
