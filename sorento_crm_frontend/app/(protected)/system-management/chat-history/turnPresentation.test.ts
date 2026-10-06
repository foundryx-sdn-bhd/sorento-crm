/**
 * S2b Phase 2 test-first: pure-function coverage for `turnPresentation.ts` (AC-251,
 * AC-252, AC-254). Like `TurnPanel.test.tsx`, this locks Phase 1 behaviour that already
 * ships rather than red-then-green on its own - no test file existed for this module
 * before this one.
 */
import { describe, it, expect } from 'vitest';
import {
  buildTimeline,
  canRetry,
  formatMs,
  laneWords,
  memoryChips,
  shortTurnId,
  stageLabel,
  stageRecords,
  turnDuration,
  turnHeadline,
  turnNotes,
} from './turnPresentation';
import type { ChatbotTurn, TurnTraceRecord } from './types/chatbotTurn.types';

function record(over: Partial<TurnTraceRecord> = {}): TurnTraceRecord {
  return {
    stage: 'received',
    status: 'ok',
    started_at: '2026-09-05T06:00:00.000Z',
    ms: 100,
    summary: 'ok',
    why: 'ok',
    facts: {},
    error: null,
    raw: null,
    ...over,
  };
}

function turn(over: Partial<ChatbotTurn> = {}): ChatbotTurn {
  return {
    id: 'ZZT-turn',
    contact_respond_id: 'ZZT-contact',
    message_id: null,
    status: 'done',
    stage: null,
    branch_kind: null,
    attempt: 1,
    is_test: false,
    created_at: '2026-09-05T06:00:00.000Z',
    finished_at: null,
    trace: [],
    response: null,
    ...over,
  };
}

describe('turnHeadline (AC-251)', () => {
  it('names the failed stage on a failed turn', () => {
    expect(turnHeadline(turn({ status: 'failed', stage: 'access' }))).toEqual({
      word: 'Failed at Access',
      tone: 'failed',
    });
  });

  it('falls back to Received when a failed turn has no stage recorded', () => {
    expect(turnHeadline(turn({ status: 'failed', stage: null }))).toEqual({
      word: 'Failed at Received',
      tone: 'failed',
    });
  });

  it('is Answered for a plain business_query done turn', () => {
    expect(turnHeadline(turn({ status: 'done', branch_kind: 'business_query' })).word).toBe(
      'Answered',
    );
  });

  it('is Escalated for out_of_scope', () => {
    expect(turnHeadline(turn({ status: 'done', branch_kind: 'out_of_scope' })).word).toBe(
      'Escalated',
    );
  });

  it('is Not allowed (ok) for an ai_not_allowed done turn', () => {
    expect(turnHeadline(turn({ status: 'done', branch_kind: 'ai_not_allowed' }))).toEqual({
      word: 'Not allowed',
      tone: 'ok',
    });
  });

  it('is pending while the turn is still running or delegated', () => {
    expect(turnHeadline(turn({ status: 'processing' })).tone).toBe('pending');
    expect(turnHeadline(turn({ status: 'delegated' })).tone).toBe('pending');
  });
});

describe('laneWords / stageLabel', () => {
  it('says Lane not reached for a null branch_kind', () => {
    expect(laneWords(null)).toBe('Lane not reached');
  });

  it('says AI not allowed for an ai_not_allowed branch_kind', () => {
    expect(laneWords('ai_not_allowed')).toBe('AI not allowed');
  });

  it('humanises an off-timeline failure stage', () => {
    expect(stageLabel('casual_llm')).toBe('Small talk');
    expect(stageLabel('queued')).toBe('Queue');
  });
});

describe('buildTimeline (AC-252)', () => {
  it('collapses everything after the failed stage into one not-reached row', () => {
    const t = turn({
      status: 'failed',
      trace: [
        record({ stage: 'received' }),
        record({ stage: 'understood', status: 'failed', error: 'boom' }),
      ],
    });
    const rows = buildTimeline(t);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({ kind: 'stage', label: 'Received' });
    expect(rows[1]).toMatchObject({ kind: 'stage', label: 'Understood' });
    expect(rows[2].kind).toBe('not-reached');
    if (rows[2].kind === 'not-reached') {
      expect(rows[2].labels).toEqual([
        'Access',
        'Routed',
        'Looked up',
        'Replied',
        'Remembered',
        'Sent',
      ]);
    }
  });

  it('adds no not-reached row on a done turn with a short trace (the lane legitimately skipped stages)', () => {
    const t = turn({
      status: 'done',
      branch_kind: 'clarify_menu',
      trace: [record({ stage: 'received' }), record({ stage: 'understood' })],
    });
    expect(buildTimeline(t)).toHaveLength(2);
  });

  it('inserts the not-reached row right after the failed row, not at the end (sent can still run on a failed turn)', () => {
    const t = turn({
      status: 'failed',
      trace: [
        record({ stage: 'received' }),
        record({ stage: 'understood', status: 'failed', error: 'boom' }),
        record({ stage: 'sent' }),
      ],
    });
    const rows = buildTimeline(t);
    const kinds = rows.map((r) => r.kind);
    expect(kinds).toEqual(['stage', 'stage', 'not-reached', 'stage']);
  });

  it('excludes a note record from the timeline (S2b hardening 1c)', () => {
    // A note (an operator asking for a retry) is written into the SAME trace array by
    // the endpoint - `app/api/v1/system/chatbot.py::retry_turn` - carrying the stage the
    // turn stopped at so it sorts with the failure it belongs to. Drawn as a timeline
    // row it reads as a second "Sent" stage, not as a record of what someone did.
    const t = turn({
      status: 'failed',
      trace: [
        record({ stage: 'received' }),
        record({ stage: 'understood', status: 'failed', error: 'boom' }),
        {
          ...record({ stage: 'sent' }),
          kind: 'note',
          summary: 'Retry requested by an operator.',
        },
      ],
    });
    const rows = buildTimeline(t);
    const stageRows = rows.filter((r) => r.kind === 'stage');
    // Only `received` and `understood` are real steps; the note must not show up as a
    // third "Sent" row.
    expect(stageRows).toHaveLength(2);
    expect(stageRows.some((r) => r.record.summary.includes('Retry requested'))).toBe(false);
  });
});

describe('stageRecords / turnNotes (S2b hardening 1c)', () => {
  const noteRecord: TurnTraceRecord = {
    ...record({ stage: 'sent' }),
    kind: 'note',
    summary: 'Retry requested by an operator.',
  };
  const t = turn({
    trace: [record({ stage: 'received' }), record({ stage: 'understood' }), noteRecord],
  });

  it('stageRecords drops every note record', () => {
    const stages = stageRecords(t);
    expect(stages).toHaveLength(2);
    expect(stages.every((r) => r.kind !== 'note')).toBe(true);
  });

  it('turnNotes returns only the note records, oldest first', () => {
    const notes = turnNotes(t);
    expect(notes).toEqual([noteRecord]);
  });

  it('turnNotes is empty for a turn with no notes', () => {
    expect(turnNotes(turn({ trace: [record({ stage: 'received' })] }))).toEqual([]);
  });
});

/**
 * The live bug: the engine's `TurnTrace.add` sub-events ride the same `trace` array and
 * carry no `stage`, so `kind !== 'note'` let a `tool` event through as a timeline row and
 * `stageLabel(undefined).replace` crashed the whole Chat History page through the error
 * boundary. Every one of the six kinds is covered, because the filter is the contract.
 */
describe('sub-events on the trace (chatbot-growth r1 TurnTrace.add)', () => {
  const toolEvent: TurnTraceRecord = {
    kind: 'tool',
    at: '2026-09-05T06:00:01.000Z',
    name: 'stock_list',
    args: { code: 'SRTWC8517' },
    envelope: { rows: [] },
    ms: 42,
  };
  const t = turn({
    status: 'done',
    trace: [record({ stage: 'received' }), toolEvent, record({ stage: 'sent' })],
  });

  it('stageRecords drops a sub-event, which has no stage', () => {
    expect(stageRecords(t).map((r) => r.stage)).toEqual(['received', 'sent']);
  });

  it('buildTimeline renders only the stages and does not throw', () => {
    expect(buildTimeline(t)).toEqual([
      { kind: 'stage', record: expect.objectContaining({ stage: 'received' }), label: 'Received' },
      { kind: 'stage', record: expect.objectContaining({ stage: 'sent' }), label: 'Sent' },
    ]);
  });

  it('turnNotes ignores sub-events', () => {
    expect(turnNotes(t)).toEqual([]);
  });

  it.each(['tool', 'crossdomain', 'reveals', 'decay', 'focus', 'open_question'] as const)(
    'a %s event never becomes a timeline row',
    (kind) => {
      const withEvent = turn({ trace: [record({ stage: 'received' }), { kind }] });
      expect(buildTimeline(withEvent)).toHaveLength(1);
    },
  );

  it('stageLabel names a missing stage instead of throwing', () => {
    expect(stageLabel(undefined)).toBe('Unknown');
  });
});

describe('memoryChips (AC-254)', () => {
  it('classifies kept / new / cleared and sorts kept before new before cleared', () => {
    const chips = memoryChips(
      record({
        stage: 'remembered',
        raw: {
          before: { domain_hint: 'inventory', pending: { kind: 'x' }, access_levels: ['dealer'] },
          after: { domain_hint: 'inventory', entities: [{ raw: 'x' }] },
        },
      }),
    );
    expect(chips.map((c) => c.kind)).toEqual(['kept', 'new', 'cleared', 'cleared']);
    const kept = chips.find((c) => c.kind === 'kept');
    expect(kept?.rawKey).toBe('domain_hint');
    expect(kept?.label).toBe('topic');
  });

  it('returns an empty list when the record has no after payload', () => {
    expect(memoryChips(record({ raw: null }))).toEqual([]);
    expect(memoryChips(undefined)).toEqual([]);
  });

  it('does not treat an empty array as a set value', () => {
    const chips = memoryChips(record({ raw: { before: {}, after: { entities: [] } } }));
    expect(chips).toEqual([]);
  });
});

describe('canRetry (AC-253/AC-257 client-side guard)', () => {
  it('is true only when status is failed', () => {
    expect(canRetry(turn({ status: 'failed' }))).toBe(true);
    expect(canRetry(turn({ status: 'done' }))).toBe(false);
    expect(canRetry(turn({ status: 'delegated' }))).toBe(false);
  });
});

describe('formatMs / turnDuration / shortTurnId', () => {
  it('formats sub-second durations in ms and larger ones in seconds', () => {
    expect(formatMs(420)).toBe('420 ms');
    expect(formatMs(4200)).toBe('4.2 s');
  });

  it('computes total wall time from created_at/finished_at', () => {
    const t = turn({ created_at: '2026-09-05T06:00:00.000Z', finished_at: '2026-09-05T06:00:04.100Z' });
    expect(turnDuration(t)).toBe('4.1 s');
  });

  it('is null while the turn has not finished', () => {
    expect(turnDuration(turn({ finished_at: null }))).toBeNull();
  });

  it('never surfaces a bare UUID (cursor rule)', () => {
    const id = '8f2c41d6-0b7a-4d33-9c2e-11ab5f0e7a10';
    const short = shortTurnId(id);
    expect(short).not.toContain('-');
    expect(short.length).toBe(4);
  });
});
