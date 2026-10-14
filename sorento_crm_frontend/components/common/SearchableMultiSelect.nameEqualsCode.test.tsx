import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/react';

import {
  SearchableMultiSelect,
  type SearchableMultiSelectOption,
} from './SearchableMultiSelect';

// AC-2: the second line is hidden when it equals the label under the AC-1 rule.
const optionTexts = () =>
  [...document.querySelectorAll('[role="option"]')].map((o) => o.textContent?.trim());

function open(options: SearchableMultiSelectOption[]) {
  render(<SearchableMultiSelect value={[]} onChange={() => {}} options={options} />);
  fireEvent.click(document.querySelector('[data-slot="searchable-multi-select-trigger"]')!);
}

afterEach(() => cleanup());

describe('SearchableMultiSelect description equal to label (AC-2)', () => {
  it('renders the text once when description equals label', async () => {
    open([{ value: 'a', label: '1861', description: '1861' }]);
    await waitFor(() => expect(optionTexts()).toEqual(['1861']));
  });

  it('renders once when description differs only by case and spaces', async () => {
    open([{ value: 'a', label: '11X11', description: ' 11x11 ' }]);
    await waitFor(() => expect(optionTexts()).toEqual(['11X11']));
  });

  it('still renders a differing description', async () => {
    const desc = 'SORENTO ANTI-ODOR FLOOR TRAP (150x150MM) SRT6641';
    open([{ value: 'a', label: 'SRT6641', description: desc }]);
    await waitFor(() => expect(optionTexts()).toEqual([`SRT6641${desc}`]));
  });
});
