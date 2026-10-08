/**
 * The board's change lightbox at 375px (owner feedback 13 Sep, Slice C board display,
 * `documentation/plans/scm/scm-change-management-one-engine-acceptance-criteria.md` AC-C9 to
 * AC-C14).
 *
 * Was the inline Was / Now table's own responsive suite (`PLAN-scm-cs-planning-uat.md` part 3,
 * AC-P3-2 / AC-P3-3) - retired by AC-C9, which replaces that table with one amber hazard icon
 * (`board-change-icon-<rowId>`) and moves everything the table used to say into the dialog
 * AC-C10 opens on click (`board-change-dialog`). The same two guarantees the table had to
 * carry now belong to the icon and the dialog instead:
 *
 * 1. Neither ever overflows. The icon sits inside a ~150px board cell on a phone as much as
 *    on a desktop, so it stays a fixed small glyph rather than sizing itself off its content;
 *    the dialog it opens draws from a full-width, responsive shell with no fixed pixel width
 *    anywhere in it, and truncates a long sentence with the full text in its `title`. jsdom
 *    does not lay out CSS, so this is a class-level check, not a measured one (kept from the
 *    original file's own disclaimer).
 * 2. The retired reaction vocabulary - Replan / Retire / Accept - never reaches the dialog at
 *    any width, because a planner reads what the engine COMPOSED, not the name of a reaction
 *    the row took to itself (`boardChangeAnnotations.ts` module docstring, rule 1; AC-C1).
 *    Keep / Reduce / Release / Reallocate DO reach it: they are now the suggestion's own
 *    words, printed verbatim from the server's sentence.
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BoardChangeTable } from './BoardChangeTable';
import type { BoardChangeAnnotation } from '../../_shared/lib/boardChangeAnnotations';

function annotation(overrides: Partial<BoardChangeAnnotation> = {}): BoardChangeAnnotation {
  return {
    rowId: 'pcr-381895-1',
    soNumber: 'SO381895',
    lineNo: 1,
    itemCode: 'SRTWCX7405-RL-S-PJ',
    kind: 'advanced',
    closed: false,
    was: { qty: '10', date: '2026-08-25', decision: 'Buy 10' },
    now: {
      qty: '25',
      date: '2026-08-19',
      decision: 'Use BRW 5 from BRW, 10 from WH3 . Borrow 10 from WH3-NTC',
    },
    suggestionLines: ['Keep 10', 'Buy 15 for 19 Aug'],
    lateDays: null,
    shortfallQty: null,
    productChangedFrom: null,
    movedTransfer: null,
    projectLineId: 'pl-381895-1',
    ...overrides,
  };
}

const ORIGINAL_WIDTH = window.innerWidth;

function setViewport(width: number) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
  window.dispatchEvent(new Event('resize'));
}

beforeEach(() => {
  setViewport(375);
});

afterEach(() => {
  setViewport(ORIGINAL_WIDTH);
  vi.restoreAllMocks();
});

/** Opens the dialog the icon triggers, and hands back the dialog element (AC-C10). */
function openDialog(rowId: string) {
  fireEvent.click(screen.getByTestId(`board-change-icon-${rowId}`));
  return screen.getByTestId('board-change-dialog');
}

describe('the change lightbox at 375px', () => {
  it('opens a dialog that draws from a full-width, responsive container rather than a fixed pixel width', () => {
    render(<BoardChangeTable annotation={annotation()} />);
    const dialog = openDialog('pcr-381895-1');

    // The viewport sizes it; it must never size itself off a literal pixel value - the same
    // rule the inline table's own container followed before AC-C9 replaced it with the icon.
    expect(dialog.className).toMatch(/\b(w-full|max-w-)/);
    expect(dialog.className).not.toMatch(/\bw-\[\d+px\]/);
  });

  it('gives no part of the dialog a fixed pixel width', () => {
    render(<BoardChangeTable annotation={annotation()} />);
    const dialog = openDialog('pcr-381895-1');

    // Scoped to `[data-slot="dialog-body"]` - `BoardChangeSummary`'s own content, which is
    // what this component is answerable for - rather than the whole `DialogContent`
    // subtree. The header/title/close-button chrome above it is the shared `Dialog`
    // primitive's (`components/ui/dialog.tsx`), not this component's content, and is out
    // of scope here the same way a page's shared `PageHeader` is out of scope for a form
    // component's own responsive tests.
    const body = dialog.querySelector<HTMLElement>('[data-slot="dialog-body"]');
    expect(body).not.toBeNull();
    for (const el of Array.from(body!.querySelectorAll<HTMLElement>('*'))) {
      // `className` is an `SVGAnimatedString` on an SVG element rather than a plain
      // string, and `.not.toMatch` would throw on it directly.
      const classes = el.getAttribute('class') ?? '';
      expect(classes).not.toMatch(/\b(w|min-w)-\[\d+px\]/);
    }
  });

  it('truncates the long text a decision sentence and a suggestion line can carry, rather than widening the dialog', () => {
    render(
      <BoardChangeTable
        annotation={annotation({
          suggestionLines: [
            'Reallocate PO-A 34 to SO420103 ORDER 50 at BRW-IB, a sentence long enough to run past any dialog width',
          ],
        })}
      />,
    );
    const dialog = openDialog('pcr-381895-1');

    // Decision 1 (`Buy 10`) and Decision 2 (the long borrow sentence in `annotation()`'s
    // `now.decision`) both changed, so the dialog's own Decision row carries the long one.
    // AC-D1 moved that from a `Decision <from> -> <to>` line to the Was/Now table's own
    // Decision row, so the cell is found through the row's header rather than by reading
    // the retired line's text; the guarantee is unchanged - the long sentence truncates and
    // rides in full in the cell's `title`.
    const decisionRow = within(dialog)
      .getByText('Decision')
      .closest('tr') as HTMLElement;
    expect(decisionRow).not.toBeNull();
    const decisionLine = within(decisionRow).getByTestId('change-now-decision');
    expect(decisionLine.className).toMatch(/\btruncate\b/);
    expect(decisionLine.getAttribute('title')).toContain(
      'Use BRW 5 from BRW, 10 from WH3',
    );

    const suggestionLine = within(dialog).getByTestId('board-change-suggestion-line');
    expect(suggestionLine.className).toMatch(/\btruncate\b/);
    expect(suggestionLine.getAttribute('title')).toContain(
      'Reallocate PO-A 34 to SO420103 ORDER 50',
    );
  });

  it('shrinks the icon to a compact size inside the board cell, without shrinking the dialog it opens', () => {
    const { unmount } = render(<BoardChangeTable annotation={annotation()} compact />);
    const compactIcon = screen.getByTestId('board-change-icon-pcr-381895-1');
    const compactClass = compactIcon.className;
    unmount();

    render(<BoardChangeTable annotation={annotation()} />);
    const normalIcon = screen.getByTestId('board-change-icon-pcr-381895-1');
    // The compact grid cell draws a visibly smaller icon than the list/non-compact reading.
    expect(compactClass).not.toBe(normalIcon.className);

    // Whichever icon opened it, the DIALOG itself is never the compact scale - it is a
    // lightbox over the whole screen, not a board cell squeezed to ~150px.
    const dialog = openDialog('pcr-381895-1');
    expect(dialog.className).not.toMatch(/text-\[10px\]/);
  });

  it('never renders a retired reaction word inside the dialog, at 375px or otherwise', () => {
    render(<BoardChangeTable annotation={annotation()} />);
    const dialog = openDialog('pcr-381895-1');
    const printed = dialog.textContent ?? '';
    for (const verb of ['Replan', 'Retire', 'Accept']) {
      expect(printed).not.toContain(verb);
    }
    // What it DOES print is the engine's own sentence for each component.
    expect(printed).toContain('Keep 10');
  });

  it('renders "Where it went" once Apply has run and recorded where a held share landed', () => {
    render(
      <BoardChangeTable
        annotation={annotation({
          whereItWent: ['Reallocate 202607-S0080 3 to pool'],
        })}
      />,
    );
    const dialog = openDialog('pcr-381895-1');

    const where = within(dialog).getByTestId(
      'board-change-where-pcr-381895-1',
    );
    expect(where).toHaveTextContent('Reallocate 202607-S0080 3 to pool');
  });

  it('renders no "Where it went" section on a row Apply has not written anything for', () => {
    render(<BoardChangeTable annotation={annotation({ whereItWent: [] })} />);
    const dialog = openDialog('pcr-381895-1');

    expect(
      within(dialog).queryByTestId('board-change-where-pcr-381895-1'),
    ).not.toBeInTheDocument();
  });
});
