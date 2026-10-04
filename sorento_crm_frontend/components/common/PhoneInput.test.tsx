import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { PhoneInput, normalisePhone } from './PhoneInput';

describe('normalisePhone (Malaysia default)', () => {
  it.each([
    ['0160000509'],
    ['60160000509'],
    ['+60 16-000 0509'],
    ['+60160000509'],
    ['0060160000509'],
    ['016-000 0509'],
    ['160000509'],
    ['16-000 0509'],
  ])('%s normalises to +60160000509, shown as 16-000 0509 (no trunk 0)', (raw) => {
    expect(normalisePhone(raw, 'MY')).toEqual({
      e164: '+60160000509',
      display: '16-000 0509',
      country: 'MY',
      valid: true,
    });
  });

  it('an incomplete number is not valid and formats without the trunk 0', () => {
    const out = normalisePhone('016675', 'MY');
    expect(out.valid).toBe(false);
    expect(out.display).toBe('16-675');
  });

  it('a lone trunk 0 is accepted and shows nothing', () => {
    expect(normalisePhone('0', 'MY')).toMatchObject({ display: '', valid: false });
  });

  it('a half-typed number for another country keeps its + until it resolves', () => {
    expect(normalisePhone('+659', 'MY')).toMatchObject({ display: '+65 9', country: 'MY', valid: false });
  });

  it('a pasted number from another country switches the country', () => {
    expect(normalisePhone('+65 9123 4567', 'MY')).toMatchObject({
      e164: '+6591234567',
      display: '9123 4567',
      country: 'SG',
      valid: true,
    });
  });

  it('empty input is empty, not an error', () => {
    expect(normalisePhone('   ', 'MY')).toEqual({ e164: '', display: '', country: 'MY', valid: false });
  });
});

function Harness({ onChange, showError }: { onChange?: (v: string, meta: { valid: boolean }) => void; showError?: boolean }) {
  const [value, setValue] = React.useState('');
  return (
    <>
      <label htmlFor="p">Phone number</label>
      <PhoneInput
        id="p"
        value={value}
        showError={showError}
        onChange={(v, meta) => {
          setValue(v);
          onChange?.(v, meta);
        }}
      />
      <output data-testid="value">{value}</output>
    </>
  );
}

const field = () => screen.getByLabelText('Phone number') as HTMLInputElement;

describe('PhoneInput', () => {
  it('defaults to Malaysia: flag trigger names Malaysia (+60) and the dial code shows', () => {
    render(<Harness />);
    expect(screen.getByRole('button', { name: 'Country: Malaysia (+60)' })).toBeInTheDocument();
    expect(screen.getByText('+60')).toBeInTheDocument();
    expect(field()).toHaveAttribute('type', 'tel');
  });

  it.each([['0160000509'], ['016-000 0509'], ['60160000509'], ['+60 16-000 0509']])(
    'pasting %s shows 16-000 0509 and emits +60160000509 as valid',
    (raw) => {
      const onChange = vi.fn();
      render(<Harness onChange={onChange} />);
      fireEvent.change(field(), { target: { value: raw } });
      expect(field().value).toBe('16-000 0509');
      expect(screen.getByTestId('value').textContent).toBe('+60160000509');
      expect(onChange).toHaveBeenLastCalledWith('+60160000509', { valid: true, country: 'MY' });
    },
  );

  it.each([['0160000509'], ['160000509']])(
    'typing %s digit by digit shows 16-000 0509, the trunk 0 dropped at once',
    (keys) => {
      render(<Harness />);
      for (const d of keys) {
        fireEvent.change(field(), { target: { value: field().value + d } });
        if (d === '0' && field().value === '') continue;
        expect(field().value.startsWith('0')).toBe(false);
      }
      expect(field().value).toBe('16-000 0509');
      expect(screen.getByTestId('value').textContent).toBe('+60160000509');
    },
  );

  it('an incomplete number shows no error while typing, then the error state on blur', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.change(field(), { target: { value: '016675' } });
    expect(onChange).toHaveBeenLastCalledWith(expect.any(String), { valid: false, country: 'MY' });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(field()).not.toHaveAttribute('aria-invalid');

    fireEvent.blur(field());
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a complete phone number.');
    expect(field()).toHaveAttribute('aria-invalid', 'true');
    expect(field()).toHaveAttribute('aria-describedby', 'p-error');
    expect(document.querySelector('[data-slot="phone-input"]')).toHaveAttribute('data-invalid', 'true');
  });

  it('completing the number clears the error state', () => {
    render(<Harness />);
    fireEvent.change(field(), { target: { value: '016675' } });
    fireEvent.blur(field());
    expect(screen.getByRole('alert')).toBeInTheDocument();
    fireEvent.change(field(), { target: { value: '0160000509' } });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('showError forces the error state for an incomplete number without a blur', () => {
    render(<Harness showError />);
    fireEvent.change(field(), { target: { value: '0123' } });
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('disabled disables both the country picker and the number', () => {
    render(<PhoneInput value="" onChange={vi.fn()} disabled aria-label="Phone" />);
    expect(screen.getByLabelText('Phone')).toBeDisabled();
    expect(screen.getByRole('button', { name: /Country:/ })).toBeDisabled();
  });

  it('seeds the national number without the trunk 0 from an E.164 value', () => {
    render(<PhoneInput value="+60160000509" onChange={vi.fn()} aria-label="Phone" />);
    expect((screen.getByLabelText('Phone') as HTMLInputElement).value).toBe('16-000 0509');
  });

  it('picking another country re-reads the digits against it', async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.change(field(), { target: { value: '91234567' } });
    fireEvent.click(screen.getByRole('button', { name: 'Country: Malaysia (+60)' }));
    fireEvent.change(await screen.findByPlaceholderText('Search...'), { target: { value: 'Singapore' } });
    fireEvent.click(await screen.findByRole('option', { name: /Singapore/ }));
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('+6591234567', { valid: true, country: 'SG' }),
    );
    expect(screen.getByRole('button', { name: 'Country: Singapore (+65)' })).toBeInTheDocument();
    expect(field().value).toBe('9123 4567');
  });

  it('switching country away and back keeps the number without a trunk 0', async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.change(field(), { target: { value: '0160000509' } });
    expect(field().value).toBe('16-000 0509');
    fireEvent.click(screen.getByRole('button', { name: 'Country: Malaysia (+60)' }));
    fireEvent.change(await screen.findByPlaceholderText('Search...'), { target: { value: 'Singapore' } });
    fireEvent.click(await screen.findByRole('option', { name: /Singapore/ }));
    await screen.findByRole('button', { name: 'Country: Singapore (+65)' });
    fireEvent.click(screen.getByRole('button', { name: 'Country: Singapore (+65)' }));
    fireEvent.change(await screen.findByPlaceholderText('Search...'), { target: { value: 'Malaysia' } });
    fireEvent.click(await screen.findByRole('option', { name: /Malaysia/ }));
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith('+60160000509', { valid: true, country: 'MY' }),
    );
    expect(field().value).toBe('16-000 0509');
  });
});
