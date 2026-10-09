'use client';

import { LoaderCircleIcon } from 'lucide-react';

/**
 * The app-boot splash - there is no page shape to draw a skeleton against
 * yet (the shell itself has not mounted), so the branding plus a spinner is
 * the whole indicator; a status word next to it added nothing a sighted user
 * could not already read from the motion (M5-02).
 *
 * The wordmark is text, not `sorento-logo.svg`: that file draws the word from
 * x=0 of a wider viewBox (the sidebar and header want it left-anchored), so its
 * box centre sat about 20px right of the word and the spinner lined up with the
 * box, not the word (LOADER-CENTER). Face, size, weight and colours match the
 * two logo SVGs.
 */
export function ScreenLoader() {
  return (
    <div
      className="flex flex-col items-center gap-3 justify-center fixed inset-0 z-50"
      data-slot="screen-loader"
      data-loading=""
    >
      <span
        className="font-[system-ui,-apple-system,BlinkMacSystemFont,'Segoe_UI',Roboto,sans-serif] text-[24px] font-semibold leading-none text-[#252F4A] dark:text-[#F4F4F5]"
        data-slot="screen-loader-wordmark"
      >
        Sorento
      </span>
      <LoaderCircleIcon className="size-4 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
    </div>
  );
}
