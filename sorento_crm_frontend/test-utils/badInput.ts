import { fireEvent } from '@testing-library/react';

/**
 * Simulates what a browser does when someone types "5-" or "1e" into a number box:
 * the box reports an empty value with validity.badInput set. React drops a change that
 * does not move the value, so the box is first given a real value.
 */
export function typeBadInput(input: HTMLElement): void {
  const el = input as HTMLInputElement;
  fireEvent.change(el, { target: { value: '7' } });
  Object.defineProperty(el, 'validity', { value: { badInput: true }, configurable: true });
  fireEvent.change(el, { target: { value: '' } });
}
