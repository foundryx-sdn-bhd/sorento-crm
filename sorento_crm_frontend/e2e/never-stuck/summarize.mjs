#!/usr/bin/env node
/**
 * Turns the never-stuck smoke's Playwright JSON report into a short Markdown summary:
 * counts, new failures (what to fix), known failures that passed tonight (entries to
 * retire), then the known failures still failing, folded. Written to $GITHUB_STEP_SUMMARY in CI and to
 * stdout always.
 *
 *   node e2e/never-stuck/summarize.mjs test-results/never-stuck/results.json
 */
import fs from 'node:fs';

const file = process.argv[2] ?? 'test-results/never-stuck/results.json';
if (!fs.existsSync(file)) {
  console.log(`## Never-stuck smoke\n\nNo report at \`${file}\`: the run died before Playwright reported.`);
  process.exit(0);
}
const report = JSON.parse(fs.readFileSync(file, 'utf8'));

const rows = [];
const walk = (suite) => {
  for (const s of suite.suites ?? []) walk(s);
  for (const spec of suite.specs ?? []) {
    for (const t of spec.tests ?? []) {
      const last = t.results?.[t.results.length - 1];
      const msg = (last?.error?.message ?? '').replace(/\u001b\[[0-9;]*m/g, '');
      // The spec's own assertion prints the problems array; keep its quoted lines only.
      const problems = [...msg.matchAll(/^\s*[+-]?\s*"(.+)",?$/gm)].map((m) => m[1]);
      const note = (t.annotations ?? []).find((a) => a.type.startsWith('known-'));
      const redirectedTo = (t.annotations ?? []).find((a) => a.type === 'redirected')?.description;
      rows.push({
        title: spec.title,
        status: t.status,
        known: note?.type ?? '',
        detail: problems.length ? problems.join('; ') : msg.split('\n')[0].slice(0, 200),
        audit: note?.description ?? '',
        redirectedTo,
      });
    }
  }
};
for (const s of report.suites ?? []) walk(s);

const newFailures = rows.filter((r) => r.status === 'unexpected' || r.status === 'flaky');
const nowPassing = rows.filter((r) => r.known === 'known-passed');
const stillKnown = rows.filter((r) => r.known === 'known-failure');
const passed = rows.filter((r) => r.status === 'expected' && !r.known);
const skipped = rows.filter((r) => r.status === 'skipped');
const redirected = rows.filter((r) => r.redirectedTo);

const esc = (s) => String(s).replace(/\|/g, '\\|');
const out = ['## Never-stuck smoke', ''];
out.push(
  `| passed | new failures | known (audit row, fix lane open) | known now passing | redirected off the route | skipped |`,
  `|---|---|---|---|---|---|`,
  `| ${passed.length} | ${newFailures.length} | ${stillKnown.length} | ${nowPassing.length} | ${redirected.length} | ${skipped.length} |`,
  '',
);
if (newFailures.length) {
  out.push('### New failures (fix the screen, or add an audit-referenced entry)', '', '| test | problem |', '|---|---|');
  for (const r of newFailures) out.push(`| \`${esc(r.title)}\` | ${esc(r.detail)} |`);
  out.push('');
}
if (nowPassing.length) {
  out.push(
    '### Known failures that passed tonight (delete the entry once it passes every night)',
    '',
    '| test | audit |',
    '|---|---|',
  );
  for (const r of nowPassing) out.push(`| \`${esc(r.title)}\` | ${esc(r.audit)} |`);
  out.push('');
}
if (stillKnown.length) {
  out.push('<details><summary>Known failures still failing (audit row, fix lane open)</summary>', '', '| test | audit: problem |', '|---|---|');
  for (const r of stillKnown) out.push(`| \`${esc(r.title)}\` | ${esc(r.audit)} |`);
  out.push('', '</details>', '');
}
if (redirected.length) {
  out.push('<details><summary>Redirected off the requested route (checked where they landed)</summary>', '', '| test | landed on |', '|---|---|');
  for (const r of redirected) out.push(`| \`${esc(r.title)}\` | \`${esc(r.redirectedTo)}\` |`);
  out.push('', '</details>', '');
}
// A run that died in global setup (sign-in, seed) reports errors and no tests: say so
// rather than print a green line over a red job.
const errors = (report.errors ?? []).map((e) => (e.message ?? String(e)).split('\n')[0]);
if (errors.length) {
  out.push('### Run errors (no route was checked)', '');
  for (const e of errors) out.push(`- ${e.replace(/\u001b\[[0-9;]*m/g, '')}`);
  out.push('');
}
if (!newFailures.length && !errors.length && rows.length > skipped.length) out.push('Nothing new is stuck.', '');

const md = out.join('\n');
console.log(md);
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, md + '\n');
