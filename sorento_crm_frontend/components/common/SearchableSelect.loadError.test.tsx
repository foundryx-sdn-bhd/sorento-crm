/**
 * L5 (NEVER-STUCK-UI S3, "Pickers / selects"): a picker whose options failed to load says
 * so; it never looks like an empty list.
 *
 * Static mode: the caller passes the options query's `error` as `loadError` and its
 * `refetch` as `onRetry`. Async mode (`fetchOptions`): a rejected fetch is caught by the
 * component itself and gets the same state, with a Retry that runs the fetch again.
 * A refusal says "no access" and offers no Retry; anything else offers Retry.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

import { SearchableSelect } from './SearchableSelect';
import { SearchableMultiSelect } from './SearchableMultiSelect';

const openSingle = () =>
  fireEvent.click(document.querySelector('[data-slot="searchable-select-trigger"]')!);
const openMulti = () =>
  fireEvent.click(document.querySelector('[data-slot="searchable-multi-select-trigger"]')!);

beforeEach(() => vi.clearAllMocks());

describe('SearchableSelect load failure', () => {
  it('a refusal reads as no access, not "No results found."', async () => {
    render(
      <SearchableSelect
        value=""
        onChange={vi.fn()}
        options={[]}
        loadError={new Error('Permission required: user_management.users.view')}
        onRetry={vi.fn()}
      />,
    );
    // The closed control already says it, so nobody has to open it to find out.
    expect(screen.getByRole('combobox')).toHaveTextContent(/no access/i);
    openSingle();
    await waitFor(() =>
      expect(screen.getByTestId('select-load-failure')).toHaveTextContent(/don.t have access/i),
    );
    expect(screen.queryByText('No results found.')).not.toBeInTheDocument();
    expect(screen.queryByText(/user_management\.users\.view/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
  });

  it('any other failure offers a Retry that refetches', async () => {
    const onRetry = vi.fn();
    render(
      <SearchableSelect
        value=""
        onChange={vi.fn()}
        options={[]}
        loadError={new Error('Server error. Try again or contact support.')}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole('combobox')).toHaveTextContent(/could not load/i);
    openSingle();
    const retry = await screen.findByRole('button', { name: /retry/i });
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('No results found.')).not.toBeInTheDocument();
  });

  it('a chosen value keeps its label on the trigger even when the list failed', () => {
    render(
      <SearchableSelect
        value="a"
        onChange={vi.fn()}
        options={[{ value: 'a', label: 'Alpha' }]}
        loadError={new Error('Server error.')}
      />,
    );
    expect(screen.getByRole('combobox')).toHaveTextContent('Alpha');
  });

  it('options that are still there (a static "All" entry, a stale list) stay pickable under the failure line', async () => {
    // Deliberate: the Users role filter always carries "All roles", so hiding the failure
    // whenever any option exists would hide it exactly where audit row T13 needs it.
    const onChange = vi.fn();
    render(
      <SearchableSelect
        value=""
        onChange={onChange}
        options={[{ value: 'all', label: 'All roles' }]}
        loadError={new Error('Permission required: user_management.roles.view')}
      />,
    );
    openSingle();
    await waitFor(() => expect(screen.getByTestId('select-load-failure')).toBeInTheDocument());
    fireEvent.click(screen.getByText('All roles'));
    expect(onChange).toHaveBeenCalledWith('all');
  });

  it('async: a rejected fetchOptions shows the failure and Retry runs it again', async () => {
    const fetchOptions = vi
      .fn()
      .mockRejectedValueOnce(new Error('Server error.'))
      .mockResolvedValueOnce([{ value: 'u1', label: 'Mei Ling' }]);
    render(<SearchableSelect value="" onChange={vi.fn()} fetchOptions={fetchOptions} />);
    openSingle();
    const retry = await screen.findByRole('button', { name: /retry/i });
    expect(screen.queryByText('No results found.')).not.toBeInTheDocument();
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText('Mei Ling')).toBeInTheDocument());
    expect(fetchOptions).toHaveBeenCalledTimes(2);
  });

  it('async: a refused fetchOptions reads as no access', async () => {
    const fetchOptions = vi.fn().mockRejectedValue(new Error('Module not enabled: projects'));
    render(<SearchableSelect value="" onChange={vi.fn()} fetchOptions={fetchOptions} />);
    openSingle();
    await waitFor(() =>
      expect(screen.getByTestId('select-load-failure')).toHaveTextContent(/don.t have access/i),
    );
  });
});

describe('SearchableMultiSelect load failure', () => {
  it('static: a failure offers Retry instead of "No results found."', async () => {
    const onRetry = vi.fn();
    render(
      <SearchableMultiSelect
        value={[]}
        onChange={vi.fn()}
        options={[]}
        loadError={new Error('Server error.')}
        onRetry={onRetry}
      />,
    );
    openMulti();
    const retry = await screen.findByRole('button', { name: /retry/i });
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('No results found.')).not.toBeInTheDocument();
  });

  it('static: a refusal reads as no access', async () => {
    render(
      <SearchableMultiSelect
        value={[]}
        onChange={vi.fn()}
        options={[]}
        loadError={new Error('Permission required: user_management.reference_data.view')}
      />,
    );
    openMulti();
    await waitFor(() =>
      expect(screen.getByTestId('select-load-failure')).toHaveTextContent(/don.t have access/i),
    );
  });

  it('async: a rejected fetchOptions shows Retry', async () => {
    const fetchOptions = vi
      .fn()
      .mockRejectedValueOnce(new Error('Server error.'))
      .mockResolvedValueOnce([{ value: 'x', label: 'Xenon' }]);
    render(<SearchableMultiSelect value={[]} onChange={vi.fn()} fetchOptions={fetchOptions} />);
    openMulti();
    fireEvent.click(await screen.findByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByText('Xenon')).toBeInTheDocument());
  });
});
