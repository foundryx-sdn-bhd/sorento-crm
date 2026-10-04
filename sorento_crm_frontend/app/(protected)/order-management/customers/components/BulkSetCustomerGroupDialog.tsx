'use client';

import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { searchCustomerGroupsSelect } from '../../customer-groups/services/customerGroupService';
import { useAssignCustomerGroup } from '../hooks/useAssignCustomerGroup';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  customerIds: string[];
  onDone: () => void;
};

// Stands in for the select's value while a not-yet-created group name is pending.
const NEW_GROUP = '__new_group__';

export function BulkSetCustomerGroupDialog({ open, onOpenChange, customerIds, onDone }: Props) {
  const [group, setGroup] = useState<{ id: string; name: string } | null>(null);
  const [newName, setNewName] = useState<string | null>(null);
  const assign = useAssignCustomerGroup();
  // Names of the groups the select has listed, so a picked id can be shown and toasted by name.
  const names = useRef(new Map<string, string>());

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setGroup(null);
      setNewName(null);
    }
    onOpenChange(next);
  };

  const value = newName ? NEW_GROUP : (group?.id ?? '');
  const selectedOption = newName
    ? { value: NEW_GROUP, label: newName }
    : group
      ? { value: group.id, label: group.name }
      : undefined;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Set customer group</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <SearchableSelect
            value={value}
            onChange={(v) => {
              if (!v) {
                setGroup(null);
                setNewName(null);
              } else if (v !== NEW_GROUP) {
                setGroup({ id: v, name: names.current.get(v) ?? 'selected group' });
                setNewName(null);
              }
            }}
            fetchOptions={async (query) =>
              (await searchCustomerGroupsSelect(query)).map((g) => {
                names.current.set(g.id, g.name);
                return { value: g.id, label: g.name };
              })
            }
            selectedOption={selectedOption}
            createOption={{
              label: (q) => `Create group "${q}"`,
              onCreate: (name) => {
                setNewName(name);
                setGroup(null);
              },
            }}
            placeholder="Select customer group"
            emptyMessage="No groups match."
            clearable
          />
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={(!group && !newName) || assign.isPending}
            onClick={() =>
              assign.mutate(
                { customerIds, group: group ?? undefined, newName: newName ?? undefined },
                {
                  onSuccess: onDone,
                  onSettled: () => handleOpenChange(false),
                },
              )
            }
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
