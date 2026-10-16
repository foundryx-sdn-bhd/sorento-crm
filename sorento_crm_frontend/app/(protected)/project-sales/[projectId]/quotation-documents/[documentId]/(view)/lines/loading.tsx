import { ListPageSkeleton } from '@/components/common/ListPageSkeleton';

/**
 * Held while this segment's chunk and first page arrive (M5-01). `bodyOnly`: the
 * parent layout already renders the header for this segment (M5-01 review B1) -
 * see `loading-inventory.test.tsx`'s BODY_ONLY_SEGMENTS map for the exact reason.
 */
export default function Loading() {
  return <ListPageSkeleton bodyOnly />;
}
