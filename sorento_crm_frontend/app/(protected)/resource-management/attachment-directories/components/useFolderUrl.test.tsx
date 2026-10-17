/**
 * useFolderUrl - the open folder lives in the URL (FILES-URL-NAV AC-1..AC-5, AC-10).
 *
 * Back / Forward are modelled by changing the mocked search params and re-rendering:
 * the hook must follow the URL, never hold a copy of it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

let push: ReturnType<typeof vi.spyOn>;
let replace: ReturnType<typeof vi.spyOn>;
let search = '';

vi.mock('next/navigation', () => ({
  usePathname: () => '/resource-management/attachment-directories',
  useSearchParams: () => new URLSearchParams(search),
}));

import { useFolderUrl, useOnFolderChange } from './useFolderUrl';

const BASE = '/resource-management/attachment-directories';

// The hook writes with the native History API (no server round trip); Next syncs it into
// useSearchParams, which these tests mock.
beforeEach(() => {
  push = vi.spyOn(window.history, 'pushState').mockImplementation(() => {});
  replace = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
  search = '';
});

afterEach(() => vi.restoreAllMocks());

describe('useFolderUrl', () => {
  it('reads the open folder from ?directoryId=', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    expect(result.current.folderId).toBe('q1');
  });

  it('is the drive root (null) with no param, an empty one, or the legacy folder-all sentinel', () => {
    for (const s of ['', 'directoryId=', 'directoryId=folder-all']) {
      search = s;
      const { result } = renderHook(() => useFolderUrl());
      expect(result.current.folderId).toBeNull();
    }
  });

  it('follows the URL when it changes underneath it (Back / Forward)', () => {
    search = 'directoryId=q1';
    const { result, rerender } = renderHook(() => useFolderUrl());
    expect(result.current.folderId).toBe('q1');
    search = 'directoryId=camp';
    rerender();
    expect(result.current.folderId).toBe('camp');
    search = '';
    rerender();
    expect(result.current.folderId).toBeNull();
  });

  it('opening a folder PUSHES a history entry with its id', () => {
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('mkt'));
    expect(push).toHaveBeenCalledWith(null, '', `${BASE}?directoryId=mkt`);
    expect(replace).not.toHaveBeenCalled();
  });

  it('opening the root drops the param and keeps every other one', () => {
    search = 'directoryId=q1&foo=1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder(null));
    expect(push).toHaveBeenCalledWith(null, '', `${BASE}?foo=1`);
  });

  it('opening the root with no other params lands on the bare path', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder(null));
    expect(push).toHaveBeenCalledWith(null, '', BASE);
  });

  it('treats the folder-all sentinel as the root', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('folder-all'));
    expect(push).toHaveBeenCalledWith(null, '', BASE);
  });

  it('opening the folder already open adds no history entry', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('q1'));
    expect(push).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it('replace: true rewrites the current entry instead of adding one', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('camp', { replace: true }));
    expect(replace).toHaveBeenCalledWith(null, '', `${BASE}?directoryId=camp`);
    expect(push).not.toHaveBeenCalled();
  });

  it('url-encodes the folder id', () => {
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('a b&c'));
    expect(push).toHaveBeenCalledWith(null, '', `${BASE}?directoryId=a+b%26c`);
  });
});

describe('useOnFolderChange (AC-9)', () => {
  it('does not fire on mount', () => {
    const onChange = vi.fn();
    renderHook(({ id }) => useOnFolderChange(id, onChange), { initialProps: { id: 'q1' as string | null } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('fires once per folder change, whatever caused it', () => {
    const onChange = vi.fn();
    const { rerender } = renderHook(({ id }) => useOnFolderChange(id, onChange), {
      initialProps: { id: 'q1' as string | null },
    });
    rerender({ id: 'q1' });
    expect(onChange).not.toHaveBeenCalled();
    rerender({ id: 'camp' });
    expect(onChange).toHaveBeenCalledTimes(1);
    rerender({ id: null });
    expect(onChange).toHaveBeenCalledTimes(2);
  });
});
