'use client';

import Link from 'next/link';
import { ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface AccessDeniedProps {
  title?: string;
  description?: string;
  /**
   * The in-place variant for a tab, panel, list or picker (NEVER-STUCK-UI S4.3): smaller,
   * no full-page height and no "Back to dashboard", because the rest of the screen is
   * still usable.
   */
  inline?: boolean;
}

const DEFAULT_TITLE = "You don't have access to this page";
const DEFAULT_DESCRIPTION =
  "Your role doesn't include permission for this area. If you think this is a mistake, ask an administrator to grant you access.";

/**
 * Shared "no access" state, rendered by page-level guards when the current user
 * lacks the required permission or superadmin role. Purely a UX surface - the
 * backend is the real enforcement (routes 403 for unauthorized principals).
 */
const INLINE_TITLE = "You don't have access to this";
const INLINE_DESCRIPTION = 'Your role does not include this. Ask an administrator if you need it.';

export default function AccessDenied({
  title,
  description,
  inline = false,
}: AccessDeniedProps) {
  if (inline) {
    return (
      <div
        data-slot="access-denied-inline"
        className="flex items-start gap-3 py-2 text-start"
      >
        <ShieldAlert className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">{title ?? INLINE_TITLE}</p>
          <p className="text-sm text-muted-foreground">{description ?? INLINE_DESCRIPTION}</p>
        </div>
      </div>
    );
  }
  title = title ?? DEFAULT_TITLE;
  description = description ?? DEFAULT_DESCRIPTION;
  return (
    <div
      className="flex min-h-[60vh] flex-col items-center justify-center px-4 py-10 text-center"
      // Read by the never-stuck smoke: a 403 on the page must end here, not in an empty state.
      data-access-denied=""
    >
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted">
        <ShieldAlert className="h-8 w-8 text-muted-foreground" />
      </div>
      <h1 className="mt-6 text-xl font-bold text-foreground">{title}</h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">{description}</p>
      <Button
        asChild
        className="mt-6 bg-destructive text-destructive-foreground hover:bg-destructive/90"
      >
        <Link href="/">Back to dashboard</Link>
      </Button>
    </div>
  );
}
