'use client';

import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { MoveLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import RequireAccess from '@/app/components/common/RequireAccess';
import SLAPolicyForm from '../components/SLAPolicyForm';

export default function NewSLAPolicyPage() {
  const router = useRouter();

  return (
    <RequireAccess permission="sla_management.sla_policies.add">
      <Container>
        <PageHeader
          title="Create SLA Policy"
          actions={
            <Button asChild variant="outline">
              <Link href="/sla-management/sla-policies">
                <MoveLeft /> Back to SLA policies
              </Link>
            </Button>
          }
        />
      </Container>
      <Container>
        <SLAPolicyForm
          onSuccess={() => {
            router.push('/sla-management/sla-policies');
          }}
        />
      </Container>
    </RequireAccess>
  );
}
