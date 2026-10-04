# UAC: PII scrub of the public repo (PII-SCRUB)

Plan: [PLAN-pii-scrub.md](PLAN-pii-scrub.md)

- **AC-1** `python3 scripts/pii_guard.py` on `main` HEAD prints `pii-guard: clean` and exits 0.
- **AC-2** No Respond.io contact id that exists in the dev DB appears in any tracked file or path,
  including inside a fixture `phone` built from the id.
- **AC-3** No real MY mobile number appears in any tracked file in any of these shapes: `+60`,
  `60`, `0060`, `%2B60`, `0`, `(01x)`, with spaces or dashes, after a letter or underscore, or
  inside an Office file.
- **AC-4** Every value under a `phone`, `wa_id`, `contact_phone*` or `mobile*` key is a fake shape.
- **AC-5** No labelled lorry plate (Lorry Plate, lorry_plate, Lorry No, car/vehicle/truck plate or
  no, No Plat, No Kenderaan, a `plate` key) holds anything but `PLATE-N`.
- **AC-6** No `.playwright-mcp/`, `*.har` or `*.rdb` path is tracked, and `.gitignore` covers them.
- **AC-7** The guard's output never contains the matched value.
- **AC-8** Replay fixtures stay self-consistent: the chatbot replay and node-fixture suites pass
  with the same results as `main` (pre-existing corpus failures excepted).
- **AC-9** No behaviour change: app-code edits are docstrings, examples and sample data, except
  two dev-only seeds (migration 183's optional dev-user link and `seed_project_cs_demo.py`'s demo
  owner), which now name a fake email and so fall back.
- **AC-10** The `pii-guard` job runs on every `ci`-labelled PR and on pushes to `main`, beside the
  alembic gate; the pre-push hook runs the same script.

Round 3 (review findings, 3 Oct):

- **AC-11** `is_fake_phone` passes only these shapes: `0000` straight after the 60xx prefix, the
  exact classic `012-345 6789` / `+60123456789`, and one digit repeated through the whole subscriber
  number. A `0000` run in the middle of the subscriber number is NOT fake; `010-0000 0001` is NOT fake.
- **AC-12** The phone-keyed rule also reads `phoneNumber`, `phone_no`, `whatsapp`, `msisdn` keys, and
  YAML-style values (`phone: '...'`, `phone: ...` unquoted).
- **AC-13** A UTF-16 text file (with a BOM) is decoded and scanned, not skipped.
- **AC-14** `scripts/chatbot_record_turn.py` never writes a real Respond.io contact id into a
  recorded contact: `id` and every field derived from it (`firstName ZZT-<id>`, `email`, `phone`)
  use one stable fake id per real id (a fake 9-digit id with a `0000` run, so the derived phone is
  a guard-approved fake).
- **AC-15** The module docstring of `scripts/pii_guard.py` describes the rules as they are.

Round 4 (owner, 3 Oct: no secrets in compose files, and stop it happening again):

- **AC-16** `sorento_crm/docker-compose.yml`: `POSTGRES_USER`, `POSTGRES_PASSWORD` and `JWT_SECRET`
  are required (`${VAR:?message}`) everywhere they are used (service env, healthcheck, URLs); the
  commented sample `.env` block holds no sample secret values (names only, or `<set me>`).
- **AC-17** `sorento_crm_backend/docker-compose.yml`: `POSTGRES_PASSWORD` and `JWT_SECRET` are
  required; `DATABASE_URL` / `DIRECT_URL` are built from `${POSTGRES_USER}` / `${POSTGRES_PASSWORD}`
  (no inline `user:password@`). No tracked compose file has a `${VAR:-default}` with a non-empty
  default for a name matching `PASSWORD|SECRET|TOKEN|ACCESS_KEY` (AWS key ids included).
- **AC-18** Local stacks still start: a tracked `.env.example` beside each compose file lists every
  required variable with a placeholder, and the compose header comment says to copy it to `.env`.
- **AC-19** `.gitignore` ignores `docker-compose.override*`, `compose.override*`, `*.key` and
  `.env.*`, while `.env.example` files stay tracked (`!.env.example`, `!**/.env.example`).
- **AC-20** CI runs gitleaks on the PR diff in the label-gated workflow (`deploy.yml`, beside the
  PII guard), with a pinned action version and a repo `.gitleaks.toml` that extends the default
  rules and allowlists only the fake placeholders (`ci-dummy-secret`, `cloud-lane-test-key`,
  `<set me>`-style placeholders, example.com/example.invalid). The job fails on a new secret.
  The prod compose file lives on the server outside git and is out of scope.

Round 5 (security re-review, 3 Oct):

- **AC-21** The gitleaks job runs on a `ci`-labelled `pull_request` and on `push`, and is skipped on
  `merge_group` (the action rejects it) and `workflow_dispatch` (a release would scan all history,
  red until the rewrite lands). The action is pinned to a 40-char commit SHA (with the version in a
  comment), `GITLEAKS_VERSION` is set to an exact version, and every `.gitleaks.toml` allowlist
  regex is anchored (`^...$`) so a secret that merely contains a placeholder is still reported.
- **AC-22** `scripts/chatbot_record_turn.py` writes no real contact id anywhere in a recording:
  every contact-id position (`contact.id`, `contactId` at any depth, `contact_id` in tool-call
  args and tool results, the `--contact` default output file name) carries the SAME fake id for
  the same real id, so a recording stays self-consistent. The fake-id space is at least 100,000
  values (no merging of contacts in a 40-contact sample), and every derived phone passes the guard.
- **AC-23** After the recorder's scrub, the real contact id text occurs nowhere in the recorded
  turn (any key, any depth, inside strings such as URLs and session vars like `respond_io_id`); it
  is replaced by the same fake id. Attachment links (`attachment.url`, `attachment.source_url` and
  similar media URLs) point at `https://cdn.example.invalid/...` placeholders, in the recorder's
  output and in the committed fixtures.
- **AC-24** Every value in a driver field of a tracked file (`*Driver:* <value>` in reply text,
  a `"Driver"` label/value pair, a `driver_name` key) is a `DRIVER X` placeholder or one of the
  non-person labels `FROM AUTOCOUNT`, `Documentation`, `-`, `null`/empty.
