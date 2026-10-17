'use client';

/**
 * The open Files folder, held in the URL (`?directoryId=<id>`) rather than component state
 * (FILES-URL-NAV). With `useState` every folder click left the URL alone, so browser Back
 * left the Files page instead of going up a folder, and reload lost the folder.
 *
 * Same idiom as `useUrlTab`: the URL is the only copy, every other query param is carried
 * over verbatim, and the root writes no param at all, so the existing `?directoryId=` links
 * (quick access pins, attachment detail, product / promotion tabs) keep working.
 *
 * Unlike `useUrlTab` it writes with the native `history.pushState` / `replaceState`, which
 * Next syncs into `useSearchParams` without a server round trip. `router.push` re-requested
 * the page on every folder click (about 1s on the dev server, measured in the lane's browser
 * pass), where the old `useState` switch had been instant.
 */

import { useCallback, useState } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { FOLDER_ALL_ID } from '../constants';

export const DIRECTORY_PARAM = 'directoryId';

export type OpenFolder = (id: string | null, opts?: { replace?: boolean }) => void;

function normalise(id: string | null | undefined): string | null {
  return !id || id === FOLDER_ALL_ID ? null : id;
}

export function useFolderUrl(): { folderId: string | null; openFolder: OpenFolder } {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const folderId = normalise(searchParams?.get(DIRECTORY_PARAM));

  const openFolder = useCallback<OpenFolder>(
    (id, opts) => {
      const next = normalise(id);
      // Re-opening the open folder must not stack a duplicate Back step.
      if (next === folderId) return;
      const params = new URLSearchParams(searchParams?.toString());
      if (next) params.set(DIRECTORY_PARAM, next);
      else params.delete(DIRECTORY_PARAM);
      const qs = params.toString();
      const href = `${pathname}${qs ? `?${qs}` : ''}`;
      if (opts?.replace) window.history.replaceState(null, '', href);
      else window.history.pushState(null, '', href);
    },
    [folderId, pathname, searchParams]
  );

  return { folderId, openFolder };
}

/**
 * Calls `onChange` once whenever the folder changes, whatever changed it (a click, Back,
 * Forward, a pasted link), never on mount. Runs during render (React's derive-from-props
 * pattern) so the reset lands before the new folder's first fetch.
 */
export function useOnFolderChange(folderId: string | null, onChange: () => void): void {
  const [prev, setPrev] = useState(folderId);
  if (prev !== folderId) {
    setPrev(folderId);
    onChange();
  }
}
