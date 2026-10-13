'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import LoadErrorState from '@/components/common/LoadErrorState';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import {
  usePortalChangelogQuery,
  usePortalFeedbackQuery,
  usePortalRoadmapQuery,
} from '@/hooks/useIdeasPortal';
import { formatDate } from '@/lib/helpers';
import Link from 'next/link';
import { PortalCardLink, PortalEmpty } from './PortalCardLink';

type PortalTab = 'roadmap' | 'feedback' | 'changelog';

const TABS: { value: PortalTab; label: string }[] = [
  { value: 'roadmap', label: 'Roadmap' },
  { value: 'feedback', label: 'Feedback' },
  { value: 'changelog', label: 'Changelog' },
];

const FEEDBACK_PAGE_SIZE = 25;

function parseTab(value: string | null): PortalTab {
  return TABS.some((t) => t.value === value) ? (value as PortalTab) : 'roadmap';
}

function errorMessage(error: unknown): string | undefined {
  return error instanceof Error ? error.message : undefined;
}

function RoadmapTab({ enabled }: { enabled: boolean }) {
  const { data, isLoading, isError, error, refetch, isFetching } = usePortalRoadmapQuery(enabled);
  if (isLoading) return <Skeleton className="h-64 w-full rounded-xl" />;
  if (isError || !data) {
    return (
      <Card>
        <LoadErrorState
          title="Could not load the roadmap"
          message={errorMessage(error)}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      </Card>
    );
  }
  if (data.columns.length === 0) {
    return <PortalEmpty title="Nothing on the roadmap yet" hint="Planned ideas appear here." />;
  }
  return (
    <div className="flex gap-4 overflow-x-auto pb-2">
      {data.columns.map((column) => (
        <section key={column.key} aria-label={column.title} className="flex w-72 shrink-0 flex-col gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">{column.title}</span>
            <Badge variant="secondary" size="sm">
              {column.count}
            </Badge>
          </div>
          {column.ideas.length === 0 ? (
            <p className="rounded-lg border border-dashed p-3 text-center text-xs text-muted-foreground">
              No ideas
            </p>
          ) : (
            column.ideas.map((card) => <PortalCardLink key={card.id} card={card} />)
          )}
        </section>
      ))}
    </div>
  );
}

function FeedbackTab({ enabled }: { enabled: boolean }) {
  const search = useDebouncedSearch('');
  const [sort, setSort] = useState<'top' | 'new'>('top');
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, error, refetch, isFetching } = usePortalFeedbackQuery(
    { q: search.debouncedValue || undefined, sort, mine, page, pageSize: FEEDBACK_PAGE_SIZE },
    enabled,
  );

  const pageCount = data ? Math.max(1, Math.ceil(data.total / (data.pageSize || FEEDBACK_PAGE_SIZE))) : 1;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="w-full sm:w-72"
          placeholder="Search feedback..."
          aria-label="Search feedback"
          value={search.value}
          onChange={(e) => {
            search.setValue(e.target.value);
            setPage(1);
          }}
        />
        <div className="flex items-center gap-1">
          {(['top', 'new'] as const).map((key) => (
            <Button
              key={key}
              type="button"
              size="sm"
              variant={sort === key ? 'primary' : 'outline'}
              aria-pressed={sort === key}
              onClick={() => {
                setSort(key);
                setPage(1);
              }}
            >
              {key === 'top' ? 'Top' : 'New'}
            </Button>
          ))}
        </div>
        <Button
          type="button"
          size="sm"
          variant={mine ? 'primary' : 'outline'}
          aria-pressed={mine}
          onClick={() => {
            setMine((v) => !v);
            setPage(1);
          }}
        >
          Mine
        </Button>
      </div>
      {isLoading ? (
        <Skeleton className="h-48 w-full rounded-xl" />
      ) : isError || !data ? (
        <Card>
          <LoadErrorState
            title="Could not load feedback"
            message={errorMessage(error)}
            onRetry={() => void refetch()}
            retrying={isFetching}
          />
        </Card>
      ) : data.items.length === 0 ? (
        <PortalEmpty title="No feedback yet" hint="Ideas that match appear here." />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {data.items.map((card) => (
              <PortalCardLink key={card.id} card={card} showStatus />
            ))}
          </div>
          {pageCount > 1 ? (
            <div className="flex items-center justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous
              </Button>
              <span className="text-xs tabular-nums text-muted-foreground">
                {page} / {pageCount}
              </span>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={page >= pageCount}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function ChangelogTab({ enabled }: { enabled: boolean }) {
  const { data, isLoading, isError, error, refetch, isFetching } = usePortalChangelogQuery(enabled);
  if (isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;
  if (isError || !data) {
    return (
      <Card>
        <LoadErrorState
          title="Could not load the changelog"
          message={errorMessage(error)}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      </Card>
    );
  }
  if (data.items.length === 0) {
    return <PortalEmpty title="Nothing shipped yet" hint="Delivered ideas appear here." />;
  }
  return (
    <ul className="flex flex-col divide-y rounded-lg border">
      {data.items.map((item) => (
        <li key={item.id} className="flex min-w-0 flex-col gap-1 px-4 py-3">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{item.ideaNumber}</span>
            <Link
              href={`/ideas/portal/${item.id}`}
              className="line-clamp-2 min-w-0 flex-1 break-words text-sm font-medium text-primary hover:underline"
              title={item.title ?? item.ideaNumber ?? undefined}
            >
              {item.title ?? item.ideaNumber}
            </Link>
            {item.deliveredAt ? (
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {formatDate(item.deliveredAt)}
              </span>
            ) : null}
          </div>
          {item.note ? <p className="whitespace-pre-wrap break-words text-sm">{item.note}</p> : null}
        </li>
      ))}
    </ul>
  );
}

/** The Roadmap portal: line tabs Roadmap / Feedback / Changelog, the open one kept in `?tab=`. */
export function IdeasPortal() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const urlTab = parseTab(searchParams.get('tab'));
  const [tab, setTab] = useState<PortalTab>(urlTab);

  // Back/forward or a link changes `?tab=` without going through `choose`.
  useEffect(() => setTab(urlTab), [urlTab]);

  // Radix reports a mouse press and the click that follows it; only the first one is a change.
  const choose = (next: PortalTab) => {
    if (next === tab) return;
    setTab(next);
    router.replace(`${pathname}?tab=${next}`, { scroll: false });
  };

  return (
    <Container>
      <Tabs value={tab} onValueChange={(v) => choose(parseTab(v))}>
        <TabsList variant="line" className="mb-5">
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value} onClick={() => choose(t.value)}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="roadmap">
          <RoadmapTab enabled={tab === 'roadmap'} />
        </TabsContent>
        <TabsContent value="feedback">
          <FeedbackTab enabled={tab === 'feedback'} />
        </TabsContent>
        <TabsContent value="changelog">
          <ChangelogTab enabled={tab === 'changelog'} />
        </TabsContent>
      </Tabs>
    </Container>
  );
}
