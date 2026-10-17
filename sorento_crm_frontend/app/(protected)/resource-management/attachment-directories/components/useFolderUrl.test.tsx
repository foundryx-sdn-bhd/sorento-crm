/**
 * useFolderUrl - the open folder lives in the URL (FILES-URL-NAV AC-1..AC-5, AC-10).
 *
 * Back / Forward are modelled by changing the mocked search params and re-rendering:
 * the hook must follow the URL, never hold a stale copy of it.
 *
 * Crew audit blocker (17 Oct, head 6c516ad0): open A > B > C, RELOAD, Back, Back, open a
 * folder, Back -> the page sat on Next's loading skeleton for good. Native
 * history.pushState copies the CURRENT entry's Next router tree into every entry it
 * writes; after a reload those trees name page segments the new document never loaded,
 * and Next's popstate restore suspends on them forever. So every history write must go
 * through the Next router, and the folder shows at once while that navigation lands.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const push = vi.fn();
const replace = vi.fn();
let search = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace }),
  usePathname: () => '/resource-management/attachment-directories',
  useSearchParams: () => new URLSearchParams(search),
}));

import { useFolderUrl, useOnFolderChange } from './useFolderUrl';

const BASE = '/resource-management/attachment-directories';
let nativePush: ReturnType<typeof vi.spyOn>;
let nativeReplace: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  push.mockReset();
  replace.mockReset();
  nativePush = vi.spyOn(window.history, 'pushState');
  nativeReplace = vi.spyOn(window.history, 'replaceState');
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
    search = 'directoryId=camp';
    rerender();
    expect(result.current.folderId).toBe('camp');
    search = '';
    rerender();
    expect(result.current.folderId).toBeNull();
  });

  it('blocker: opening a folder navigates through the Next router, never native history', () => {
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('mkt'));
    expect(push).toHaveBeenCalledWith(`${BASE}?directoryId=mkt`, { scroll: false });
    expect(nativePush).not.toHaveBeenCalled();
    expect(nativeReplace).not.toHaveBeenCalled();
  });

  it('blocker: replace goes through router.replace, never native history', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('camp', { replace: true }));
    expect(replace).toHaveBeenCalledWith(`${BASE}?directoryId=camp`, { scroll: false });
    expect(push).not.toHaveBeenCalled();
    expect(nativePush).not.toHaveBeenCalled();
    expect(nativeReplace).not.toHaveBeenCalled();
  });

  it('shows the opened folder at once, before the router navigation lands', () => {
    search = 'directoryId=mkt';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('camp'));
    expect(result.current.folderId).toBe('camp');
  });

  it('once the URL lands, later URL changes (Back) are followed again', () => {
    search = 'directoryId=mkt';
    const { result, rerender } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('camp'));
    search = 'directoryId=camp';
    rerender();
    expect(result.current.folderId).toBe('camp');
    search = 'directoryId=mkt';
    rerender();
    expect(result.current.folderId).toBe('mkt');
  });

  it('Back / Forward while a navigation is still pending wins over it', () => {
    search = 'directoryId=camp';
    const { result, rerender } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('q1'));
    expect(result.current.folderId).toBe('q1');
    act(() => {
      search = 'directoryId=mkt';
      window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
    });
    rerender();
    expect(result.current.folderId).toBe('mkt');
  });

  it('any Back / Forward drops a pending folder, even onto the folder it started from', () => {
    search = 'directoryId=mkt';
    const { result, rerender } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('q1'));
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
    });
    rerender();
    expect(result.current.folderId).toBe('mkt');
  });

  it('a pending folder is dropped when another navigation discarded it and moved the URL', () => {
    // Next's action queue drops an in-flight navigation when a newer one starts (e.g. a
    // router.replace elsewhere on the page); the screen must follow the URL, not keep q1.
    search = 'directoryId=mkt';
    const { result, rerender } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('q1'));
    expect(result.current.folderId).toBe('q1');
    search = 'directoryId=camp';
    rerender();
    expect(result.current.folderId).toBe('camp');
  });

  it('a second open while the first is pending shows the second', () => {
    search = 'directoryId=mkt';
    const { result, rerender } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('camp'));
    act(() => result.current.openFolder('q1'));
    expect(result.current.folderId).toBe('q1');
    expect(push).toHaveBeenLastCalledWith(`${BASE}?directoryId=q1`, { scroll: false });
    search = 'directoryId=q1';
    rerender();
    expect(result.current.folderId).toBe('q1');
  });

  it('opening the root drops the param and keeps every other one', () => {
    search = 'directoryId=q1&foo=1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder(null));
    expect(push).toHaveBeenCalledWith(`${BASE}?foo=1`, { scroll: false });
  });

  it('opening the root with no other params lands on the bare path', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder(null));
    expect(push).toHaveBeenCalledWith(BASE, { scroll: false });
  });

  it('treats the folder-all sentinel as the root', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('folder-all'));
    expect(push).toHaveBeenCalledWith(BASE, { scroll: false });
  });

  it('opening the folder already open adds no history entry', () => {
    search = 'directoryId=q1';
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('q1'));
    expect(push).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it('url-encodes the folder id', () => {
    const { result } = renderHook(() => useFolderUrl());
    act(() => result.current.openFolder('a b&c'));
    expect(push).toHaveBeenCalledWith(`${BASE}?directoryId=a+b%26c`, { scroll: false });
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
