# Telegram master-resume update (inbound webhook)

## Goal

Let the owner send a PDF to the Telegram bot at any time. The Worker validates
it, stores it content-addressed in R2, and atomically repoints the master
resume used by every future application. The local machine can pull the same
master so R2 plus the D1 pointer stay the single source of truth.

## Scope

- New inbound route `POST /telegram/webhook` on the Worker, authenticated by
  the Telegram `X-Telegram-Bot-Api-Secret-Token` header and restricted to the
  owner's chat id.
- PDF-only uploads with size cap, `%PDF-` magic check, and `pdf-lib` parse;
  sha256 dedupe makes re-delivery idempotent.
- Content-addressed R2 objects `cvs/master/<sha256>.pdf`; old versions are
  retained (immutable history), never overwritten.
- New D1 `settings` table holding `master_resume_sha`; the Worker apply path
  prefers the pointer and falls back to the `MASTER_RESUME_SHA` secret.
- `/master` status command plus bounded confirmation/error replies.
- `scripts/pull_master.py` local sync with `--check` drift mode.
- `/health` exposes `master_resume_sha`; `verify_deploy` validates its shape.

Out of scope: editing or merging PDFs, profile/skill updates via Telegram,
multiple resume variants, deleting old versions, an always-on local daemon,
any new unauthenticated data endpoint, and processing message types beyond
document upload and the exact `/master` command.

## Architecture

The Worker gains one route in the existing `fetch` handler
(`worker/src/index.ts`), served from a new module `worker/src/lib/webhook.ts`.
Processing is inline (no new queue): `getFile` + file download + parse +
R2 put + D1 upsert + reply stays well inside Telegram's timeout, and the
sha comparison makes re-delivery a no-op so retries cannot corrupt state.

Order of checks in the handler:

1. `X-Telegram-Bot-Api-Secret-Token` must equal `TELEGRAM_WEBHOOK_SECRET`
   (new Worker secret); otherwise 401 with no side effects.
2. Chat allowlist: `message.chat.id` must equal `TELEGRAM_CHAT_ID`;
   foreign chats are silently ignored (200, no reply, no writes).
3. `/master` exact text command replies with the current pointer.
4. `message.document` only; MIME must be `application/pdf` or filename must
   end `.pdf`; `file_size` and downloaded length must be `<= 15 MB`;
   bytes must start with `%PDF-` and parse via `pdf-lib` (already bundled).
5. `sha256` via `crypto.subtle`. Equal to the current pointer value means
   "already current": reply, zero writes.
6. Otherwise put `cvs/master/<sha>.pdf` and upsert
   `settings(key='master_resume_sha')`, then reply.

Reply contract (stable strings, tests depend on the prefixes):

```text
✅ Master resume updated: <sha8> (<pages> pages, <bytes> bytes). Previous: <old8>.
Already current: <sha8> (<pages> pages).
Master resume sha: <sha8> (source: d1|secret).
❌ Not a valid PDF: <short reason>. Master unchanged.
❌ File too large (<mb> MB > 15 MB). Master unchanged.
❌ Upload failed: <short reason>. Master unchanged.
```

Security rules: message text is never executed or echoed; tokens, document
bytes, and full bodies never appear in logs; only `file_id`, `sha256`, and
byte counts may be logged; handler returns 200 after processing (including
user-facing errors) so Telegram does not retry-storm; only the secret-header
mismatch returns 401.

Pointer and apply path: `worker/schema.sql` gains an idempotent `settings`
table (`key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL`)
plus a runtime `ensureSettings(db)` migration mirroring
`ensureNotificationEvents`. The apply path resolves
`sha = settings.master_resume_sha || env.MASTER_RESUME_SHA`, so the secret
remains the bootstrap/fallback and future uploads need no secret rotation.
`/health` reports `master_resume_sha` (pointer or secret, `null` when
neither is set).

Local sync: `scripts/pull_master.py` reads the pointer with
`wrangler d1 execute jobhunt --remote --json`, downloads
`cvs/master/<sha>.pdf` via `wrangler r2 object get --remote`, verifies the
sha, and atomically replaces `data/cvs/Abdulsamed_Hamdy.pdf`. `--check`
compares only and exits 1 on drift without writing. Wrangler is invoked with
`cwd=worker/`; parse/sha/atomic-write logic is factored into pure helpers for
tests.

## Data flow

1. Owner sends a PDF (or `/master`) to the bot.
2. Telegram POSTs the update to `/telegram/webhook` with the secret header.
3. Handler verifies header and chat, validates the document, downloads
   bytes, checks sha against the current pointer.
4. New bytes: R2 `put` under the content-addressed key, then D1 upsert of
   `settings.master_resume_sha`.
5. Handler replies; Telegram shows the confirmation in the chat.
6. Next `tailor-apply` message resolves the pointer and reads the new
   `cvs/master/<sha>.pdf`; existing jobs and old objects are untouched.
7. On the local machine `scripts/pull_master.py` fetches the same object so
   the Python pipeline and Rezi paths use the same bytes; `--check` fails
   loudly on drift.

## Ops

- Revoke the pasted token in BotFather, reissue; store via
  `wrangler secret put TELEGRAM_BOT_TOKEN` and set
  `wrangler secret put TELEGRAM_WEBHOOK_SECRET` (random hex).
- One-time registration: `setWebhook` with `secret_token`, and for this bot
  `drop_pending_updates=true`; `deleteWebhook` is the rollback.
- Post-deploy e2e: `/master` (status), then send the current PDF bytes
  (expect "Already current"), then a modified copy (expect "updated").
- Rollback: `deleteWebhook`; D1 pointer back to the previous sha (or delete
  the row) restores the old master; no R2 object is ever deleted.

## Validation

- `worker/test/webhook.test.ts` (vitest, mock fetch/R2/D1): valid swap
  writes object + pointer + reply; wrong secret 401 with no writes; foreign
  chat ignored; non-PDF, oversized, and corrupt-bytes rejected; same-sha
  no-op; `/master` reply; double delivery idempotent.
- `tests/test_pull_master.py`: pointer parse, sha verify, drift `--check`
  exit code, atomic replace, subprocess mocked.
- `tests/test_verify_deploy.py`: health check accepts a well-formed
  `master_resume_sha`, flags missing/malformed values.
- Focused suites then the full repository suite stay green; Worker `tsc`
  clean.

## Files

- `worker/src/lib/webhook.ts` (new), `worker/src/index.ts` (route, apply
  pointer, `/health`, `ensureSettings` call).
- `worker/src/state.ts` (`ensureSettings`, `getSetting`, `setSetting`),
  `worker/schema.sql` (settings table).
- `worker/test/webhook.test.ts` (new), `worker/test/health.test.ts`.
- `scripts/pull_master.py` (new), `tests/test_pull_master.py` (new).
- `scripts/verify_deploy.py` + `tests/test_verify_deploy.py`.
- Docs: `README.md`, `worker/README.md`, `AGENTS.md` (bot is no longer
  strictly outbound-only; document the restricted inbound surface).
