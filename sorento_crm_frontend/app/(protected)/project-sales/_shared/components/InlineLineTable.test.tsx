/**
 * The line table that behaves like a spreadsheet.
 *
 * What is pinned here is the BEHAVIOUR the client asked for, not the markup: a row is added
 * inline and takes the caret, a cell is edited where it sits, Tab walks and rolls onto the
 * next row, Enter drops down, Escape puts one cell back, the total moves while a quantity is
 * typed, picking an option fills the rest of the row, a row that cannot be saved marks the
 * cell at fault instead of vanishing, and removing a row asks first.
 *
 * Values stay STRINGS end to end: what is typed is what the caller is handed.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/toast', () => ({
  toast: {
    custom: vi.fn(),
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

import {
  InlineLineTable,
  type InlineDraft,
  type InlineLineColumn,
  type InlineStagedRow,
} from './InlineLineTable';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

interface Row {
  id: string;
  code: string;
  description: string;
  quantity: string;
  unit_price: string;
  notes: string;
}

const UNITS = [
  { value: 'pcs', label: 'Pieces' },
  { value: 'set', label: 'Sets' },
];

function row(overrides: Partial<Row> = {}): Row {
  return {
    id: 'r1',
    code: 'pcs',
    description: 'Wall-hung WC',
    quantity: '2',
    unit_price: '900.00',
    notes: '',
    ...overrides,
  };
}

const onCreate = vi.fn<(draft: InlineDraft) => Promise<void>>(async () => {});
const onUpdate = vi.fn<(row: Row, draft: InlineDraft) => Promise<void>>(async () => {});
const onDelete = vi.fn<(row: Row) => Promise<void>>(async () => {});

function columns(): InlineLineColumn<Row>[] {
  return [
    {
      key: 'description',
      header: 'Description',
      width: 200,
      kind: 'text',
    },
    {
      key: 'quantity',
      header: 'Qty',
      width: 90,
      kind: 'number',
      align: 'end',
      validate: (value) => (/^\d*\.?\d*$/.test(value) ? null : 'Must be a number'),
    },
    {
      key: 'unit_price',
      header: 'Unit price',
      width: 120,
      kind: 'number',
      align: 'end',
    },
    {
      key: 'code',
      header: 'Unit',
      width: 120,
      kind: 'select',
      options: UNITS,
      resolveSelected: (_row, draft) => UNITS.find((unit) => unit.value === draft.code),
    },
    {
      key: 'total',
      header: 'Total',
      width: 120,
      kind: 'derived',
      align: 'end',
      derive: (draft) =>
        `RM ${(Number(draft.quantity || 0) * Number(draft.unit_price || 0)).toFixed(2)}`,
    },
  ];
}

function toDraft(item: Row): InlineDraft {
  return {
    description: item.description,
    quantity: item.quantity,
    unit_price: item.unit_price,
    code: item.code,
    notes: item.notes,
  };
}

function emptyDraft(): InlineDraft {
  return { description: '', quantity: '1', unit_price: '', code: '', notes: '' };
}

function renderTable(props: Partial<React.ComponentProps<typeof InlineLineTable<Row>>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InlineLineTable<Row>
        rows={[row()]}
        getRowId={(item) => item.id}
        columns={columns()}
        toDraft={toDraft}
        emptyDraft={emptyDraft}
        onCreate={onCreate}
        onUpdate={onUpdate}
        onDelete={onDelete}
        describeRow={(item, index) => item?.code ?? `line ${index + 1}`}
        rowDetail={{ key: 'notes', label: 'Notes' }}
        validateRow={(draft): Record<string, string> =>
          draft.description.trim() ? {} : { description: 'Needed' }
        }
        {...props}
      />
    </QueryClientProvider>,
  );
}

function cell(name: string) {
  return screen.getByRole('textbox', { name });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('InlineLineTable', () => {
  it('keeps its header and its add row when there is nothing in it', () => {
    renderTable({ rows: [], emptyHint: 'Nothing quoted yet.' });

    // The header survives an empty table: a person about to type needs to see the shape
    // of what they are about to type into.
    expect(screen.getByRole('columnheader', { name: 'Description' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Qty' })).toBeInTheDocument();
    expect(screen.getByText('Nothing quoted yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a line' })).toBeInTheDocument();
  });

  it('appends an editable row inline and puts the caret in its first cell', async () => {
    renderTable({ rows: [] });

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));

    const added = await screen.findByRole('textbox', { name: 'Description on line 1' });
    await waitFor(() => expect(document.activeElement).toBe(added));
    // No dialog opened; the row IS the form.
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('keeps a freshly added row alive when its picker is opened', async () => {
    // The client hit this: click Add a line, click the product dropdown, and the row
    // vanished before anything could be picked. Opening a Radix popover fires focusout
    // on the row with a NULL relatedTarget, which read as "the caret left the table" and
    // sent the untouched row down the discard path. Focus has not left the row: it has
    // gone into the row's own popover.
    renderTable({ rows: [] });
    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    await screen.findByRole('textbox', { name: 'Description on line 1' });

    const picker = screen.getByRole('combobox', { name: 'Unit on line 1' });
    fireEvent.focusOut(picker, { relatedTarget: null });
    fireEvent.click(picker);

    expect(
      screen.getByRole('textbox', { name: 'Description on line 1' }),
    ).toBeInTheDocument();
  });

  it('leaves an untouched added row waiting rather than discarding it', async () => {
    // Removing a row is the Remove button's job, and that asks first. Nothing the user
    // does with the caret should make a row disappear underneath them.
    renderTable({ rows: [] });
    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    const added = await screen.findByRole('textbox', { name: 'Description on line 1' });

    const outside = document.createElement('button');
    document.body.appendChild(outside);
    fireEvent.focusOut(added, { relatedTarget: outside });

    await waitFor(() =>
      expect(
        screen.getByRole('textbox', { name: 'Description on line 1' }),
      ).toBeInTheDocument(),
    );
    // And it was not saved either: nothing was typed, so there is nothing to send.
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('edits a text cell and a number cell where they sit', () => {
    renderTable();

    fireEvent.change(cell('Description on pcs'), { target: { value: 'Rimless WC' } });
    fireEvent.change(cell('Qty on pcs'), { target: { value: '12' } });

    expect(cell('Description on pcs')).toHaveValue('Rimless WC');
    expect(cell('Qty on pcs')).toHaveValue('12');
  });

  it('picks a value in a select cell through the searchable dropdown', async () => {
    renderTable();

    fireEvent.click(screen.getByRole('combobox', { name: 'Unit on pcs' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Sets' }));

    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Unit on pcs' })).toHaveTextContent('Sets'),
    );
  });

  it('recomputes the line total while the quantity is being typed', () => {
    renderTable();

    expect(screen.getByText('RM 1800.00')).toBeInTheDocument();

    fireEvent.change(cell('Qty on pcs'), { target: { value: '3' } });

    // Nothing was saved and nothing was refetched: the number moved off the draft.
    expect(screen.getByText('RM 2700.00')).toBeInTheDocument();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('walks across the row with Tab and rolls onto the next one', async () => {
    renderTable({ rows: [row(), row({ id: 'r2', code: 'set' })] });

    const first = cell('Description on pcs');
    first.focus();

    fireEvent.keyDown(first, { key: 'Tab' });
    expect(document.activeElement).toBe(cell('Qty on pcs'));

    fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
    expect(document.activeElement).toBe(cell('Unit price on pcs'));

    // Last editable cell of the row is the select, so Tab from it lands on the next row.
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByRole('combobox', { name: 'Unit on pcs' }));

    fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
    await waitFor(() =>
      expect(document.activeElement).toBe(cell('Description on set')),
    );
  });

  it('walks backwards with Shift-Tab and rolls onto the previous row', async () => {
    renderTable({ rows: [row(), row({ id: 'r2', code: 'set' })] });

    const second = cell('Description on set');
    second.focus();

    fireEvent.keyDown(second, { key: 'Tab', shiftKey: true });
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole('combobox', { name: 'Unit on pcs' }),
      ),
    );
  });

  it('drops down a row on Enter', async () => {
    renderTable({ rows: [row(), row({ id: 'r2', code: 'set' })] });

    const first = cell('Qty on pcs');
    first.focus();
    fireEvent.keyDown(first, { key: 'Enter' });

    await waitFor(() => expect(document.activeElement).toBe(cell('Qty on set')));
  });

  it('puts one cell back on Escape, and leaves the rest of the row alone', () => {
    renderTable();

    fireEvent.change(cell('Description on pcs'), { target: { value: 'Changed' } });
    fireEvent.change(cell('Qty on pcs'), { target: { value: '9' } });

    fireEvent.keyDown(cell('Description on pcs'), { key: 'Escape' });

    expect(cell('Description on pcs')).toHaveValue('Wall-hung WC');
    expect(cell('Qty on pcs')).toHaveValue('9');
  });

  it('marks the cell at fault instead of dropping the row or raising a toast', async () => {
    renderTable();

    fireEvent.change(cell('Description on pcs'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save pcs' }));

    expect(await screen.findByText('Needed')).toBeInTheDocument();
    expect(cell('Description on pcs')).toHaveAttribute('aria-invalid', 'true');
    // The row is still there, still holding what was typed, and nothing was sent.
    expect(onUpdate).not.toHaveBeenCalled();

    // Fixing the cell clears the mark without a second attempt.
    fireEvent.change(cell('Description on pcs'), { target: { value: 'Rimless WC' } });
    expect(screen.queryByText('Needed')).toBeNull();
  });

  it('marks a per-cell shape error on the cell that holds it', async () => {
    renderTable();

    fireEvent.change(cell('Qty on pcs'), { target: { value: 'twelve' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save pcs' }));

    expect(await screen.findByText('Must be a number')).toBeInTheDocument();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('saves a row when the caret moves on to another row, the way a spreadsheet does', async () => {
    renderTable({ rows: [row(), row({ id: 'r2', code: 'set' })] });

    const first = cell('Qty on pcs');
    first.focus();
    fireEvent.change(first, { target: { value: '12' } });

    cell('Qty on set').focus();

    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    expect(onUpdate.mock.calls[0][1]).toMatchObject({ quantity: '12' });
  });

  it('leaves a clean row alone when the caret passes through it', async () => {
    renderTable({ rows: [row(), row({ id: 'r2', code: 'set' })] });

    cell('Qty on pcs').focus();
    cell('Qty on set').focus();

    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('saves an edited row through the caller, as strings', async () => {
    renderTable();

    fireEvent.change(cell('Qty on pcs'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save pcs' }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'r1' }),
      expect.objectContaining({ quantity: '12', unit_price: '900.00' }),
    );
  });

  it('creates an added row through the caller, carrying fields that have no column', async () => {
    renderTable({ rows: [] });

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    const description = await screen.findByRole('textbox', {
      name: 'Description on line 1',
    });
    fireEvent.change(description, { target: { value: 'Bespoke vanity top' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save line 1' }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(onCreate).toHaveBeenCalledWith({
      description: 'Bespoke vanity top',
      quantity: '1',
      unit_price: '',
      code: '',
      notes: '',
    });
  });

  it('creates an added row once, however many times the save is asked for', async () => {
    renderTable({ rows: [] });

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    const description = await screen.findByRole('textbox', {
      name: 'Description on line 1',
    });
    fireEvent.change(description, { target: { value: 'First' } });

    // Two commit paths can meet on one row (focus leaving it, and the tick being pressed).
    // Whichever gets there first, the line goes onto the record once.
    const save = screen.getByRole('button', { name: 'Save line 1' });
    fireEvent.click(save);
    fireEvent.click(save);

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(onCreate.mock.calls[0][0]).toMatchObject({ description: 'First' });
  });

  it('keeps the fields with no column in a per-row note, rather than losing them', async () => {
    renderTable();

    fireEvent.click(screen.getByRole('button', { name: 'Notes on pcs' }));
    const note = await screen.findByRole('textbox', { name: 'Notes on pcs' });
    fireEvent.change(note, { target: { value: 'Agreed with the QS on 3 July' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save pcs' }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    expect(onUpdate.mock.calls[0][1]).toMatchObject({
      notes: 'Agreed with the QS on 3 July',
    });
  });

  it('asks before removing a row, and only then removes it', async () => {
    renderTable();

    fireEvent.click(screen.getByRole('button', { name: 'Remove pcs' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/cannot be undone/i)).toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 'r1' })));
  });

  it('drops an added row nobody typed into without a question, since it holds nothing', async () => {
    renderTable({ rows: [] });

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    await screen.findByRole('textbox', { name: 'Description on line 1' });

    fireEvent.click(screen.getByRole('button', { name: 'Remove line 1' }));

    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Description on line 1' })).toBeNull(),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('ticks a box in the row and hands the caller the string it holds', async () => {
    // A boolean still travels as a string, like every other draft field, so the value the
    // caller reads is the one the shared helper defines rather than one each screen invents.
    renderTable({
      columns: [
        ...columns(),
        { key: 'flagged', header: 'Rate only', width: 90, kind: 'checkbox' },
      ],
    });

    const box = screen.getByRole('checkbox', { name: 'Rate only on pcs' });
    expect(box).not.toBeChecked();

    fireEvent.click(box);
    expect(box).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Save pcs' }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    expect(onUpdate.mock.calls[0][1]).toMatchObject({ flagged: 'true' });
  });

  it('reads a ticked box as a word when the table cannot be edited', () => {
    renderTable({
      readOnly: true,
      columns: [
        ...columns(),
        { key: 'flagged', header: 'Rate only', width: 90, kind: 'checkbox' },
      ],
      toDraft: (item) => ({ ...toDraft(item), flagged: 'true' }),
    });

    // Not a greyed-out box, which is easy to mistake for one that has not loaded yet.
    expect(screen.getByText('Yes')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('renders a band heading once, spanning the table, above the line that carries it', () => {
    renderTable({
      rows: [row(), row({ id: 'r2', code: 'set' })],
      toDraft: (item) => ({
        ...toDraft(item),
        band_label: item.id === 'r1' ? 'BILL NO 3 PAGE 15/4' : '',
      }),
      band: { key: 'band_label', label: 'Section heading' },
    });

    const heading = screen.getByRole('textbox', { name: 'Section heading on pcs' });
    expect(heading).toHaveValue('BILL NO 3 PAGE 15/4');
    // Only the line that opens the section shows it. The one below is inside the band.
    expect(screen.getAllByDisplayValue('BILL NO 3 PAGE 15/4')).toHaveLength(1);
    expect(screen.queryByRole('textbox', { name: 'Section heading on set' })).toBeNull();

    // Immediately above its own line and inside the same table, so no amount of sorting or
    // re-rendering can leave a heading stranded over somebody else's lines.
    const bandRow = heading.closest('tr');
    expect(bandRow?.nextElementSibling).toBe(cell('Qty on pcs').closest('tr'));
    expect(bandRow?.querySelector('td')?.getAttribute('colspan')).toBe(
      String(columns().length + 1),
    );
  });

  it('re-titles a section by typing in the heading it already shows', async () => {
    // Editing a section is clicking its heading, which is a cell like any other. There is no
    // control to find first: the heading IS the affordance.
    renderTable({
      toDraft: (item) => ({ ...toDraft(item), band_label: 'BILL NO 3' }),
      band: { key: 'band_label', label: 'Section heading' },
    });

    const heading = screen.getByRole('textbox', { name: 'Section heading on pcs' });
    fireEvent.change(heading, { target: { value: 'OPTION' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save pcs' }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    expect(onUpdate.mock.calls[0][1]).toMatchObject({ band_label: 'OPTION' });
  });

  it('offers adding a line and adding a section side by side', () => {
    renderTable({ band: { key: 'band_label', label: 'Section heading' } });

    const add = screen.getByRole('button', { name: 'Add a line' });
    const addSection = screen.getByRole('button', { name: 'Add a section' });
    // The per-row icon that used to turn a line into a heading is gone: the client called it
    // counterintuitive, and a row cannot advertise what it might become.
    expect(screen.queryByRole('button', { name: 'Section heading on pcs' })).toBeNull();
    expect(add.parentElement).toBe(addSection.parentElement);
  });

  it('adds a section as a line with its heading open and holding the caret', async () => {
    renderTable({ band: { key: 'band_label', label: 'Section heading' } });

    fireEvent.click(screen.getByRole('button', { name: 'Add a section' }));

    const heading = await screen.findByRole('textbox', { name: 'Section heading on line 2' });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    // The line underneath is ready too: a section is a line that carries a heading, not a
    // record of its own.
    expect(screen.getByRole('textbox', { name: 'Description on line 2' })).toBeInTheDocument();

    fireEvent.change(heading, { target: { value: 'OPTIONAL ITEMS' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Description on line 2' }), {
      target: { value: 'Grab bar' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save line 2' }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(onCreate.mock.calls[0][0]).toMatchObject({
      band_label: 'OPTIONAL ITEMS',
      description: 'Grab bar',
    });
  });

  it('folds an empty band editor away when the caret leaves the row', async () => {
    renderTable({
      rows: [row(), row({ id: 'r2', code: 'set' })],
      band: { key: 'band_label', label: 'Section heading' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add a section' }));
    await screen.findByRole('textbox', { name: 'Section heading on line 3' });

    // A mis-click should not strand a blank heading above a line for the rest of the session.
    cell('Qty on set').focus();
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Section heading on line 3' })).toBeNull(),
    );
  });

  it('fills the rest of the row from the option that was picked', async () => {
    // One decision, five cells. The column says what the option means; the table writes it.
    renderTable({
      columns: columns().map((column) =>
        column.key === 'code'
          ? {
              ...column,
              onOptionSelected: (option): InlineDraft =>
                option
                  ? { description: `Sold in ${option.label}`, unit_price: '42.00' }
                  : {},
            }
          : column,
      ),
    });

    fireEvent.click(screen.getByRole('combobox', { name: 'Unit on pcs' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Sets' }));

    await waitFor(() =>
      expect(cell('Description on pcs')).toHaveValue('Sold in Sets'),
    );
    expect(cell('Unit price on pcs')).toHaveValue('42.00');
    // Nothing was saved: the fill is a draft edit like any other keystroke.
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('D1: keeps the picked value when onOptionSelected spreads the whole draft back', async () => {
    renderTable({
      columns: columns().map((column) =>
        column.key === 'code'
          ? {
              ...column,
              onOptionSelected: (option, draft): InlineDraft => ({
                ...draft,
                description: `Sold in ${option?.label ?? ''}`,
              }),
            }
          : column,
      ),
    });

    fireEvent.click(screen.getByRole('combobox', { name: 'Unit on pcs' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Sets' }));

    await waitFor(() => expect(cell('Description on pcs')).toHaveValue('Sold in Sets'));
    expect(screen.getByRole('combobox', { name: 'Unit on pcs' })).toHaveTextContent('Sets');
  });

  it('lets a picked option overwrite what somebody typed, which is the client\'s rule', async () => {
    // The tradeoff was put to the client and chosen: one product means one set of fields, and
    // picking it twice gives the same row both times. An edit made before a re-pick is lost.
    renderTable({
      columns: columns().map((column) =>
        column.key === 'code'
          ? {
              ...column,
              onOptionSelected: (option): InlineDraft =>
                option ? { description: `Sold in ${option.label}` } : {},
            }
          : column,
      ),
    });

    fireEvent.change(cell('Description on pcs'), { target: { value: 'Hand-written wording' } });
    fireEvent.click(screen.getByRole('combobox', { name: 'Unit on pcs' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Sets' }));

    await waitFor(() => expect(cell('Description on pcs')).toHaveValue('Sold in Sets'));
  });

  it('numbers a derived column by its position, and closes the gap after a delete', () => {
    // An item number IS the row it sits on. Nobody renumbers 52 lines by hand after an insert.
    const numbered: InlineLineColumn<Row>[] = [
      { key: 'no', header: 'Item', width: 60, kind: 'derived', derive: (_d, index) => index + 1 },
      ...columns(),
    ];
    const three = [row(), row({ id: 'r2', code: 'set' }), row({ id: 'r3', code: 'set' })];
    const { rerender } = renderTable({ rows: three, columns: numbered });

    const numbersInColumn = () =>
      Array.from(screen.getByRole('table').querySelectorAll('tbody tr')).map(
        (tr) => tr.querySelector('td')?.textContent,
      );

    expect(numbersInColumn()).toEqual(['1', '2', '3']);

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    rerender(
      <QueryClientProvider client={client}>
        <InlineLineTable<Row>
          rows={[three[0], three[2]]}
          getRowId={(item) => item.id}
          columns={numbered}
          toDraft={toDraft}
          emptyDraft={emptyDraft}
          onCreate={onCreate}
          onUpdate={onUpdate}
          onDelete={onDelete}
          describeRow={(item, index) => item?.code ?? `line ${index + 1}`}
        />
      </QueryClientProvider>,
    );

    expect(numbersInColumn()).toEqual(['1', '2']);
  });

  it('sums its footer from the live drafts, not from the saved rows', () => {
    const withFooter = columns().map((column) =>
      column.key === 'total'
        ? {
            ...column,
            footer: (drafts: InlineDraft[]) =>
              `RM ${drafts
                .reduce(
                  (sum, draft) =>
                    sum + Number(draft.quantity || 0) * Number(draft.unit_price || 0),
                  0,
                )
                .toFixed(2)}`,
          }
        : column,
    );
    renderTable({ columns: withFooter });

    const foot = () => screen.getByRole('table').querySelector('tfoot')?.textContent ?? '';
    expect(foot()).toContain('RM 1800.00');

    fireEvent.change(cell('Qty on pcs'), { target: { value: '3' } });

    // Nothing was saved and nothing was refetched: the bottom line follows the cells above it.
    expect(foot()).toContain('RM 2700.00');
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('hands the live drafts to a total that lives outside the table', () => {
    const onDraftsChange = vi.fn();
    renderTable({ onDraftsChange });

    fireEvent.change(cell('Qty on pcs'), { target: { value: '7' } });

    const last = onDraftsChange.mock.calls.at(-1)?.[0] as InlineDraft[];
    expect(last).toHaveLength(1);
    expect(last[0]).toMatchObject({ quantity: '7', unit_price: '900.00' });
  });

  it('offers nothing to type into when it cannot be edited', () => {
    renderTable({ readOnly: true });

    expect(screen.getByRole('columnheader', { name: 'Description' })).toBeInTheDocument();
    expect(screen.getByText('Wall-hung WC')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add a line' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Remove pcs' })).toBeNull();
  });
});

/**
 * Staged mode: the screen owns the changes, and this table writes nothing.
 *
 * Everything above still holds for a table that IS the feature. What changes here is a table
 * that is one section of a document with a single Save over all of it - the client's "every
 * addition of line doesn't trigger a save, cause now i delete each line, then you ask me to
 * confirm, then when i add line, you also trigger save, very annoying".
 */
describe('InlineLineTable staged mode', () => {
  /** A reported row as this harness stores it, so an unchanged report can be recognised. */
  function toRow({ rowKey, draft }: InlineStagedRow): Row {
    return {
      id: rowKey,
      code: draft.code,
      description: draft.description,
      quantity: draft.quantity,
      unit_price: draft.unit_price,
      notes: draft.notes,
    };
  }

  function sameRows(a: Row[], b: Row[]): boolean {
    return a.length === b.length && a.every((item, index) => {
      const other = b[index];
      return (Object.keys(item) as (keyof Row)[]).every((key) => item[key] === other[key]);
    });
  }

  /**
   * The screen, reduced to the part the table talks to: it holds the rows and the marks.
   *
   * The identity guard is not test scaffolding, it is the contract. The table reports on every
   * render, so a screen that stored a fresh array each time would hand back new rows, provoke
   * another report, and spin. The real session guards the same way.
   */
  function StagedHarness({ initial }: { initial: Row[] }) {
    const [rows, setRows] = React.useState(initial);
    const [removed, setRemoved] = React.useState<string[]>([]);

    return (
      <InlineLineTable<Row>
        rows={rows}
        getRowId={(item) => item.id}
        columns={columns()}
        toDraft={toDraft}
        emptyDraft={emptyDraft}
        describeRow={(item, index) => item?.code || `line ${index + 1}`}
        staging={{
          onChange: (reported) => {
            lastStaged = reported;
            const next = reported.map(toRow);
            setRows((previous) => (sameRows(previous, next) ? previous : next));
          },
          isRemoved: (item) => removed.includes(item.id),
          toggleRemove: (rowKey) =>
            setRemoved((previous) =>
              previous.includes(rowKey)
                ? previous.filter((key) => key !== rowKey)
                : [...previous, rowKey],
            ),
        }}
      />
    );
  }

  let lastStaged: InlineStagedRow[] = [];

  function renderStaged(initial: Row[] = [row()]) {
    lastStaged = [];
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <StagedHarness initial={initial} />
      </QueryClientProvider>,
    );
  }

  it('reports every change at once and never calls a write', async () => {
    renderStaged();

    fireEvent.change(cell('Qty on pcs'), { target: { value: '12' } });

    await waitFor(() => expect(lastStaged[0]?.draft.quantity).toBe('12'));
    // The key travels with the draft. Without it the screen cannot tell an existing line from
    // one added a moment ago, and a whole-set write would insert duplicates and delete originals.
    expect(lastStaged[0]?.rowKey).toBe('r1');
    expect(onUpdate).not.toHaveBeenCalled();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('does not save when the caret moves off the row', async () => {
    renderStaged([row(), row({ id: 'r2', code: 'set' })]);

    const first = cell('Qty on pcs');
    first.focus();
    fireEvent.change(first, { target: { value: '12' } });
    cell('Qty on set').focus();

    await waitFor(() => expect(lastStaged[0]?.draft.quantity).toBe('12'));
    // The blur-save is the whole complaint. It is not a save any more, it is a report.
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('drops the per-row tick and the Unsaved pill, since one Save covers the lot', () => {
    renderStaged();

    fireEvent.change(cell('Qty on pcs'), { target: { value: '12' } });

    expect(screen.queryByRole('button', { name: 'Save pcs' })).toBeNull();
    expect(screen.queryByText('Unsaved')).toBeNull();
  });

  it('strikes a removed row through without asking, and restores it', async () => {
    renderStaged();

    fireEvent.click(screen.getByRole('button', { name: 'Remove pcs' }));

    // Nothing was destroyed, so there is nothing to confirm. The confirmation moves to Save,
    // which is where lines actually leave the record.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onDelete).not.toHaveBeenCalled();
    expect(await screen.findByText('Removed on save')).toBeInTheDocument();
    // Still on screen, and no longer editable: a removal nobody can see cannot be taken back.
    expect(screen.getByText('Wall-hung WC')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Description on pcs' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Restore pcs' }));

    expect(
      await screen.findByRole('textbox', { name: 'Description on pcs' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Removed on save')).toBeNull();
  });

  it('leaves a row staged for removal out of the totals', async () => {
    const withFooter = columns().map((column) =>
      column.key === 'total'
        ? {
            ...column,
            footer: (drafts: InlineDraft[]) =>
              `RM ${drafts
                .reduce(
                  (sum, draft) =>
                    sum + Number(draft.quantity || 0) * Number(draft.unit_price || 0),
                  0,
                )
                .toFixed(2)}`,
          }
        : column,
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function Harness() {
      const [removed, setRemoved] = React.useState<string[]>([]);
      return (
        <InlineLineTable<Row>
          rows={[row(), row({ id: 'r2', code: 'set' })]}
          getRowId={(item) => item.id}
          columns={withFooter}
          toDraft={toDraft}
          emptyDraft={emptyDraft}
          describeRow={(item, index) => item?.code || `line ${index + 1}`}
          staging={{
            onChange: () => {},
            isRemoved: (item) => removed.includes(item.id),
            toggleRemove: (rowKey) => setRemoved((previous) => [...previous, rowKey]),
          }}
        />
      );
    }
    render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );

    const foot = () => screen.getByRole('table').querySelector('tfoot')?.textContent ?? '';
    expect(foot()).toContain('RM 3600.00');

    fireEvent.click(screen.getByRole('button', { name: 'Remove pcs' }));

    // The figure has to be what will actually be charged. Counting a line on its way off the
    // quotation states a total nobody is ever going to pay.
    await waitFor(() => expect(foot()).toContain('RM 1800.00'));
  });

  it('draws an added row once, even after the screen hands it back', async () => {
    // The screen echoes the reported rows straight back through `rows`, so the table is holding
    // the same new row twice for one render. Drawn twice under one key, every keystroke would
    // land in both copies.
    renderStaged([]);

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    await screen.findByRole('textbox', { name: 'Description on line 1' });

    await waitFor(() =>
      expect(screen.getAllByRole('textbox', { name: 'Description on line 1' })).toHaveLength(1),
    );
    expect(lastStaged).toHaveLength(1);
    expect(lastStaged[0].rowKey.startsWith('new:')).toBe(true);
    expect(onCreate).not.toHaveBeenCalled();
  });
});
