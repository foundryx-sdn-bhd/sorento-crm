'use client';

import { use } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { MoveLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import SupplierForm from '../../components/SupplierForm';

export default function EditSupplierPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const qs = searchParams.toString();
  const querySuffix = qs ? `?${qs}` : '';

  return (
    <>
      <Container>
        <PageHeader
          title="Edit Supplier"
          actions={
            <Button asChild variant="outline">
              <Link href={`/procurement-management/suppliers/${id}${querySuffix}`}>
                <MoveLeft /> Back to supplier
              </Link>
            </Button>
          }
        />
      </Container>

      <Container>
        <SupplierForm
          supplierId={id}
          onSuccess={() => {
            router.push(`/procurement-management/suppliers/${id}${querySuffix}`);
          }}
        />
      </Container>
    </>
  );
}
