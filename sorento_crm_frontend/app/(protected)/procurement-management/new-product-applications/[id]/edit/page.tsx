'use client';

import Link from 'next/link';
import { use } from 'react';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { Button } from '@/components/ui/button';
import NewProductApplicationForm from '../../components/NewProductApplicationForm';

export default function EditNewProductApplicationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <Container>
      <PageHeader
        title="Edit New Product Application"
        actions={
          <Button variant="outline" asChild>
            <Link
              href={`/procurement-management/new-product-applications/${id}`}
            >
              Cancel
            </Link>
          </Button>
        }
      />
      <div className="mt-6">
        <NewProductApplicationForm applicationId={id} />
      </div>
    </Container>
  );
}
