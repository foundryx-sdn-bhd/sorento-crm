'use client';

import { Fragment, useMemo, useState, type ComponentProps } from 'react';
import Link from 'next/link';
import { format } from 'date-fns';
import { CalendarIcon, ChevronDown, ChevronRight, GitBranch, Loader2 } from 'lucide-react';
import { DateRange } from 'react-day-picker';
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts';
import { useHasPermission } from '@/hooks/usePermissions';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { formatUsd } from '../lib/formatUsd';
import {
  useQueryDetail,
  useRecentQueries,
  useTopContacts,
  useTopUsers,
  useUsageByDay,
  useUsageSummary,
} from '../hooks/useAIUsage';
import type { RecentQueryItem } from '../services/aiUsageService';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';

const FEATURE_ALL = 'all';
const FEATURE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: FEATURE_ALL, label: 'All AI usage' },
  { value: 'ai_assistant', label: 'AI Assistant (chat)' },
  { value: 'ai_extract', label: 'AI Extract (portal forms)' },
  { value: 'ai_document_extract', label: 'Document reading (PO, delivery schedule)' },
  { value: 'chatbot_parser', label: 'Chatbot (understanding a WhatsApp message)' },
];

// One place, so a new feature cannot appear in the filter but read as a dash in the table.
const FEATURE_LABELS: Readonly<Record<string, string>> = {
  ai_assistant: 'AI Assistant',
  ai_extract: 'AI Extract',
  ai_document_extract: 'Document reading',
  chatbot_parser: 'Chatbot parser',
};

const PERMISSION = 'system.ai_assistant_settings.view';

function defaultRange(): DateRange {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 7);
  return { from, to };
}

function formatNumber(n: number | null | undefined): string {
  if (n == null) return '0';
  return n.toLocaleString();
}

/** A total over `calls` calls of which `unpriced` had no price: a dash when none could be priced. */
function totalCost(cost: number, unpriced: number, calls: number, unknown = 0): string {
  return formatUsd(unpriced + unknown >= calls && calls > 0 ? null : cost);
}

function costTitle(q: RecentQueryItem): string | undefined {
  if (q.cost_usd != null) return undefined;
  if (q.cache_unknown) {
    return q.feature === 'chatbot_parser' ? 'Cache use not recorded before 4 Oct' : 'Cache use not recorded';
  }
  const model = q.model ?? 'this model';
  return q.cached_price_missing ? `No cached-input price for ${model}` : `No price for ${model}`;
}

function notPricedLabel(n: number): string {
  return `${n} ${n === 1 ? 'call' : 'calls'} not priced`;
}

function unknownCacheLabel(n: number): string {
  return `${n} ${n === 1 ? 'call' : 'calls'} with unknown cache use`;
}

/** Why some calls are left out of a total, or undefined when none are. */
function excludedLabel(unpriced = 0, unknown = 0): string | undefined {
  const parts = [unpriced > 0 ? notPricedLabel(unpriced) : '', unknown > 0 ? unknownCacheLabel(unknown) : ''];
  return parts.filter(Boolean).join(', ') || undefined;
}

export default function AIUsagePage() {
  const hasPermission = useHasPermission(PERMISSION);

  const [dateRange, setDateRange] = useState<DateRange | undefined>(defaultRange());
  const [pendingRange, setPendingRange] = useState<DateRange | undefined>(dateRange);
  const [datePopoverOpen, setDatePopoverOpen] = useState(false);
  const [featureFilter, setFeatureFilter] = useState<string>(FEATURE_ALL);

  const from = dateRange?.from;
  const to = dateRange?.to;
  const featureForApi = featureFilter === FEATURE_ALL ? undefined : featureFilter;

  const summaryQuery = useUsageSummary(from, to, featureForApi);
  const byDayQuery = useUsageByDay(from, to, featureForApi);
  const topUsersQuery = useTopUsers(from, to, 10, featureForApi);
  const topContactsQuery = useTopContacts(from, to, 10, featureForApi ?? 'ai_extract');
  const recentQueriesQuery = useRecentQueries(from, to, 50, featureForApi);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const queryDetail = useQueryDetail(expandedId);

  const chartData = useMemo(() => {
    return (byDayQuery.data || []).map((d) => ({
      date: d.date,
      messages: d.messages,
      tokens: d.tokens,
      cost_usd: d.cost_usd,
      unpriced_calls: d.unpriced_calls,
      unknown_cache_calls: d.unknown_cache_calls,
    }));
  }, [byDayQuery.data]);

  const chartConfig = useMemo(
    () => ({
      tokens: { label: 'Tokens', color: 'hsl(var(--primary))' },
      messages: { label: 'Messages', color: 'hsl(var(--muted-foreground))' },
    }),
    [],
  );

  if (!hasPermission) {
    return (
      <Container>
        <div className="rounded-md border p-6 text-sm text-muted-foreground">
          Forbidden - you don&apos;t have permission to view AI assistant usage.
        </div>
      </Container>
    );
  }

  const handleRangeApply = () => {
    setDateRange(pendingRange);
    setDatePopoverOpen(false);
  };

  const handleRangeReset = () => {
    const def = defaultRange();
    setDateRange(def);
    setPendingRange(def);
    setDatePopoverOpen(false);
  };

  const summary = summaryQuery.data;

  return (
    <>
      <Container>
        <PageHeader title="AI Usage" />
      </Container>
      <Container className="space-y-4">
        {/* Two w-60 controls in a no-wrap row is 480px of content in a 375px
            viewport, and the page scrolled sideways to fit them. They wrap and
            take the full width under sm. */}
        <div className="flex flex-wrap items-center gap-3">
          <Popover open={datePopoverOpen} onOpenChange={setDatePopoverOpen}>
            <PopoverTrigger asChild>
              <Button
                id="usage-date"
                variant="outline"
                className={cn(
                  'w-full justify-start font-normal sm:w-60',
                  !dateRange && 'text-muted-foreground',
                )}
              >
                <CalendarIcon className="h-4 w-4" />
                {dateRange?.from ? (
                  dateRange.to ? (
                    <>
                      {format(dateRange.from, 'LLL dd, y')} - {format(dateRange.to, 'LLL dd, y')}
                    </>
                  ) : (
                    format(dateRange.from, 'LLL dd, y')
                  )
                ) : (
                  <span>Filter by date range</span>
                )}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
              <Calendar
                mode="range"
                defaultMonth={pendingRange?.from}
                selected={pendingRange}
                onSelect={setPendingRange}
                numberOfMonths={2}
              />
              <div className="flex items-center justify-end gap-1.5 border-t border-border p-3">
                <Button variant="outline" onClick={handleRangeReset}>
                  Reset
                </Button>
                <Button onClick={handleRangeApply}>Apply</Button>
              </div>
            </PopoverContent>
          </Popover>
          <div className="w-full sm:w-auto" data-testid="ai-usage-feature-filter">
            <SearchableSelect
              value={featureFilter}
              onChange={setFeatureFilter}
              options={FEATURE_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
              placeholder="All AI usage"
              triggerClassName="w-full sm:w-60"
            />
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-5">
          <SummaryCard
            label="Messages"
            value={formatNumber(summary?.total_messages)}
            loading={summaryQuery.isLoading}
          />
          <SummaryCard
            label="Total tokens"
            value={formatNumber(summary?.total_tokens)}
            loading={summaryQuery.isLoading}
          />
          <SummaryCard
            label="Prompt tokens"
            value={formatNumber(summary?.prompt_tokens)}
            loading={summaryQuery.isLoading}
          />
          <SummaryCard
            label="Completion tokens"
            value={formatNumber(summary?.completion_tokens)}
            loading={summaryQuery.isLoading}
          />
          <SummaryCard
            label="Cost (USD)"
            value={
              summary
                ? totalCost(
                    summary.cost_usd,
                    summary.unpriced_calls,
                    summary.total_messages,
                    summary.unknown_cache_calls,
                  )
                : formatUsd(null)
            }
            notes={[
              summary && summary.unpriced_calls > 0 ? notPricedLabel(summary.unpriced_calls) : '',
              summary && summary.unknown_cache_calls > 0
                ? unknownCacheLabel(summary.unknown_cache_calls)
                : '',
            ]}
            loading={summaryQuery.isLoading}
          />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Tokens per day</CardTitle>
            <CardDescription>Total tokens consumed by date</CardDescription>
          </CardHeader>
          <CardContent>
            {byDayQuery.isLoading ? (
              <div className="flex h-64 items-center justify-center text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
              </div>
            ) : chartData.length === 0 ? (
              <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
                No usage data for the selected range.
              </div>
            ) : (
              <ChartContainer config={chartConfig} className="h-64 w-full">
                <LineChart data={chartData} margin={{ left: 8, right: 8, top: 8, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <ChartTooltip
                    content={(props) => {
                      const day = props.payload?.[0]?.payload as
                        | {
                            cost_usd: number;
                            unpriced_calls: number;
                            unknown_cache_calls?: number;
                            messages: number;
                          }
                        | undefined;
                      return (
                        <div>
                          <ChartTooltipContent {...(props as ComponentProps<typeof ChartTooltipContent>)} />
                          {day ? (
                            <div className="mt-1 rounded-lg border border-border/50 bg-background px-2.5 py-1.5 text-xs shadow-xl">
                              Cost{' '}
                              {totalCost(
                                day.cost_usd,
                                day.unpriced_calls,
                                day.messages,
                                day.unknown_cache_calls,
                              )}
                              {excludedLabel(day.unpriced_calls, day.unknown_cache_calls) ? (
                                <span className="text-muted-foreground">
                                  {' '}
                                  ({excludedLabel(day.unpriced_calls, day.unknown_cache_calls)})
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      );
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="tokens"
                    stroke="var(--color-tokens)"
                    strokeWidth={2}
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="messages"
                    stroke="var(--color-messages)"
                    strokeWidth={1.5}
                    dot={false}
                  />
                </LineChart>
              </ChartContainer>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-3 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Top users</CardTitle>
              <CardDescription>Internal staff - highest message volume in range</CardDescription>
            </CardHeader>
            <CardContent>
              {topUsersQuery.isLoading ? (
                <SectionSkeleton rows={3} />
              ) : (topUsersQuery.data || []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No user activity yet.</p>
              ) : (
                <ScrollArea>
                  <table className="w-auto min-w-full text-sm">
                    <thead>
                      <tr className="border-b text-xs text-muted-foreground">
                        <th className="py-2 text-left font-medium">#</th>
                        <th className="py-2 text-left font-medium">Name</th>
                        <th className="py-2 text-right font-medium">Messages</th>
                        <th className="py-2 text-right font-medium">Tokens</th>
                        <th className="py-2 text-right font-medium">Cost (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(topUsersQuery.data || []).map((u, i) => (
                        <tr key={u.user_id} className="border-b last:border-0">
                          <td className="py-2 text-muted-foreground">{i + 1}</td>
                          <td className="py-2">{u.name || u.user_id}</td>
                          <td className="py-2 text-right tabular-nums">{formatNumber(u.messages)}</td>
                          <td className="py-2 text-right tabular-nums">{formatNumber(u.tokens)}</td>
                          <td
                            className="py-2 text-right tabular-nums"
                            title={excludedLabel(u.unpriced_calls, u.unknown_cache_calls)}
                          >
                            {totalCost(u.cost_usd, u.unpriced_calls, u.messages, u.unknown_cache_calls)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <ScrollBar orientation="horizontal" />
                </ScrollArea>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Top contacts</CardTitle>
              <CardDescription>
                Portal contacts (by phone) - defaults to AI Extract spend
              </CardDescription>
            </CardHeader>
            <CardContent>
              {topContactsQuery.isLoading ? (
                <SectionSkeleton rows={3} />
              ) : (topContactsQuery.data || []).length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No portal contact activity in range.
                </p>
              ) : (
                <ScrollArea>
                  <table className="w-auto min-w-full text-sm" data-testid="ai-usage-top-contacts">
                    <thead>
                      <tr className="border-b text-xs text-muted-foreground">
                        <th className="py-2 text-left font-medium">#</th>
                        <th className="py-2 text-left font-medium">Phone</th>
                        <th className="py-2 text-left font-medium">Name</th>
                        <th className="py-2 text-right font-medium">Calls</th>
                        <th className="py-2 text-right font-medium">Tokens</th>
                        <th className="py-2 text-right font-medium">Cost (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(topContactsQuery.data || []).map((c, i) => (
                        <tr key={c.contact_id} className="border-b last:border-0">
                          <td className="py-2 text-muted-foreground">{i + 1}</td>
                          <td className="py-2 font-mono text-xs">{c.phone_number || '-'}</td>
                          <td className="py-2">{c.name || <span className="text-muted-foreground italic">unknown</span>}</td>
                          <td className="py-2 text-right tabular-nums">{formatNumber(c.messages)}</td>
                          <td className="py-2 text-right tabular-nums">{formatNumber(c.tokens)}</td>
                          <td
                            className="py-2 text-right tabular-nums"
                            title={excludedLabel(c.unpriced_calls, c.unknown_cache_calls)}
                          >
                            {totalCost(c.cost_usd, c.unpriced_calls, c.messages, c.unknown_cache_calls)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <ScrollBar orientation="horizontal" />
                </ScrollArea>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-3 md:grid-cols-1">

          <Card>
            <CardHeader>
              <CardTitle>Recent queries</CardTitle>
              <CardDescription>Most recent assistant turns</CardDescription>
            </CardHeader>
            <CardContent>
              {recentQueriesQuery.isLoading ? (
                <SectionSkeleton rows={3} />
              ) : (recentQueriesQuery.data || []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No recent queries in range.</p>
              ) : (
                <div className="max-h-[420px] overflow-auto">
                  {/* One scroller, both axes: a nested one broke the sticky head. */}
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-background">
                      <tr className="border-b text-xs text-muted-foreground">
                        <th className="w-6 py-2"></th>
                        <th className="py-2 text-left font-medium">Time</th>
                        <th className="py-2 text-left font-medium">Feature</th>
                        <th className="py-2 text-left font-medium">Principal</th>
                        <th className="py-2 text-left font-medium">Query</th>
                        <th className="py-2 text-right font-medium">ms</th>
                        <th className="py-2 text-right font-medium">Tokens</th>
                        <th className="py-2 text-right font-medium">Cost (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(recentQueriesQuery.data || []).map((q) => {
                        // message_id is null for ai_extract rows; key on a stable composite.
                        const rowKey = q.message_id ?? `${q.created_at}-${q.contact_phone ?? q.user_name ?? ''}`;
                        const isExpanded = q.message_id != null && expandedId === q.message_id;
                        const featureLabel = (q.feature && FEATURE_LABELS[q.feature]) || '-';
                        const principal =
                          q.contact_phone || q.contact_name
                            ? `${q.contact_name ?? 'unknown'} · ${q.contact_phone ?? ''}`.trim()
                            : q.user_name || '-';
                        return (
                          <Fragment key={rowKey}>
                            <tr
                              className={cn(
                                'border-b hover:bg-muted/40',
                                q.message_id ? 'cursor-pointer' : '',
                              )}
                              onClick={() =>
                                q.message_id &&
                                setExpandedId(isExpanded ? null : q.message_id)
                              }
                            >
                              <td className="py-2 align-top">
                                {q.message_id ? (
                                  isExpanded ? (
                                    <ChevronDown className="size-3.5 text-muted-foreground" />
                                  ) : (
                                    <ChevronRight className="size-3.5 text-muted-foreground" />
                                  )
                                ) : null}
                              </td>
                              <td className="py-2 align-top text-xs whitespace-nowrap text-muted-foreground">
                                {formatDateTimeInMalaysia(q.created_at)}
                              </td>
                              <td className="py-2 align-top text-xs">{featureLabel}</td>
                              <td className="py-2 align-top" title={principal}>
                                {principal}
                              </td>
                              <td
                                className="max-w-[240px] truncate py-2 align-top"
                                title={q.query_preview}
                              >
                                {q.query_preview}
                              </td>
                              <td className="py-2 text-right align-top tabular-nums">
                                {formatNumber(q.response_time_ms)}
                              </td>
                              <td className="py-2 text-right align-top tabular-nums">
                                {formatNumber(q.tokens)}
                              </td>
                              <td
                                className="py-2 text-right align-top tabular-nums"
                                title={costTitle(q)}
                              >
                                {formatUsd(q.cost_usd)}
                              </td>
                            </tr>
                            {isExpanded ? (
                              <tr className="border-b bg-muted/20">
                                <td></td>
                                <td colSpan={7} className="space-y-2 py-3 pe-3">
                                  {queryDetail.isLoading ? (
                                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                      <Loader2 className="size-3 animate-spin" /> Loading detail...
                                    </div>
                                  ) : queryDetail.isError ? (
                                    <p className="text-xs text-destructive">
                                      {(queryDetail.error as Error)?.message ||
                                        'Failed to load query detail.'}
                                    </p>
                                  ) : queryDetail.data ? (
                                    <>
                                      {q.feature !== 'ai_extract' && q.message_id ? (
                                        <div>
                                          <Link
                                            href={`/system-management/ai-assistant/usage/trace/${encodeURIComponent(q.message_id)}`}
                                            className="inline-flex items-center gap-1 text-xs font-medium text-primary underline underline-offset-2"
                                            data-testid="view-trace-link"
                                          >
                                            <GitBranch className="size-3" />
                                            View full trace
                                          </Link>
                                        </div>
                                      ) : null}
                                      <div>
                                        <p className="text-xs font-medium text-muted-foreground">
                                          Reply
                                        </p>
                                        <p className="mt-1 whitespace-pre-wrap text-xs">
                                          {queryDetail.data.reply || (
                                            <span className="italic text-muted-foreground">
                                              (empty reply)
                                            </span>
                                          )}
                                        </p>
                                      </div>
                                      <div>
                                        <p className="text-xs font-medium text-muted-foreground">
                                          Tools used
                                        </p>
                                        {queryDetail.data.tools_used.length === 0 ? (
                                          <p className="mt-1 text-xs italic text-muted-foreground">
                                            None
                                          </p>
                                        ) : (
                                          <ul className="mt-1 list-disc ps-4 text-xs">
                                            {queryDetail.data.tools_used.map((t, i) => (
                                              <li
                                                key={`${t.name}-${i}`}
                                                className={cn(
                                                  t.ok ? '' : 'text-destructive',
                                                )}
                                              >
                                                <span className="font-mono">{t.name}</span>{' '}
                                                {t.ok ? '' : '(failed)'}
                                              </li>
                                            ))}
                                          </ul>
                                        )}
                                      </div>
                                    </>
                                  ) : null}
                                </td>
                              </tr>
                            ) : null}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </Container>
    </>
  );
}

function SummaryCard({
  label,
  value,
  notes = [],
  loading,
}: {
  label: string;
  value: string;
  notes?: string[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl">
          {loading ? <Loader2 className="size-4 animate-spin" /> : value}
        </CardTitle>
        {loading
          ? null
          : notes
              .filter(Boolean)
              .map((note) => (
                <p key={note} className="text-xs text-muted-foreground">
                  {note}
                </p>
              ))}
      </CardHeader>
    </Card>
  );
}
