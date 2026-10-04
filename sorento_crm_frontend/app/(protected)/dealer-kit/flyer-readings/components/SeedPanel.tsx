'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ArrowRight, Sparkles } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { SearchableSelect } from '@/components/common/SearchableSelect';

import { listPages } from '../../services/dealerKitService';
import type { FlyerSeedResult, UnmatchedCode } from '../../services/flyerReadingService';
import { useSeedFromFlyerReading } from '../hooks/useFlyerReadings';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

/**
 * Step three: where the draft goes, and what it will not contain.
 *
 * The two things this panel exists to say out loud, because both are invisible
 * in the result otherwise:
 *
 * 1. **Unmatched codes are dropped.** Named before the seed AND again in the
 *    result, because each one is a product the flyer prints that the brochure
 *    will not.
 * 2. **Re-seeding never overwrites.** It writes a new version and leaves the
 *    published one exactly where it is (PLAN D10). "Seed into an existing page"
 *    reads like an overwrite to anybody who has not read the plan, so the panel
 *    states the version it is about to create and what stays live.
 */

/** Lowercase, hyphen-separated. The same rule the backend validator applies. */
function toSlug(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** "SRT1, SRT2 and 3 more" - a list a person reads, not a wall of codes. */
function codeSentence(entries: UnmatchedCode[], shown = 6): string {
  const codes = entries.map((entry) => entry.code);
  if (codes.length <= shown) return codes.join(', ');
  return `${codes.slice(0, shown).join(', ')} and ${codes.length - shown} more`;
}

export interface SeedPanelProps {
  readingId: string;
  filename: string;
  /** Printed codes with no product. The seed drops every one of them. */
  unmatched: UnmatchedCode[];
  /** How many printed codes DID resolve, so the panel can say what it will build. */
  matchedCount: number;
  /** The promotion chosen on the review step above, carried into the seed. */
  promotionId: string | null;
  promotionLabel: string | null;
}

export function SeedPanel({
  readingId,
  filename,
  unmatched,
  matchedCount,
  promotionId,
  promotionLabel,
}: SeedPanelProps) {
  const [target, setTarget] = useState<'new' | 'existing'>('new');
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [pageId, setPageId] = useState('');
  const [result, setResult] = useState<FlyerSeedResult | null>(null);

  const {
    data: pages,
    isLoading: pagesLoading,
    error: pagesError,
    refetch: refetchPages,
  } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['dealer-kit', 'pages'],
    queryFn: listPages,
  });

  const { mutate, isPending } = useSeedFromFlyerReading(readingId);

  const pageOptions = useMemo(
    () =>
      (pages ?? []).map((page) => ({
        value: page.id,
        // Never the id. The address is what a designer recognises a brochure by
        // when two of them are called something similar.
        label: page.name,
        description: page.publicPath ?? `/${page.slug}`,
        searchText: `${page.name} ${page.slug}`,
      })),
    [pages],
  );

  const selectedPage = useMemo(
    () => (pages ?? []).find((page) => page.id === pageId) ?? null,
    [pages, pageId],
  );

  const effectiveSlug = slugTouched ? slug : toSlug(name);
  const canSeed =
    !isPending &&
    (target === 'new' ? name.trim().length > 0 && effectiveSlug.length > 0 : Boolean(pageId));

  const submit = () => {
    mutate(
      target === 'new'
        ? {
            name: name.trim(),
            slug: effectiveSlug,
            promotionId,
            commitMessage: `Seeded from ${filename}`,
          }
        : // No promotionId on a re-seed. The promotion lives on the PAGE and
          // the published version reads it from there, so sending the review
          // header's selection would re-price the LIVE brochure the moment this
          // request lands - which is the opposite of what the note below
          // promises. Changing a brochure's offer is the page's own action.
          { pageId, commitMessage: `Seeded from ${filename}` },
      { onSuccess: setResult },
    );
  };

  if (result) {
    return <SeedResult result={result} />;
  }

  return (
    <Card data-testid="dk-fr-seed-panel">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="size-4" />
          Create the draft brochure
        </CardTitle>
      </CardHeader>

      <CardContent className="flex flex-col gap-5">
        <p className="text-sm text-muted-foreground">
          {matchedCount} product{matchedCount === 1 ? '' : 's'} will be laid out, one section per
          flyer page. Nothing is published.
        </p>

        {unmatched.length > 0 && (
          <div
            className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
            data-testid="dk-fr-seed-skip-warning"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <p>
              {unmatched.length} printed code{unmatched.length === 1 ? '' : 's'} will be left out
              because the product master does not have {unmatched.length === 1 ? 'it' : 'them'}:{' '}
              <span className="font-mono">{codeSentence(unmatched)}</span>.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-3">
          <Label>Where it goes</Label>
          <RadioGroup
            value={target}
            onValueChange={(value) => setTarget(value as 'new' | 'existing')}
            className="flex flex-col gap-3"
          >
            <div className="flex items-center gap-2">
              <RadioGroupItem value="new" id="dk-fr-target-new" />
              <Label htmlFor="dk-fr-target-new" className="font-normal">
                A new brochure
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="existing" id="dk-fr-target-existing" />
              <Label htmlFor="dk-fr-target-existing" className="font-normal">
                An existing brochure
              </Label>
            </div>
          </RadioGroup>
        </div>

        {target === 'new' ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="dk-fr-name">Name</Label>
              <Input
                id="dk-fr-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Sorento A3 Flyer 2025-2026"
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="dk-fr-slug">Address</Label>
              <Input
                id="dk-fr-slug"
                value={effectiveSlug}
                onChange={(event) => {
                  setSlugTouched(true);
                  setSlug(toSlug(event.target.value));
                }}
                placeholder="a3-flyer-2026"
              />
              {/* The company segment is what keeps two companies' identical
                  slugs apart, so a hint without it is an address that 404s. */}
              <p className="text-xs text-muted-foreground">
                Readers will find this brochure at /c/&lt;company&gt;/
                {effectiveSlug || '...'}
              </p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <Label htmlFor="dk-fr-page">Brochure</Label>
            <SearchableSelect
              id="dk-fr-page"
              value={pageId}
              onChange={setPageId}
              options={pageOptions}
              loadError={pagesError}
              onRetry={() => void refetchPages()}
              disabled={pagesLoading}
              placeholder={pagesLoading ? 'Loading brochures' : 'Choose a brochure'}
              emptyMessage="No brochure to seed into yet"
            />
            {selectedPage && (
              <p className="text-xs text-muted-foreground" data-testid="dk-fr-reseed-note">
                Creates version {selectedPage.latestVersion + 1} as a draft.{' '}
                {selectedPage.publishedVersion === null
                  ? 'Nothing is published for this brochure yet, so no reader sees anything change.'
                  : `The live version ${selectedPage.publishedVersion} is untouched until somebody publishes the new one.`}
              </p>
            )}
          </div>
        )}

        <div className="flex flex-col gap-1">
          <Label>Prices from</Label>
          {promotionLabel ? (
            <p className="text-sm text-muted-foreground" data-testid="dk-fr-seed-promotion">
              <strong className="text-foreground">{promotionLabel}</strong>, the promotion chosen
              above. Change it there to change it here.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="dk-fr-seed-promotion">
              No promotion. Every tile shows its list price. Choose one above to price the
              brochure from an offer.
            </p>
          )}
        </div>
      </CardContent>

      <CardFooter className="justify-end">
        <Button onClick={submit} disabled={!canSeed} data-testid="dk-fr-seed-button">
          {isPending ? 'Creating the draft' : 'Create draft brochure'}
        </Button>
      </CardFooter>
    </Card>
  );
}

/**
 * What was made, and what did not make it.
 *
 * The skipped list is repeated here rather than assumed read: this is the last
 * screen before somebody opens the builder and stops thinking about the flyer,
 * and each entry is a product the paper advertises and the brochure does not.
 */
export function SeedResult({ result }: { result: FlyerSeedResult }) {
  return (
    <Card data-testid="dk-fr-seed-result">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 break-words">{result.name}</span>
          <Badge variant="outline" className="font-normal">
            Draft v{result.version}
          </Badge>
        </CardTitle>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          {result.sectionCount} section{result.sectionCount === 1 ? '' : 's'},{' '}
          {result.collectionCount} printed row{result.collectionCount === 1 ? '' : 's'} and{' '}
          {result.seededProductCount} product{result.seededProductCount === 1 ? '' : 's'}.
          Nothing is published yet.
        </p>

        {result.skipped.length > 0 ? (
          <div
            className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
            data-testid="dk-fr-result-skipped"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div>
              <p className="font-medium">
                {result.skipped.length} printed code{result.skipped.length === 1 ? '' : 's'} did
                not make it into the brochure
              </p>
              <p className="mt-1 font-mono break-words">{codeSentence(result.skipped, 12)}</p>
              <p className="mt-1">
                The product master has no such code. Create the products and seed again, or
                accept the gap.
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground" data-testid="dk-fr-result-nothing-skipped">
            Every printed code reached a tile. Nothing was left out.
          </p>
        )}

        {/* The review screen already listed every heading beside its page and
            said they are guessed. Repeating the explanation here is the third
            time one screen has made the same point. */}
        <p className="text-sm text-muted-foreground">
          Check the headings first - they are the likeliest thing to be wrong.
        </p>
      </CardContent>

      <CardFooter className="justify-end">
        <Button asChild>
          <Link href={`/dealer-kit/pages/${result.pageId}`}>
            Open the draft
            <ArrowRight className="size-4" />
          </Link>
        </Button>
      </CardFooter>
    </Card>
  );
}
