'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  type ColumnDef,
  type PaginationState,
  type SortingState,
  type VisibilityState,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import {
  ChartColumn,
  Check,
  Download,
  Settings2,
  TableProperties,
} from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { formatDateSafe, formatMoney2dp } from '@/lib/helpers';
import {
  useReportExport,
  useReportMeta,
  useReportRun,
  useReportViews,
} from '@/hooks/useReports';
import {
  reportLayoutListingKey,
  ReportCappedError,
  type ReportColumn,
  type ReportDetailLayout,
  type ReportParamMeta,
  type ReportParamValue,
  type ReportParamValues,
  type ReportPeriod,
  type ReportPivotLayout,
  type ReportRow,
  type ReportView,
  type ReportViewConfig,
} from '@/services/reportService';
import { ConfigureSummaryDialog, unavailableKeys } from './ConfigureSummaryDialog';
import { ReportPivotChart } from './ReportPivotChart';
import { ReportFilterBar } from './ReportFilterBar';
import { ReportPivotTable } from './ReportPivotTable';
import { ReportViewsMenu } from './ReportViewsMenu';

type PageState = {
  viewId: string | null;
  params: ReportParamValues;
  detail: ReportViewConfig['detail'];
  pivot: ReportViewConfig['pivot'];
  /** Bumped when a view is applied, so the grid drops the user's in-session columns. */
  token: number;
};

type GridState = {
  visibility: VisibilityState;
  order: string[];
  token: number;
};

/** A saved view names the GROUP SOURCE; the run result says which tick columns it became. */
function expandDetailColumns(
  keys: string[],
  layout: ReportDetailLayout,
): string[] {
  return keys.flatMap((key) => {
    const group = layout.column_groups.find((g) => g.source === key);
    return group ? group.keys : [key];
  });
}

/**
 * The inverse: the grid's leaf ids back to what a view (and an export) is allowed to name.
 *
 * A tick group's leaves are its member columns (`expected_delivery_year_1` .. `_4` on the
 * sponsorship report; a group that derives its members from the data has one per value
 * present). Neither the export nor a saved view may name one: the backend catalog has no
 * such column, and a view written in 2026 must still mean "show the delivery years" in
 * 2027. Hiding one member of a group therefore hides none of them - it is one choice.
 */
function collapseDetailColumns(
  keys: string[],
  layout: ReportDetailLayout,
): string[] {
  const out: string[] = [];
  for (const key of keys) {
    const group = layout.column_groups.find((g) => g.keys.includes(key));
    const id = group ? group.source : key;
    if (!out.includes(id)) out.push(id);
  }
  return out;
}

function gridStateFromView(
  detail: ReportViewConfig['detail'],
  layout: ReportDetailLayout,
): GridState {
  const visibleKeys = new Set(expandDetailColumns(detail.columns, layout));
  const visibility: VisibilityState = {};
  for (const column of layout.columns)
    visibility[column.key] = visibleKeys.has(column.key);
  const ordered = expandDetailColumns(
    detail.order.length ? detail.order : detail.columns,
    layout,
  );
  const order = [
    ...ordered,
    ...layout.columns.map((c) => c.key).filter((k) => !ordered.includes(k)),
  ];
  return { visibility, order, token: -1 };
}

/**
 * Money travels as a DECIMAL STRING ("1166830.70"), so TanStack's default comparator ranks
 * it as text: "1166830.70" lands before "900.00" and the column reads as broken. A blank
 * is not zero and sorts below every number, the same way it renders as "-".
 */
export function numericSortingFn(
  rowA: { getValue: (id: string) => unknown },
  rowB: { getValue: (id: string) => unknown },
  columnId: string,
): number {
  const parse = (value: unknown): number | null => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const a = parse(rowA.getValue(columnId));
  const b = parse(rowB.getValue(columnId));
  if (a === null && b === null) return 0;
  if (a === null) return -1;
  if (b === null) return 1;
  return a === b ? 0 : a - b;
}

function cellFor(column: ReportColumn) {
  return function Cell({ getValue }: { getValue: () => unknown }) {
    const value = getValue();
    if (column.type === 'bool') {
      return value ? (
        <Check className="size-4 text-muted-foreground" aria-label="Yes" />
      ) : null;
    }
    if (value == null || value === '')
      return <span className="text-muted-foreground">-</span>;
    if (column.type === 'money') {
      // A zero reads as "no money here", not as a number somebody typed - which is why the
      // client's own sheet prints RM- in that cell rather than 0.00 (AC-G5).
      if (Number(value) === 0)
        return <span className="block text-end text-muted-foreground">-</span>;
      return (
        <span className="block text-end tabular-nums">
          {formatMoney2dp(String(value), '-')}
        </span>
      );
    }
    if (column.type === 'date')
      return <span>{formatDateSafe(String(value))}</span>;
    const text = String(value);
    return (
      <span className="truncate" title={text}>
        {text}
      </span>
    );
  };
}

/**
 * Which visible column carries the "Total" label, by the SAME rule the workbook uses
 * (AC-G9): the column immediately before the first one carrying a total, else the first
 * column that carries none. The screen and the file are read side by side, so the word
 * cannot sit in a different place in each.
 */
function isTotalLabelColumn(
  leafIds: string[],
  columnId: string,
  totalled: Set<string>,
): boolean {
  const free = leafIds.filter((id) => !totalled.has(id));
  if (!free.length) return false;
  const firstTotal = leafIds.findIndex((id) => totalled.has(id));
  const preferred = firstTotal > 0 ? leafIds[firstTotal - 1] : null;
  return (
    columnId === (preferred && !totalled.has(preferred) ? preferred : free[0])
  );
}

function buildColumns(layout: ReportDetailLayout): ColumnDef<ReportRow>[] {
  const byKey = new Map(layout.columns.map((c) => [c.key, c]));
  const grouped = new Set(layout.column_groups.flatMap((g) => g.keys));
  const totalled = new Set(Object.keys(layout.totals));

  const leaf = (column: ReportColumn): ColumnDef<ReportRow> => ({
    accessorKey: column.key,
    id: column.key,
    header: ({ column: tableColumn }) => (
      <DataGridColumnHeader title={column.label} column={tableColumn} />
    ),
    size: column.size ?? 140,
    cell: cellFor(column),
    sortingFn:
      column.type === 'money' || column.type === 'integer'
        ? (a, b, id) => numericSortingFn(a, b, id)
        : 'auto',
    /**
     * The totals row. Every column declares a footer so the "Total" label can follow the
     * column the user dragged to the front: pinning it to the column that happened to be
     * first when the run came back leaves the word stranded mid-row after a reorder.
     */
    footer: ({ table: tableInstance }) => {
      if (column.type === 'money') {
        return (
          <span className="block text-end tabular-nums">
            {formatMoney2dp(layout.totals[column.key], '')}
          </span>
        );
      }
      const leafIds = tableInstance.getVisibleLeafColumns().map((c) => c.id);
      return isTotalLabelColumn(leafIds, column.key, totalled) ? 'Total' : null;
    },
    meta: {
      headerTitle: column.label,
      skeleton: <Skeleton className="h-4 w-24" />,
    },
  });

  const out: ColumnDef<ReportRow>[] = [];
  layout.columns.forEach((column) => {
    if (grouped.has(column.key)) {
      const group = layout.column_groups.find((g) => g.keys[0] === column.key);
      if (!group) return;
      out.push({
        id: group.source,
        header: group.label,
        columns: group.keys
          .map((key) => byKey.get(key))
          .filter((c): c is ReportColumn => Boolean(c))
          .map((c) => leaf(c)),
      });
      return;
    }
    out.push(leaf(column));
  });
  return out;
}

/**
 * The params as the screen holds them: the view's own, and the param's default for
 * anything it does not name.
 *
 * The report default deliberately carries NO period (see `views_service.default_config`):
 * a default view saved in 2026 would otherwise open every user on 2026 for the whole of
 * 2027. Filling it in here from the param meta - which resolves to the current year on the
 * server, per request - is what puts a concrete period on screen, in the run and in any
 * view the user then saves.
 */
function withParamDefaults(
  params: ReportParamValues,
  metaParams: ReportParamMeta[],
  reportDefault: ReportParamValues = {},
): ReportParamValues {
  const filled: ReportParamValues = { ...params };
  for (const param of metaParams) {
    // A required single choice the caller cannot pick (a shared view saved on a company
    // outside their grant) falls back to what the report opens on for THEM, rather than
    // running a view that can only answer 403.
    if (
      param.kind === 'select' &&
      !param.multi &&
      !param.clearable &&
      param.key in filled
    ) {
      const allowed = new Set(param.options.map((option) => option.value));
      const chosen = filled[param.key];
      const values = Array.isArray(chosen)
        ? chosen
        : typeof chosen === 'string'
          ? [chosen]
          : [];
      if (values.some((value) => !allowed.has(value))) delete filled[param.key];
    }
    if (param.key in filled) continue;
    filled[param.key] = (reportDefault[param.key] ??
      param.default) as ReportParamValue;
  }
  return filled;
}

/**
 * Is this set of params a whole question?
 *
 * A custom range is picked in TWO clicks and cleared in one, so between them the period
 * carries one end, or none, or an end that falls before its start. None of those is a
 * question the backend can answer, and asking anyway answers a 422 on screen that the user
 * appears to have caused by opening a calendar. The screen keeps the last answer instead.
 */
export function periodIsRunnable(params: ReportParamValues): boolean {
  for (const value of Object.values(params)) {
    if (
      !value ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      !('kind' in value)
    )
      continue;
    const period = value as ReportPeriod;
    if (period.kind !== 'custom') continue;
    if (!period.from || !period.to || period.from > period.to) return false;
  }
  return true;
}

/**
 * One summary table, and under it the chart when the report draws one. A block with no
 * sales keeps its table, blank, with the empty line under its title: a missing section
 * would read as a report that forgot a channel (AC-S1-15).
 */
function SummaryBlock({
  title,
  layout,
}: {
  title?: string;
  layout: ReportPivotLayout;
}) {
  const empty = Object.keys(layout.cells).length === 0;
  return (
    <section className="min-w-0 space-y-3" aria-label={title ?? layout.title}>
      {title && <h3 className="text-sm font-semibold">{title}</h3>}
      {empty && (
        <p className="text-sm text-muted-foreground">No sales in this period</p>
      )}
      <ReportPivotTable layout={layout} />
      {(layout.chart === 'line' || layout.chart === 'bar') && !empty && (
        <Card>
          <CardContent className="py-4">
            <ReportPivotChart layout={layout} />
          </CardContent>
        </Card>
      )}
    </section>
  );
}

/**
 * ONE page, always (AC-G2), and a skeleton that does not grow with the answer.
 *
 * The register this screen mirrors has no pages: a month is a table you read down to its
 * GRAND TOTAL, and a page control that hides half the rows from a total printed under them
 * is a way to be quietly wrong. So there is no pagination row model at all - every row of
 * the run renders - and the pagination state is left to the ONE thing that still reads it:
 * the shared DataGrid draws one skeleton row per page size while a run is in flight. Set to
 * the row count, a year of forms turned every reload into 214 grey rows scrolling past a
 * screen the user is waiting on; the wait is the same wait whatever the answer's size.
 */
export const ONE_PAGE: PaginationState = { pageIndex: 0, pageSize: 15 };

/**
 * The one report screen. Everything about it comes from `GET /reports/{key}` and
 * `POST /reports/{key}/run`, so report #2 is a two-line route wrapper over this file and
 * nothing here learns its name (PLAN-reporting-foundation, "Why a foundation and not a page").
 */
export function ReportPage({
  reportKey,
  breadcrumb,
}: {
  reportKey: string;
  breadcrumb: { label: string; href?: string }[];
}) {
  const {
    data: meta,
    isLoading: metaLoading,
    error: metaError,
    refetch: refetchMeta,
  } = useReportMeta(reportKey);
  // Saved views are an ADDITION to the report, never a precondition: the page seeds itself
  // from the meta as soon as the views query has SETTLED, error included. Waiting on a
  // query whose failure nothing renders is how a page stays on skeletons for good.
  const { data: views, isLoading: viewsLoading } = useReportViews(reportKey);
  const exportMutation = useReportExport(reportKey);

  const [state, setState] = useState<PageState | null>(null);
  const [gridOverride, setGridOverride] = useState<GridState | null>(null);
  const [configureOpen, setConfigureOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('detail');
  const [sorting, setSorting] = useState<SortingState>([]);

  const applyView = (view: ReportView | null, fallback: ReportViewConfig) => {
    const config = view?.view ?? fallback;
    setState((prev) => ({
      viewId: view?.id ?? null,
      params: withParamDefaults(
        config.params,
        meta?.params ?? [],
        meta?.default_view.params,
      ),
      detail: config.detail,
      pivot: config.pivot,
      token: (prev?.token ?? 0) + 1,
    }));
  };

  // The report opens on the shared default view when one exists, else on the report's own
  // default. Both queries have to have answered, or a default view would be missed.
  // Both lists are searched: a published view stays under Mine for its own author, so
  // looking only at `shared` would miss the default from the account that published it.
  useEffect(() => {
    if (state || !meta || viewsLoading) return;
    const fallback = views
      ? ([...views.mine, ...views.shared].find((v) => v.is_default) ?? null)
      : null;
    const config = fallback?.view ?? meta.default_view;
    // A report opens on the tab it is read by: the Yearly comparison is its summary.
    if (meta.opens_on) setActiveTab(meta.opens_on);
    setState({
      viewId: fallback?.id ?? null,
      params: withParamDefaults(
        config.params,
        meta.params,
        meta.default_view.params,
      ),
      detail: config.detail,
      pivot: config.pivot,
      token: 0,
    });
  }, [state, meta, views, viewsLoading]);

  // The last params that were a whole question. Everything that leaves this page - the
  // run, the export, a saved view - uses these, so the file and the screen always carry the
  // same period; the filter bar goes on showing what the user has picked so far.
  const lastRunnable = useRef<ReportParamValues | null>(null);
  const runParams = useMemo(() => {
    if (!state) return {};
    if (periodIsRunnable(state.params)) lastRunnable.current = state.params;
    return lastRunnable.current ?? state.params;
  }, [state]);

  // A slot the chosen dataset lacks (Count by switched under a held pivot) is replaced by
  // the default view's value, else the first valid option, before anything is sent.
  const datasetValue = meta?.dataset_param
    ? (runParams[meta.dataset_param] as string[] | undefined)?.[0]
    : undefined;
  const { pivot, pivotNote } = useMemo(() => {
    if (!state || !meta) return { pivot: state?.pivot, pivotNote: null as string | null };
    const bad = unavailableKeys(meta.catalog, datasetValue);
    if (bad.size === 0) return { pivot: state.pivot, pivotNote: null };
    const label = (key: string) => meta.catalog.find((c) => c.key === key)?.label ?? key;
    const cleared: string[] = [];
    const firstOf = (tag: string) =>
      meta.catalog.find((c) => c.tag === tag && !bad.has(c.key))?.key ?? '';
    const dim = (held: string, fallback: string, other: string) => {
      if (!bad.has(held)) return held;
      cleared.push(label(held));
      return !bad.has(fallback) && fallback !== other
        ? fallback
        : (meta.catalog.find((c) => c.tag === 'dimension' && !bad.has(c.key) && c.key !== other)
            ?.key ?? '');
    };
    const def = meta.default_view.pivot;
    const rows = dim(state.pivot.rows, def.rows, state.pivot.cols);
    const cols = dim(state.pivot.cols, def.cols, rows);
    let measures = state.pivot.measures;
    const badMeasures = measures.filter((m) => bad.has(m));
    if (badMeasures.length > 0) {
      badMeasures.forEach((m) => cleared.push(label(m)));
      measures = def.measures.filter((m) => !bad.has(m));
      if (measures.length === 0) measures = [firstOf('measure')];
    }
    return {
      pivot: { rows, cols, measures },
      pivotNote: cleared.length
        ? `${cleared.join(', ')} does not apply to this ${
            meta.params.find((p) => p.key === meta.dataset_param)?.label ?? 'choice'
          }; using the default.`
        : null,
    };
  }, [state, meta, datasetValue]);

  const runView: ReportViewConfig | null = useMemo(
    () =>
      state
        ? // Empty `detail.columns` asks for the whole catalog: hiding a column is then a
          // client-side tick rather than a round trip, and a hidden column stays offerable.
          {
            params: runParams,
            detail: { columns: [], order: [] },
            pivot: pivot ?? state.pivot,
          }
        : null,
    [state, runParams, pivot],
  );

  const {
    data: result,
    isPlaceholderData,
    error: runError,
    isFetching,
    refetch: refetchRun,
  } = useReportRun(reportKey, runParams, runView);

  const detailLayout = result?.layouts.detail;
  const columns = useMemo(
    () => (detailLayout ? buildColumns(detailLayout) : []),
    [detailLayout],
  );
  const desiredGrid = useMemo(
    () =>
      detailLayout && state
        ? {
            ...gridStateFromView(state.detail, detailLayout),
            token: state.token,
          }
        : null,
    [detailLayout, state],
  );
  const effectiveGrid =
    gridOverride && state && gridOverride.token === state.token
      ? gridOverride
      : desiredGrid;

  /**
   * The order, minus any id the CURRENT result has no column for.
   *
   * The sponsorship band is fixed now, so its four ids outlive any period; a group that
   * DERIVES its members still has data-dependent ones, and a column order saved before the
   * band was fixed still names the old derived ids. Either way the table must not be handed
   * an id it cannot resolve, or it logs "Column with id ... does not exist" every render.
   */
  const columnOrder = useMemo(() => {
    const present = new Set(
      (detailLayout?.columns ?? []).map((column) => column.key),
    );
    return (effectiveGrid?.order ?? []).filter((id) => present.has(id));
  }, [detailLayout, effectiveGrid]);

  const table = useReactTable({
    data: (detailLayout?.rows ?? []) as ReportRow[],
    columns,
    getRowId: (_row, index) => String(index),
    state: {
      sorting,
      pagination: ONE_PAGE,
      columnVisibility: effectiveGrid?.visibility ?? {},
      columnOrder,
    },
    onSortingChange: setSorting,
    // Both changes start from `effectiveGrid`, which already DISCARDS an override written
    // against an older token. Starting from the raw override instead resurrected the
    // pre-view columns on the first toggle after a saved view was applied.
    //
    // Both are also FUNCTIONAL updates, and that is load-bearing rather than tidiness:
    // `useListingColumnPreferences` applies a saved config by calling `setColumnOrder` and
    // `setColumnVisibility` back to back in one effect, so both handlers run against the
    // same render. Reading `effectiveGrid` directly, the second call rebuilt the whole
    // GridState from the render's stale value and threw away the order the first had just
    // applied - the saved order was replaced by the report's default one, and the grid then
    // PERSISTED that default over the user's order on the very next visit. Visibility
    // survived, because it was the write that won.
    onColumnVisibilityChange: (updater) => {
      if (!effectiveGrid || !state) return;
      setGridOverride((prev) => {
        const base = prev && prev.token === state.token ? prev : effectiveGrid;
        const next =
          typeof updater === 'function' ? updater(base.visibility) : updater;
        return { visibility: next, order: base.order, token: state.token };
      });
    },
    onColumnOrderChange: (updater) => {
      if (!effectiveGrid || !state) return;
      setGridOverride((prev) => {
        const base = prev && prev.token === state.token ? prev : effectiveGrid;
        const next =
          typeof updater === 'function' ? updater(base.order) : updater;
        return { visibility: base.visibility, order: next, token: state.token };
      });
    },
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  /** What Save view and Export record: the columns the user can actually see, in order. */
  const visibleDetail = (): ReportViewConfig['detail'] => {
    const leaves = table.getVisibleLeafColumns().map((c) => c.id);
    const visible = detailLayout
      ? collapseDetailColumns(leaves, detailLayout)
      : leaves;
    return { columns: visible, order: visible };
  };

  const currentConfig: ReportViewConfig | null = state
    ? { params: runParams, detail: visibleDetail(), pivot: pivot ?? state.pivot }
    : null;

  const setParam = (key: string, value: ReportParamValue) => {
    // A report that names its pivot rows per dataset value (Top asks: topic or product)
    // moves the rows with the dataset param, so no saved view is needed to see them.
    const picked = Array.isArray(value) ? value[0] : undefined;
    const rows =
      key === meta?.dataset_param && picked !== undefined
        ? meta?.pivot_rows_by?.[picked]
        : undefined;
    setState((prev) =>
      prev
        ? {
            ...prev,
            params: { ...prev.params, [key]: value },
            ...(rows ? { pivot: { ...prev.pivot, rows } } : {}),
          }
        : prev,
    );
  };

  const heading = (
    <Container>
      <PageHeader
        title={meta?.title ?? 'Report'}
        crumbs={breadcrumb.map((crumb) => ({
          title: crumb.label,
          path: crumb.href,
        }))}
        actions={
          meta && state && currentConfig ? (
            <>
              <ReportViewsMenu
                reportKey={reportKey}
                canPublish={meta.can_publish}
                currentViewId={state.viewId}
                currentConfig={currentConfig}
                onApply={(view) => applyView(view, meta.default_view)}
              />
              <Button
                variant="outline"
                onClick={() => setConfigureOpen(true)}
                className="gap-1.5"
              >
                <Settings2 className="size-4" />
                Configure summary
              </Button>
              <Button
                onClick={() =>
                  exportMutation.mutate({
                    params: runParams,
                    view: currentConfig,
                  })
                }
                disabled={exportMutation.isPending}
                className="gap-1.5"
              >
                <Download className="size-4" />
                Export to Excel
              </Button>
            </>
          ) : null
        }
      />
    </Container>
  );

  if (metaError) {
    return (
      <>
        {heading}
        <Container>
          <Alert variant="destructive" appearance="light">
            <AlertTitle>{metaError.message}</AlertTitle>
            <AlertDescription>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refetchMeta()}
              >
                Try again
              </Button>
            </AlertDescription>
          </Alert>
        </Container>
      </>
    );
  }

  if (metaLoading || !meta || !state) {
    return (
      <>
        {heading}
        <Container>
          <Card>
            <CardContent className="space-y-4 py-6">
              <Skeleton className="h-9 w-full max-w-3xl" />
              <Skeleton className="h-64 w-full" />
            </CardContent>
          </Card>
        </Container>
      </>
    );
  }

  const capped = runError instanceof ReportCappedError;
  const detailTitle = detailLayout?.title ?? 'Detail';
  const summary = result?.layouts.summary;

  return (
    <>
      {heading}
      <Container>
        <div className="space-y-5">
          <Card>
            <CardContent className="py-4">
              <ReportFilterBar
                params={meta.params}
                values={state.params}
                onChange={setParam}
                disabled={isFetching}
              />
              {result?.note && (
                <p
                  className="mt-3 text-xs text-muted-foreground"
                  data-testid="report-note"
                >
                  {result.note}
                </p>
              )}
            </CardContent>
          </Card>

          {pivotNote && (
            <p
              role="status"
              className="text-xs text-muted-foreground"
              data-testid="summary-choice-note"
            >
              {pivotNote}
            </p>
          )}

          {capped && (
            <Alert variant="warning" appearance="light">
              <AlertTitle>{runError.message}</AlertTitle>
              <AlertDescription>
                <Button
                  size="sm"
                  onClick={() =>
                    currentConfig &&
                    exportMutation.mutate({
                      params: runParams,
                      view: currentConfig,
                    })
                  }
                  className="gap-1.5"
                >
                  <Download className="size-4" />
                  Export to Excel
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {runError && !capped && (
            <Alert variant="destructive" appearance="light">
              <AlertTitle>{runError.message}</AlertTitle>
              <AlertDescription>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void refetchRun()}
                >
                  Try again
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {!runError && !result && (
            <Card>
              <CardContent className="space-y-3 py-6">
                <Skeleton className="h-9 w-64" />
                <Skeleton className="h-56 w-full" />
              </CardContent>
            </Card>
          )}

          {!runError && result && result.row_count === 0 && (
            <Card>
              <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
                <p className="text-sm font-medium">
                  {/* Named after the report being rendered: one page serves every report,
                      so a hardcoded noun is wrong for report #2. */}
                  No {detailLayout ? detailLayout.title.toLowerCase() : 'rows'}{' '}
                  in {result.period_label}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => applyView(null, meta.default_view)}
                >
                  Reset to report default
                </Button>
              </CardContent>
            </Card>
          )}

          {!runError &&
            result &&
            result.row_count > 0 &&
            detailLayout &&
            summary && (
              <Tabs
                value={activeTab}
                onValueChange={setActiveTab}
                className="w-full"
              >
                <TabsList>
                  <TabsTrigger value="detail">
                    <TableProperties />
                    <span>
                      {detailTitle} ({result.row_count})
                    </span>
                  </TabsTrigger>
                  <TabsTrigger value="summary">
                    <ChartColumn />
                    <span>{summary.title}</span>
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="detail" className="mt-5">
                  {detailLayout.truncated && (
                    <p
                      className="mb-3 text-xs text-muted-foreground"
                      data-testid="detail-truncated"
                    >
                      Showing the latest{' '}
                      {detailLayout.rows.length.toLocaleString()} of{' '}
                      {result.row_count.toLocaleString()} lines.
                    </p>
                  )}
                  <DataGrid
                    table={table}
                    recordCount={result.row_count}
                    isLoading={isFetching}
                    isPlaceholderData={isPlaceholderData}
                    /**
                     * Column preferences are the REPORT DEFAULT's memory (AC-C1).
                     *
                     * The preferences row is one per layout, not one per view, so a saved
                     * view's columns used to be written under the same key the default
                     * reads: the next visit opened on that view's columns while the menu
                     * named a different one, and Save and Export then recorded them under
                     * that name. With a view applied the grid follows the VIEW alone and
                     * writes nothing back; an empty listing key is how the shared hook is
                     * told there is nothing to read or save.
                     */
                    listingKey={
                      state.viewId
                        ? ''
                        : reportLayoutListingKey(
                            meta.permission,
                            detailLayout.key,
                          )
                    }
                    tableLayout={{
                      width: 'fixed',
                      columnsResizable: true,
                      columnsVisibility: true,
                      // The client reads a whole month at a glance; a comfortable row height
                      // turns twenty forms into a scroll (AC-G2).
                      dense: true,
                    }}
                  >
                    <Card>
                      <CardHeader className="block">
                        <DataGridListToolbar
                          table={table}
                          exportConfig={false}
                          onRefresh={() => void refetchRun()}
                          isRefreshing={isFetching}
                        />
                      </CardHeader>
                      <CardTable>
                        <DataGridTable />
                      </CardTable>
                    </Card>
                  </DataGrid>
                </TabsContent>

                <TabsContent value="summary" className="mt-5 space-y-6">
                  {result.layouts.blocks?.length ? (
                    result.layouts.blocks.map((block) => (
                      <SummaryBlock
                        key={block.key}
                        title={block.title}
                        layout={block.summary}
                      />
                    ))
                  ) : (
                    <SummaryBlock layout={summary} />
                  )}
                </TabsContent>
              </Tabs>
            )}
        </div>
      </Container>

      <ConfigureSummaryDialog
        open={configureOpen}
        onOpenChange={setConfigureOpen}
        catalog={meta.catalog}
        value={pivot ?? state.pivot}
        datasetValue={datasetValue}
        datasetLabel={meta.params.find((p) => p.key === meta.dataset_param)?.label}
        onApply={(pivot) =>
          setState((prev) => (prev ? { ...prev, pivot } : prev))
        }
      />
    </>
  );
}
