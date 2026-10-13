# PLAN: form comment times in Malaysia time (COMMENT-TZ)

**Status:** in review (PR #1550). Track: small fix (no migration, no auth/RBAC change, no new
ingest surface, diff well under 300 lines).

## Ruling

Owner hand test on main `cf9fa6bb4`: a comment posted at 16:26 MYT showed "8:26 AM" on the staff
Comments tab and on the portal. The stored times are naive UTC
(`entity_conversation_messages.created_at` / `edited_at`, `last_responded_at` on stock inquiries
and complaints), the API serialised them with no offset (`form_comment_service.py`), and
`CommentThreadView` formatted them with `formatDateTime`, which reads them as browser-local.

## Seam

- `components/common/comments/CommentThreadView.tsx`: `formatDateTimeInMalaysia` (same helper the
  Respond Outbox uses). Covers the staff Comments tab, the portal submission thread and the staff
  idea thread, which all render through it.
- `app/(auth)/portal/ideas/[token]/components/PortalIdeaTrack.tsx`: same switch for portal idea
  comments (reviewer finding; the idea service sends UTC with `Z`).
- `form_comment_service.py`: `_iso_utc` stamps `+00:00` on `created_at`, `edited_at` and the
  response entry's time, so no reader has to guess. No consumer sorts or compares the string.
- Form comment emails and WhatsApp messages carry no timestamp, so nothing changes there.

## Acceptance criteria

1. A comment stored at `2026-10-09 08:26:20` (UTC) shows `09/10/2026, 4:26 pm` on the staff
   Comments tab and on the portal submission page, whatever the browser's time zone.
2. The comment list API returns `created_at` / `edited_at` as `...+00:00`.
3. Portal idea comments show Malaysia time the same way.
