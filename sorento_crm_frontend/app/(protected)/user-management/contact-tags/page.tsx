import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import ContactTagsAdmin from './components/ContactTagsAdmin';

export const metadata: Metadata = {
  title: 'Contact Tags',
};

export default async function Page() {
  return (
    <>
      <Container>
        <PageHeader title="Contact Tags" />
      </Container>
      <Container>
        <ContactTagsAdmin />
      </Container>
    </>
  );
}
