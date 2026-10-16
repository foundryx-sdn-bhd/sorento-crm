/**
 * DatePicker typed input: a partial or impossible date never becomes a different real day.
 * DO-BACKFILL-SETTING review: typing "2026-07-01" key by key used to parse "2026-07-0" as
 * 30 Jun 2026 (Date rolls day 0 back), re-sync the box to 30/06/2026 and save a day early.
 */
import React, { useState } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import { DatePicker } from './date-picker';

afterEach(cleanup);

function Controlled({ onChange }: { onChange: (d: Date | undefined) => void }) {
  const [value, setValue] = useState<Date | undefined>(undefined);
  return (
    <DatePicker
      ariaLabel="Day"
      value={value}
      onChange={(d) => {
        onChange(d);
        setValue(d);
      }}
    />
  );
}

const ymd = (d: Date | undefined) =>
  d ? [d.getFullYear(), d.getMonth() + 1, d.getDate()] : undefined;

function typeKeys(input: HTMLElement, text: string) {
  for (let i = 1; i <= text.length; i += 1) {
    fireEvent.change(input, { target: { value: text.slice(0, i) } });
  }
}

describe('DatePicker typed input', () => {
  it('typing an ISO date key by key lands on that day, never a rolled-over one', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText('Day') as HTMLInputElement;

    typeKeys(input, '2026-07-01');

    const days = onChange.mock.calls.map(([d]) => ymd(d));
    expect(days).not.toContainEqual([2026, 6, 30]);
    expect(days[days.length - 1]).toEqual([2026, 7, 1]);
    expect(input.value).toBe('01/07/2026');
  });

  it('an impossible DD/MM/YYYY date is not accepted', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Day'), {
      target: { value: '31/02/2026' },
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('a real DD/MM/YYYY date is accepted', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Day'), {
      target: { value: '01/07/2026' },
    });
    expect(ymd(onChange.mock.calls[0][0])).toEqual([2026, 7, 1]);
  });
});
