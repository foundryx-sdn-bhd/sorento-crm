'use client';

import Link from 'next/link';
import { use } from 'react';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { Button } from '@/components/ui/button';
import SampleRequestForm from '../../components/SampleRequestForm';

export default function EditSampleRequestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Container>
      <PageHeader
        title="Edit Sample Request"
        actions={
          <Button variant="outline" asChild>
            <Link href={`/project-sales/sample-requests/${id}`}>Cancel</Link>
          </Button>
        }
      />
      <div className="mt-6">
        <SampleRequestForm requestId={id} />
      </div>
    </Container>
  );
}
