import { Container } from '@/components/common/container';
import { Skeleton } from '@/components/ui/skeleton';

export default function Loading() {
  return (
    <Container>
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-6 w-full max-w-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    </Container>
  );
}
