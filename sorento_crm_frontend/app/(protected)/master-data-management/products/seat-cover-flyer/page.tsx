import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import SeatCoverFlyerReview from '../components/SeatCoverFlyerReview';

export const metadata: Metadata = {
  title: 'Seat Cover Flyer',
  description: 'Link WC SKUs to the seat covers the flyer prints for them.',
};

export default async function SeatCoverFlyerPage() {
  return (
    <>
      <Container>
        <PageHeader title="Seat Cover Flyer" />
      </Container>

      <Container>
        <SeatCoverFlyerReview />
      </Container>
    </>
  );
}
