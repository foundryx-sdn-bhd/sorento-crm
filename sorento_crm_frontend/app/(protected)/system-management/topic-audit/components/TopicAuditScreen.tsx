'use client';

import { useMemo, useState } from 'react';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table';
import { Alert, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { PageHeader } from '@/components/common/PageHeader';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { cn } from '@/lib/utils';
import { formatDateSafe } from '@/lib/helpers';
import { useTopicAuditQuery } from '@/hooks/useTopicAuditQuery';
import {
  useDrawTopicAuditSample,
  useSaveTopicAuditVerdict,
} from '@/hooks/useTopicAuditMutations';
import type {
  AuditShow,
  AuditVerdict,
  TopicAudit,
  TopicAuditRow,
  TopicAuditTopic,
  TopicAuditVerdictBody,
} from '@/services/topicAuditService';

// The Container's own classes: the Container reads a settings provider this page does not need.
const PAGE_WIDTH = 'w-full mx-auto px-4 lg:px-6 max-w-[1920px]';

type Pending = { id: string; kind: 'wrong_topic' | 'wrong_subject' } | null;

const SHOWS: { value: AuditShow; label: string }[] = [
  { value: 'to_review', label: 'To review' },
  { value: 'reviewed', label: 'Reviewed' },
  { value: 'all', label: 'All' },
];

function percent(rate: number | null): string {
  return rate === null ? '-' : `${Math.round(rate * 100)}%`;
}

function Tile({
  testId,
  label,
  value,
  caption,
  below,
}: {
  testId: string;
  label: string;
  value: string;
  caption: string;
  below?: boolean;
}) {
  return (
    <Card data-testid={testId}>
      <CardContent className="space-y-1 py-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div
          className={cn(
            'text-2xl font-semibold tabular-nums',
            below && 'text-destructive',
          )}
        >
          {value}
        </div>
        <div className="text-xs text-muted-foreground">{caption}</div>
      </CardContent>
    </Card>
  );
}

function Tiles({ data }: { data: TopicAudit }) {
  const { rates, targets } = data;
  const under = (rate: number | null, target: number) =>
    rate !== null && rate < target;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        testId="audit-tile-reviewed"
        label="Reviewed"
        value={`${data.reviewed} / ${data.sample_size}`}
        caption={`${Math.max(data.sample_size - data.reviewed, 0)} to go`}
      />
      <Tile
        testId="audit-tile-subject"
        label="Subject right"
        value={percent(rates.subject)}
        caption={`Target ${percent(targets.subject)}`}
        below={under(rates.subject, targets.subject)}
      />
      <Tile
        testId="audit-tile-topic"
        label="Topic right"
        value={percent(rates.topic)}
        caption={`Target ${percent(targets.topic)}`}
        below={under(rates.topic, targets.topic)}
      />
      <Tile
        testId="audit-tile-product"
        label="Product right"
        value={percent(rates.product)}
        caption={`Target ${percent(targets.product)}`}
        below={under(rates.product, targets.product)}
      />
    </div>
  );
}

function EmptyState({
  heading,
  hint,
  testId,
}: {
  heading: string;
  hint: string;
  testId?: string;
}) {
  return (
    <Card>
      <CardContent
        data-testid={testId}
        className="flex flex-col items-center gap-1 py-10 text-center"
      >
        <h3 className="text-base font-semibold">{heading}</h3>
        <p className="text-sm text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  );
}

function VerdictCell({
  row,
  topics,
  pending,
  setPending,
  onSave,
  busy,
}: {
  row: TopicAuditRow;
  topics: TopicAuditTopic[];
  pending: Pending;
  setPending: (p: Pending) => void;
  onSave: (id: string, body: TopicAuditVerdictBody) => void;
  busy: boolean;
}) {
  const subjectLabels = useMemo(
    () =>
      new Map(
        topics
          .filter((t) => t.parent_code === null)
          .map((t) => [t.code, t.label]),
      ),
    [topics],
  );
  const leaves = topics.filter((t) => t.parent_code !== null);
  const mine = pending?.id === row.id ? pending.kind : null;
  const options = (
    mine === 'wrong_topic'
      ? leaves.filter((t) => t.parent_code === row.subject)
      : leaves
  ).map((t) => ({
    value: t.code,
    label: t.label,
    group:
      mine === 'wrong_subject'
        ? subjectLabels.get(t.parent_code ?? '')
        : undefined,
  }));

  const verdictButton = (verdict: AuditVerdict, label: string) => {
    const active = row.verdict === verdict;
    return (
      <Button
        key={verdict}
        size="sm"
        variant={active ? 'primary' : 'outline'}
        disabled={busy}
        aria-pressed={active}
        onClick={() => {
          if (verdict === 'right') {
            setPending(null);
            onSave(row.id, { verdict: 'right' });
          } else {
            setPending({ id: row.id, kind: verdict });
          }
        }}
      >
        {label}
      </Button>
    );
  };

  const productButton = (value: 'right' | 'wrong', label: string) => (
    <Button
      key={value}
      size="sm"
      variant={row.products_verdict === value ? 'primary' : 'outline'}
      disabled={busy}
      aria-pressed={row.products_verdict === value}
      aria-label={`Products ${value}`}
      onClick={() => onSave(row.id, { products_verdict: value })}
    >
      {label}
    </Button>
  );

  return (
    <div className="space-y-1.5 py-1">
      <div className="flex flex-wrap gap-1">
        {verdictButton('right', 'Right')}
        {verdictButton('wrong_topic', 'Wrong topic')}
        {verdictButton('wrong_subject', 'Wrong subject')}
      </div>
      {mine && (
        <SearchableSelect
          value=""
          onChange={(code) => {
            if (!code) return;
            setPending(null);
            onSave(row.id, { verdict: mine, corrected_topic: code });
          }}
          options={options}
          clearable
          placeholder={
            mine === 'wrong_topic'
              ? 'Topic it should be'
              : 'Subject and topic it should be'
          }
          aria-label="Corrected topic"
        />
      )}
      {row.products.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-xs text-muted-foreground">Products</span>
          {productButton('right', 'Right')}
          {productButton('wrong', 'Wrong')}
        </div>
      )}
      {(row.corrected_topic_label || row.reviewed_by_name) && (
        <div className="text-xs text-muted-foreground">
          {row.corrected_topic_label
            ? `Should be ${row.corrected_topic_label}`
            : ''}
          {row.corrected_topic_label && row.reviewed_by_name ? ' - ' : ''}
          {row.reviewed_by_name ? `by ${row.reviewed_by_name}` : ''}
        </div>
      )}
    </div>
  );
}

export default function TopicAuditScreen() {
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [show, setShow] = useState<AuditShow>('to_review');
  const [subject, setSubject] = useState('');
  const [pending, setPending] = useState<Pending>(null);

  const query = useTopicAuditQuery({
    version,
    show,
    subject: subject || undefined,
  });
  const draw = useDrawTopicAuditSample();
  const save = useSaveTopicAuditVerdict();
  const data = query.data;
  const topics = useMemo(() => data?.topics ?? [], [data]);

  const onSave = (id: string, body: TopicAuditVerdictBody) =>
    save.mutate({ id, body });

  const columns = useMemo<ColumnDef<TopicAuditRow>[]>(
    () => [
      {
        id: 'asked_at',
        accessorFn: (r) => r.asked_at,
        header: ({ column }) => (
          <DataGridColumnHeader title="Asked on" column={column} />
        ),
        cell: ({ row }) => formatDateSafe(row.original.asked_at),
        size: 110,
        enableSorting: false,
      },
      {
        id: 'message',
        accessorFn: (r) => r.message,
        header: ({ column }) => (
          <DataGridColumnHeader title="Message" column={column} />
        ),
        cell: ({ row }) => (
          <div className="truncate" title={row.original.message}>
            {row.original.message}
          </div>
        ),
        size: 280,
        enableSorting: false,
      },
      {
        id: 'subject',
        accessorFn: (r) => r.subject_label,
        header: ({ column }) => (
          <DataGridColumnHeader title="Subject" column={column} />
        ),
        cell: ({ row }) => (
          <div className="truncate" title={row.original.subject_label}>
            {row.original.subject_label}
          </div>
        ),
        size: 130,
        enableSorting: false,
      },
      {
        id: 'topic',
        accessorFn: (r) => r.topic_label,
        header: ({ column }) => (
          <DataGridColumnHeader title="Topic" column={column} />
        ),
        cell: ({ row }) => (
          <div className="truncate" title={row.original.topic_label}>
            {row.original.topic_label}
          </div>
        ),
        size: 170,
        enableSorting: false,
      },
      {
        id: 'confidence',
        accessorFn: (r) => r.confidence,
        header: ({ column }) => (
          <DataGridColumnHeader title="Sure" column={column} />
        ),
        cell: ({ row }) =>
          row.original.confidence === null
            ? '-'
            : row.original.confidence.toFixed(2),
        size: 80,
        enableSorting: false,
      },
      {
        id: 'products',
        accessorFn: (r) => r.products.join(', '),
        header: ({ column }) => (
          <DataGridColumnHeader title="Products" column={column} />
        ),
        cell: ({ row }) => {
          const text = row.original.products.join(', ');
          return (
            <div className="truncate" title={text}>
              {text || '-'}
            </div>
          );
        },
        size: 150,
        enableSorting: false,
      },
      {
        id: 'verdict',
        header: ({ column }) => (
          <DataGridColumnHeader title="Verdict" column={column} />
        ),
        cell: ({ row }) => (
          <VerdictCell
            row={row.original}
            topics={topics}
            pending={pending}
            setPending={setPending}
            onSave={onSave}
            busy={save.isPending}
          />
        ),
        size: 330,
        enableSorting: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [topics, pending, save.isPending],
  );

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
  });

  const subjectOptions = topics
    .filter((t) => t.parent_code === null)
    .map((t) => ({ value: t.code, label: t.label }));
  const versionOptions = (data?.versions ?? []).map((v) => ({
    value: String(v),
    label: `Version ${v}`,
  }));
  const drawn = (data?.sample_size ?? 0) > 0;

  return (
    <>
      <div className={PAGE_WIDTH}>
        <PageHeader
          title="Topic audit"
          actions={
            <Button
              disabled={!data || drawn || draw.isPending}
              onClick={() => data && draw.mutate(data.version)}
            >
              Draw sample
            </Button>
          }
        />
      </div>
      <div className={PAGE_WIDTH}>
        <div className="space-y-4">
          {query.isError && (
            <Alert variant="destructive">
              <AlertTitle>{query.error.message}</AlertTitle>
            </Alert>
          )}
          {save.isError && (
            <Alert variant="destructive">
              <AlertTitle>{save.error.message}</AlertTitle>
            </Alert>
          )}
          {query.isLoading && <Skeleton className="h-64 w-full" />}
          {data && (
            <>
              <div className="flex flex-wrap items-end gap-3">
                <div className="w-44">
                  <SearchableSelect
                    value={String(data.version)}
                    onChange={(v) => v && setVersion(Number(v))}
                    options={versionOptions}
                    aria-label="Taxonomy version"
                  />
                </div>
                <ToggleGroup
                  type="single"
                  variant="outline"
                  value={show}
                  onValueChange={(v) => v && setShow(v as AuditShow)}
                  aria-label="Show"
                >
                  {SHOWS.map((s) => (
                    <ToggleGroupItem key={s.value} value={s.value}>
                      {s.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <div className="w-52">
                  <SearchableSelect
                    value={subject}
                    onChange={setSubject}
                    options={subjectOptions}
                    clearable
                    placeholder="All subjects"
                    aria-label="Subject"
                  />
                </div>
              </div>
              {!drawn ? (
                <EmptyState
                  testId="topic-audit-empty"
                  heading={`No sample for taxonomy v${data.version} yet`}
                  hint="A sample is 300 labelled asks, drawn once per taxonomy version."
                />
              ) : (
                <>
                  <Tiles data={data} />
                  {rows.length === 0 ? (
                    <EmptyState
                      heading="Nothing to show"
                      hint="No rows match these filters."
                    />
                  ) : (
                    <DataGrid
                      table={table}
                      recordCount={rows.length}
                      isLoading={false}
                      listingKey={null}
                      tableLayout={{
                        width: 'fixed',
                        columnsResizable: true,
                        columnsVisibility: false,
                      }}
                      tableClassNames={{ bodyRow: 'align-top' }}
                    >
                      <Card>
                        <CardHeader className="block">
                          <h3 className="text-sm font-semibold">
                            Sample, taxonomy v{data.version}
                          </h3>
                        </CardHeader>
                        <CardTable>
                          <DataGridTable />
                        </CardTable>
                      </Card>
                    </DataGrid>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
