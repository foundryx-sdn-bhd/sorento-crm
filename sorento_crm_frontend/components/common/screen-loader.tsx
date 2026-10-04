'use client';

import { LoaderCircleIcon } from 'lucide-react';
import { toAbsoluteUrl } from '@/lib/helpers';

/**
 * The app-boot splash - there is no page shape to draw a skeleton against
 * yet (the shell itself has not mounted), so the branding plus a spinner is
 * the whole indicator; a status word next to it added nothing a sighted user
 * could not already read from the motion (M5-02).
 */
export function ScreenLoader() {
  return (
    <div
      className="flex flex-col items-center gap-3 justify-center fixed inset-0 z-50"
      data-slot="screen-loader"
      data-loading=""
    >
      <img
        className="h-[30px] max-w-none"
        src={toAbsoluteUrl('/media/app/sorento-logo.svg')}
        alt="logo"
      />
      <LoaderCircleIcon className="size-4 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
    </div>
  );
}
