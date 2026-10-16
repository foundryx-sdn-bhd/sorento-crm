'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Edit, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useSLAPolicy } from '../hooks/useSLAPolicies';
import { formatDate } from '@/lib/helpers';
import { useHasPermission } from '@/hooks/usePermissions';
import SLAPolicyDeleteDialog from './sla-policy-delete-dialog';
import SLAPolicyTiersTable from './SLAPolicyTiersTable';

interface SLAPolicyDetailProps {
  slaPolicyId: string;
}

export default function SLAPolicyDetail({ slaPolicyId }: SLAPolicyDetailProps) {
  const router = useRouter();
  const { data: slaPolicy, isLoading } = useSLAPolicy(slaPolicyId);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const canEdit = useHasPermission('sla_management.sla_policies.edit');
  const canDelete = useHasPermission('sla_management.sla_policies.delete');

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!slaPolicy) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">SLA policy not found</p>
        <Button variant="outline" onClick={() => router.push('/sla-management/sla-policies')} className="mt-4">
          Back to SLA Policies
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold">{slaPolicy.name}</h2>
            <Badge variant={slaPolicy.is_active ? 'success' : 'secondary'}>
              <BadgeDot />
              {slaPolicy.is_active ? 'Active' : 'Inactive'}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Code: {slaPolicy.code}
          </p>
        </div>
        <div className="flex gap-2">
          {canEdit && (
            <Button variant="outline" onClick={() => router.push(`/sla-management/sla-policies/${slaPolicyId}/edit`)}>
              <Edit className="size-4" />
              Edit
            </Button>
          )}
          {canDelete && (
            <Button variant="destructive" onClick={() => setDeleteDialogOpen(true)}>
              <Trash2 className="size-4" />
              Delete
            </Button>
          )}
        </div>
      </div>

      {/* SLA Policy Information */}
      <Card>
        <CardHeader>
          <CardTitle>SLA Policy Information</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="text-sm text-muted-foreground">Code</p>
              <p className="font-medium">{slaPolicy.code}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Name</p>
              <p className="font-medium">{slaPolicy.name}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Status</p>
              <Badge variant={slaPolicy.is_active ? 'success' : 'secondary'}>
                <BadgeDot />
                {slaPolicy.is_active ? 'Active' : 'Inactive'}
              </Badge>
            </div>
            {slaPolicy.description && (
              <div className="md:col-span-2">
                <p className="text-sm text-muted-foreground">Description</p>
                <p className="font-medium">{slaPolicy.description}</p>
              </div>
            )}
            <div>
              <p className="text-sm text-muted-foreground">Created At</p>
              <p className="font-medium">{formatDate(new Date(slaPolicy.created_at))}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Updated At</p>
              <p className="font-medium">{formatDate(new Date(slaPolicy.updated_at))}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* SLA Policy Tiers Table */}
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>SLA Policy Tiers</CardTitle>
        </CardHeader>
        <SLAPolicyTiersTable policyId={slaPolicyId} />
      </Card>

      {/* Delete Dialog */}
      {slaPolicy && canDelete && (
        <SLAPolicyDeleteDialog
          open={deleteDialogOpen}
          closeDialog={() => setDeleteDialogOpen(false)}
          slaPolicy={slaPolicy}
          onSuccess={() => {
            router.push('/sla-management/sla-policies');
          }}
        />
      )}
    </div>
  );
}
