/**
 * The auto-collect setting reaches the FE (r9 S3/D10, AC-S3-7).
 *
 * LESSONS: a new settings column reaches the frontend only if it is in the
 * MANUAL mapper too. `mapSettingsFromApi` in `layout.tsx` is that mapper, and a
 * column missing from it looks exactly like a backend that never saved the
 * value.
 *
 * The Phase 1 per-tab override (`readAutoCollectDaysOverride`) went with the
 * column landing, which is the point: a real answer of 0 ("the sweep is off")
 * is falsy, so a fallback consulted after the column exists would quietly
 * overwrite it.
 */
import { describe, it, expect } from 'vitest';

import { AUTO_COLLECT_DAYS_DEFAULT } from '@/lib/dealer-kit/print-collection';
import { mapSettingsFromApi } from './layout';

describe('mapSettingsFromApi (AC-S3-7)', () => {
  it('carries the auto-collect days the server sent', () => {
    const mapped = mapSettingsFromApi({
      name: 'ZZT Co',
      price_tag_auto_collect_days: 14,
    } as never);

    expect(mapped.priceTagAutoCollectDays).toBe(14);
  });

  it('keeps 0, which means the sweep is off and is NOT "no answer"', () => {
    const mapped = mapSettingsFromApi({
      name: 'ZZT Co',
      price_tag_auto_collect_days: 0,
    } as never);

    expect(mapped.priceTagAutoCollectDays).toBe(0);
  });

  it('falls back to the shipped default when the row predates the column', () => {
    const mapped = mapSettingsFromApi({ name: 'ZZT Co' } as never);

    expect(mapped.priceTagAutoCollectDays).toBe(AUTO_COLLECT_DAYS_DEFAULT);
  });
});

describe('mapSettingsFromApi chatbot file link expiry', () => {
  it('carries the number of days the server sent', () => {
    const mapped = mapSettingsFromApi({
      name: 'ZZT Co',
      chatbot_file_link_expiry_days: 30,
    } as never);

    expect(mapped?.chatbotFileLinkExpiryDays).toBe(30);
  });

  it('keeps null, which means the links never expire', () => {
    const mapped = mapSettingsFromApi({
      name: 'ZZT Co',
      chatbot_file_link_expiry_days: null,
    } as never);

    expect(mapped?.chatbotFileLinkExpiryDays).toBeNull();
  });

  it('reads a row that predates the column as never expires', () => {
    const mapped = mapSettingsFromApi({ name: 'ZZT Co' } as never);

    expect(mapped?.chatbotFileLinkExpiryDays).toBeNull();
  });
});
