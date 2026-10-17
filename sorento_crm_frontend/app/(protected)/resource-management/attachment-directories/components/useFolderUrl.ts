'use client';

/**
 * The open Files folder, held in the URL (`?directoryId=<id>`) rather than component state
 * (FILES-URL-NAV). With `useState` every folder click left the URL alone, so browser Back
 * left the Files page instead of going up a folder, and reload lost the folder.
 *
 * Same idiom as `useUrlTab`: the URL is the source of truth, every other query param is
 * carried over verbatim, and the root writes no param at all, so the existing
 * `?directoryId=` links (quick access pins, attachment detail, product / promotion tabs)
 * keep working.
 *
 * Every history write goes through the Next router, never the native History API. Native
 * `pushState` copies the CURRENT entry's Next router tree into each entry it writes; after
 * a reload those trees name page segments the new document never loaded, and Back onto
 * one left the page on the route's loading skeleton for good (crew audit of PR #1759:
 * open A > B > C, reload, Back, Back, open D, Back).
 *
 * A router navigation is a server round trip (about 1s on the dev server), so the folder
 * the user opened is shown at once as `pending` and handed back to the URL as soon as the
 * URL moves at all: its own navigation landing, Back / Forward, or another navigation that
 * discarded it (Next's action queue drops an in-flight navigation when a newer one starts).
 * So the screen never keeps a folder the URL does not hold.
 */

import { startTransition, useCallback, useEffect, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { FOLDER_ALL_ID } from '../constants';

export const DIRECTORY_PARAM = 'directoryId';

export type OpenFolder = (id: string | null, opts?: { replace?: boolean }) => void;

function normalise(id: string | null | undefined): string | null {
  return !id || id === FOLDER_ALL_ID ? null : id;
}

export function useFolderUrl(): { folderId: string | null; openFolder: OpenFolder } {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const urlFolderId = normalise(searchParams?.get(DIRECTORY_PARAM));

  // The folder just opened, and the URL's folder at that moment; dropped once the URL
  // moves away from `fromUrl`.
  const [pending, setPending] = useState<{ id: string | null; fromUrl: string | null } | null>(
    null
  );
  if (pending && pending.fromUrl !== urlFolderId) setPending(null);

  // Back / Forward wins over a navigation still in flight, even when it lands on the URL
  // the pending one started from. A transition, like Next's own popstate restore, so the
  // screen does not show the pre-Back folder for a frame before the restore commits.
  useEffect(() => {
    const onPopState = () => startTransition(() => setPending(null));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const folderId = pending ? pending.id : urlFolderId;

  const openFolder = useCallback<OpenFolder>(
    (id, opts) => {
      const next = normalise(id);
      // Re-opening the shown folder must not stack a duplicate Back step.
      if (next === folderId) return;
      setPending({ id: next, fromUrl: urlFolderId });
      const params = new URLSearchParams(searchParams?.toString());
      if (next) params.set(DIRECTORY_PARAM, next);
      else params.delete(DIRECTORY_PARAM);
      const qs = params.toString();
      const href = `${pathname}${qs ? `?${qs}` : ''}`;
      if (opts?.replace) router.replace(href, { scroll: false });
      else router.push(href, { scroll: false });
    },
    [folderId, urlFolderId, pathname, router, searchParams]
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
