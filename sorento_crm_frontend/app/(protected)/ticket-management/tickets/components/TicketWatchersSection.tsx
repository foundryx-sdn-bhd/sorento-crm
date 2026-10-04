'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { getUserLookup, type UserLookupItem } from '@/services/userSelectService';
import {
  addTicketWatchers,
  removeTicketWatcher,
} from '../services/ticketService';
import type { Ticket, TicketWatcherRef } from '../types/ticket.types';

interface Props {
  ticket: Ticket;
  onChange: (updated: Ticket) => void;
}

export default function TicketWatchersSection({ ticket, onChange }: Props) {
  const [users, setUsers] = useState<UserLookupItem[]>([]);
  const [adding, setAdding] = useState(false);
  const [pendingUserId, setPendingUserId] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);

  const loadUsers = useCallback(() => {
    getUserLookup()
      .then((rows) => {
        setUsers(rows);
        setLoadError(null);
      })
      .catch(setLoadError);
  }, []);

  // Lazy-load user list when the picker is first opened.
  useEffect(() => {
    if (!adding || users.length > 0) return;
    loadUsers();
  }, [adding, users.length, loadUsers]);

  const watcherIds = useMemo(
    () => new Set(ticket.watchers.map((w) => w.user_id)),
    [ticket.watchers],
  );

  const options: SearchableSelectOption[] = useMemo(
    () =>
      users
        .filter((u) => !watcherIds.has(u.id))
        .map((u) => ({
          value: u.id,
          label: u.name || 'Unnamed user',
          searchText: u.name ?? '',
        })),
    [users, watcherIds],
  );

  async function add(userId: string) {
    if (!userId) return;
    setBusy(true);
    try {
      const updated = await addTicketWatchers(ticket.id, { user_ids: [userId] });
      onChange(updated);
      setPendingUserId('');
      setAdding(false);
      toast.success('Watcher added');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(userId: string) {
    setBusy(true);
    try {
      await removeTicketWatcher(ticket.id, userId);
      onChange({
        ...ticket,
        watchers: ticket.watchers.filter((w) => w.user_id !== userId),
      });
      toast.success('Watcher removed');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-semibold uppercase text-muted-foreground">
          Watchers ({ticket.watchers.length})
        </h4>
        {!adding && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={() => setAdding(true)}
          >
            + Add
          </Button>
        )}
      </div>

      {adding && (
        <div className="flex items-center gap-2 mb-2">
          <SearchableSelect
            value={pendingUserId}
            onChange={(v) => add(v)}
            options={options}
            loadError={loadError}
            onRetry={loadUsers}
            placeholder={
              users.length === 0
                ? 'Loading users…'
                : options.length === 0
                ? 'All users are already watchers'
                : 'Pick a user'
            }
            disabled={busy}
            size="sm"
            triggerClassName="text-sm"
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setAdding(false);
              setPendingUserId('');
            }}
            disabled={busy}
          >
            Cancel
          </Button>
        </div>
      )}

      {ticket.watchers.length === 0 ? (
        <span className="text-sm text-muted-foreground">None.</span>
      ) : (
        <ul className="flex flex-col gap-1">
          {ticket.watchers.map((w: TicketWatcherRef) => (
            <li
              key={w.user_id}
              className="flex items-center justify-between text-sm"
            >
              <span className="truncate">{w.display_name ?? w.user_id}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-muted-foreground hover:text-destructive"
                disabled={busy}
                onClick={() => remove(w.user_id)}
                aria-label="Remove watcher"
              >
                <X className="size-3" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
