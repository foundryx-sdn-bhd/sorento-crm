import { Container } from '@/components/common/container';
import { Skeleton } from '@/components/ui/skeleton';

export default function Loading() {
  return (
    <Container>
      <Skeleton className="h-24 w-full" />
    </Container>
  );
}
