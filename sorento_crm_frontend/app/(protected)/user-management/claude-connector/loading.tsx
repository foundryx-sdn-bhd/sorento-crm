import { ListPageSkeleton } from '@/components/common/ListPageSkeleton';

/** Held while this list's chunk and first page arrive (S7-04). */
export default function Loading() {
  return <ListPageSkeleton />;
}
