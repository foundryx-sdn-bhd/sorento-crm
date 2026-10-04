'use client';

import { useCallback, useEffect, useState } from 'react';
import { toast } from '@/lib/toast';
import {
  getPortalIdea,
  listPortalComments,
  PortalIdeaNotFoundError,
  postPortalComment,
} from '@/services/portalIdeasService';
import type { PortalIdea, PortalIdeaComment } from '@/types/ideas';

/**
 * The track page's state: plain state over the service (a portal token, not the CRM session, so
 * no query client), like the other portal pages. An unknown token is `notFound`, any other
 * failure is `error`.
 */
export function usePortalIdea(token: string) {
  const [idea, setIdea] = useState<PortalIdea | null>(null);
  const [comments, setComments] = useState<PortalIdeaComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setNotFound(false);
    setError(null);
    Promise.all([getPortalIdea(token), listPortalComments(token)])
      .then(([loadedIdea, loadedComments]) => {
        if (cancelled) return;
        setIdea(loadedIdea);
        setComments(loadedComments);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        if (e instanceof PortalIdeaNotFoundError) setNotFound(true);
        else setError(e.message || 'Could not load this page.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const post = useCallback(
    async (body: string) => {
      setPosting(true);
      try {
        const created = await postPortalComment(token, body);
        setComments((current) => [...current, created]);
        return true;
      } catch (e) {
        toast.error((e as Error).message || 'Could not post your comment');
        return false;
      } finally {
        setPosting(false);
      }
    },
    [token],
  );

  return { idea, comments, loading, notFound, error, posting, post };
}
