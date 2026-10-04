'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { FormSection } from '@/components/common/FormSection';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { getUserLookup } from '@/services/userSelectService';
import { useBrandSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query';
import {
  CLASH_MIN_CHARS,
  useClashPreview,
  useFetchClashPreview,
  useFetchLeadOptions,
  useProjectParties,
  useProjectTemplates,
  useProjectTypes,
  useRegisterProject,
  useTakeoverMutations,
  useUpdateProject,
} from '../hooks/useProjects';
import type {
  ClashCandidate,
  Project,
  ProjectRegisterBody,
  ProjectUpdateBody,
} from '../types/project.types';
import { ClashWarningPanel } from '../../pipeline/components/ClashWarningPanel';

type LeadOption = { value: string; label: string };

type ProjectFormProps = { mode: 'create' } | { mode: 'edit'; project: Project };

const OWNER_MANAGE_PERMISSION = 'projects.projects.manage';

/**
 * The form both `/project-sales/new` and `/project-sales/<id>/edit` render
 * (PLAN-project-form-28sep.md D1). One `<form>`, no dialog: the client asked for the
 * dialog gone and create and edit standardised on the same page.
 *
 * The Check button, the clash preview and the progressive "Details" section carry over
 * from the register dialog it replaces (PR #1336 round 2). Two things differ from that
 * dialog:
 *
 * - A third section, "Salesperson and lead", open from the start.
 * - Edit mode starts every section open, never wipes the project's own template on
 *   first render, and excludes the project itself from its own clash check.
 */
export function ProjectForm(props: ProjectFormProps) {
  const { mode } = props;
  const project = mode === 'edit' ? props.project : undefined;
  const router = useRouter();
  const { data: session } = useSession();
  const canManageOwner = useHasPermission(OWNER_MANAGE_PERMISSION);

  const register = useRegisterProject();
  const update = useUpdateProject(project?.id ?? '');

  const [title, setTitle] = React.useState(project?.title ?? '');
  const [developerId, setDeveloperId] = React.useState(project?.developer_party_id ?? '');
  const [typeId, setTypeId] = React.useState(project?.type_id ?? '');
  const [templateId, setTemplateId] = React.useState(project?.template_id ?? '');
  const [registeredCompany, setRegisteredCompany] = React.useState(
    project?.registered_company_name ?? '',
  );
  const [location, setLocation] = React.useState(project?.location ?? '');
  const [address, setAddress] = React.useState(project?.address ?? '');
  const [adminRef, setAdminRef] = React.useState(project?.admin_ref ?? '');
  const [architectId, setArchitectId] = React.useState(project?.architect_party_id ?? '');
  const [mainContractorId, setMainContractorId] = React.useState(
    project?.main_contractor_party_id ?? '',
  );
  const [estimatedValue, setEstimatedValue] = React.useState(project?.estimated_sales_value ?? '');
  const [launchDate, setLaunchDate] = React.useState(project?.launch_date ?? '');
  const [deliveryFrom, setDeliveryFrom] = React.useState(project?.expected_delivery_from ?? '');
  const [deliveryTo, setDeliveryTo] = React.useState(project?.expected_delivery_to ?? '');
  const [brandIds, setBrandIds] = React.useState<string[]>(project?.brand_ids ?? []);
  const [ownerUserId, setOwnerUserId] = React.useState(
    mode === 'create' ? (session?.user?.id ?? '') : (project?.owner_user_id ?? ''),
  );
  // The session can resolve after the first render; the default salesperson follows it
  // until somebody picks one.
  const sessionUserId = session?.user?.id;
  React.useEffect(() => {
    if (mode === 'create' && sessionUserId) {
      setOwnerUserId((current) => current || sessionUserId);
    }
  }, [mode, sessionUserId]);
  const [leadId, setLeadId] = React.useState(project?.lead_id ?? '');
  const [leadOption, setLeadOption] = React.useState<LeadOption | null>(
    project?.lead_id ? { value: project.lead_id, label: project.lead_code ?? 'Linked lead' } : null,
  );

  const [joinTarget, setJoinTarget] = React.useState<{
    candidate: ClashCandidate;
    kind: 'join' | 'dispute';
  } | null>(null);

  const developers = useProjectParties({ party_type: 'developer', limit: 200 });
  const architects = useProjectParties({ party_type: 'architect', limit: 200 });
  const contractors = useProjectParties({ party_type: 'main_contractor', limit: 200 });
  const types = useProjectTypes();
  const templates = useProjectTemplates(typeId || undefined);
  const brands = useBrandSelectQuery();
  const fetchLeadOptions = useFetchLeadOptions();
  const users = useQuery({
    queryKey: ['user-lookup', 'project-owner'],
    queryFn: () => getUserLookup(),
  });

  // The title the user last checked; editing the field clears it, so a result never
  // sits under a title it was not asked about.
  const [checkedTitle, setCheckedTitle] = React.useState('');
  // The title counts as filled once the user leaves the field or checks it, so Details
  // does not open under them after the first letter.
  const [titleSettled, setTitleSettled] = React.useState(mode === 'edit');
  const clash = useClashPreview(checkedTitle, developerId || null);
  const fetchClash = useFetchClashPreview();

  const [sectionOpen, setSectionOpen] = React.useState({
    who: true,
    details: mode === 'edit',
    salesLead: true,
  });
  // Edit mode never runs the auto-open below: every field already has a value, so
  // there is nothing "complete" to react to.
  const detailsSettledRef = React.useRef(mode === 'edit');

  const selectedType = types.data?.find((type) => type.id === typeId);
  // A property development infers its delivery window from the launch date plus a
  // configurable lag; anything else has to state the window, because a hotel
  // refurbishment has no launch date to count from.
  const derivesDelivery = selectedType?.derives_delivery_from_launch ?? false;

  function toggleSection(key: 'who' | 'details' | 'salesLead', next: boolean) {
    if (key === 'details') detailsSettledRef.current = true;
    setSectionOpen((prev) => ({ ...prev, [key]: next }));
  }

  // Template counts only when the chosen type offers one to pick, and only once that is
  // known: an unanswered template list must not open Details early.
  const templateNeeded = Boolean(typeId) && (templates.data?.length ?? 0) > 0;
  const whoComplete =
    Boolean(developerId) &&
    Boolean(typeId) &&
    templates.isSuccess &&
    titleSettled &&
    title.trim().length > 0 &&
    (!templateNeeded || Boolean(templateId));

  // Every Who and what field filled opens Details, once per form (the portal price tag
  // form's openSectionOnce).
  React.useEffect(() => {
    if (!whoComplete || detailsSettledRef.current) return;
    detailsSettledRef.current = true;
    setSectionOpen((prev) => (prev.details ? prev : { ...prev, details: true }));
  }, [whoComplete]);

  // The server re-runs the clash check on an edit only when the title or developer
  // changes; the form's guard follows it, so a project that already exists is never
  // blocked from saving an unrelated field.
  const identityChanged =
    mode === 'create' ||
    title.trim() !== (project?.title ?? '') ||
    (developerId || null) !== (project?.developer_party_id ?? null);
  const checkable = title.trim().length >= CLASH_MIN_CHARS;
  const hasChecked = checkedTitle.length > 0;
  const rawCandidates = clash.data?.candidates ?? [];
  // In edit mode the project's own record is a "clash" against itself; it must never
  // block a save or show up in the panel.
  const candidates =
    mode === 'edit' ? rawCandidates.filter((c) => c.project_id !== project!.id) : rawCandidates;
  const wouldBlock =
    identityChanged && hasChecked && candidates.some((candidate) => candidate.blocks);
  const pending = register.isPending || update.isPending;
  const canSubmit = title.trim().length > 0 && !wouldBlock && !pending;

  async function handleCheck() {
    const trimmed = title.trim();
    if (trimmed.length < CLASH_MIN_CHARS) return;
    setTitleSettled(true);
    setCheckedTitle(trimmed);
    await fetchClash(trimmed, developerId || null).catch(() => undefined);
  }

  function buildBody(): ProjectRegisterBody {
    return {
      title: title.trim(),
      developer_party_id: developerId || null,
      type_id: typeId || null,
      template_id: templateId || null,
      owner_user_id: ownerUserId || null,
      registered_company_name: registeredCompany.trim() || null,
      location: location.trim() || null,
      address: address.trim() || null,
      admin_ref: adminRef.trim() || null,
      architect_party_id: architectId || null,
      main_contractor_party_id: mainContractorId || null,
      estimated_sales_value: estimatedValue.trim() || null,
      launch_date: launchDate || null,
      expected_delivery_from: deliveryFrom || null,
      expected_delivery_to: deliveryTo || null,
      brand_ids: brandIds,
      lead_id: leadId || null,
    };
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    // The one duplicate check on submit: the guard, whether or not Check was pressed.
    if (checkable && identityChanged) {
      const trimmed = title.trim();
      setCheckedTitle(trimmed);
      // A failed check does not stop the user: the server refuses a blocked title
      // with a 409 of its own, which the register/update mutation toasts.
      const preview = await fetchClash(trimmed, developerId || null).catch(() => null);
      const previewCandidates = preview?.candidates ?? [];
      const filtered =
        mode === 'edit'
          ? previewCandidates.filter((c) => c.project_id !== project!.id)
          : previewCandidates;
      if (filtered.some((c) => c.blocks)) return;
    }
    // A refusal (409 lead_already_linked, 403, ...) is toasted by the mutation hook;
    // the user stays on the form with what they typed.
    try {
      if (mode === 'create') {
        const created = await register.mutateAsync(buildBody());
        router.push(`/project-sales/${created.id}`);
      } else {
        const body: ProjectUpdateBody = buildBody();
        await update.mutateAsync(body);
        router.push(`/project-sales/${project!.id}`);
      }
    } catch {
      // handled by the hook's onError
    }
  }

  function handleCancel() {
    router.push(mode === 'create' ? '/project-sales/pipeline' : `/project-sales/${project!.id}`);
  }

  return (
    <>
      <form onSubmit={handleSubmit} className="space-y-4">
        <FormSection
          title="Who and what"
          summary={title.trim() || null}
          open={sectionOpen.who}
          onOpenChange={(next) => toggleSection('who', next)}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="project-developer">Developer</Label>
              <SearchableSelect
                id="project-developer"
                value={developerId}
                onChange={setDeveloperId}
                clearable
                loadError={developers.error}
                onRetry={() => void developers.refetch()}
                options={(developers.data?.data ?? []).map((party) => ({
                  value: party.id,
                  label: party.name,
                  description: party.registration_no ?? undefined,
                }))}
                placeholder="Search developers"
                emptyMessage="No developers yet. Add one under Parties"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="project-type">Project type</Label>
              <SearchableSelect
                id="project-type"
                value={typeId}
                onChange={(next) => {
                  // A new type must clear the template too, or a stale template from
                  // the previous type is submitted and the project gets roles it should
                  // not offer. In the handler, not an effect, so opening an edit form
                  // never wipes the project's own template.
                  setTypeId(next);
                  if (next !== typeId) setTemplateId('');
                }}
                clearable
                loadError={types.error}
                onRetry={() => void types.refetch()}
                options={(types.data ?? []).map((type) => ({
                  value: type.id,
                  label: type.name,
                }))}
                placeholder="Select a type"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="project-title">
              Project title <span className="text-destructive">*</span>
            </Label>
            <div className="flex gap-2">
              <Input
                id="project-title"
                value={title}
                onChange={(event) => {
                  setTitle(event.target.value);
                  setCheckedTitle('');
                  if (!event.target.value.trim()) setTitleSettled(false);
                }}
                onBlur={() => setTitleSettled(title.trim().length > 0)}
                placeholder="e.g. Setia Alam Phase 3B"
                autoComplete="off"
                required
              />
              <Button
                type="button"
                variant="outline"
                className="shrink-0"
                disabled={!checkable || clash.isFetching}
                onClick={() => void handleCheck()}
              >
                {clash.isFetching && hasChecked && (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                )}
                Check
              </Button>
            </div>
            {hasChecked && clash.isSuccess && !clash.isFetching && candidates.length === 0 && (
              <p className="text-xs text-muted-foreground" aria-live="polite">
                No existing project matches this title.
              </p>
            )}
          </div>

          {hasChecked && (
            <ClashWarningPanel
              candidates={candidates}
              isLoading={clash.isFetching}
              developerChosen={Boolean(developerId)}
              onRequestJoin={(candidate) => setJoinTarget({ candidate, kind: 'join' })}
              onDispute={(candidate) => setJoinTarget({ candidate, kind: 'dispute' })}
            />
          )}

          {typeId && (
            <div className="space-y-1.5">
              <Label htmlFor="project-template">Template</Label>
              <SearchableSelect
                id="project-template"
                value={templateId}
                onChange={setTemplateId}
                clearable
                loadError={templates.error}
                onRetry={() => void templates.refetch()}
                options={(templates.data ?? []).map((template) => ({
                  value: template.id,
                  label: template.name,
                  description: template.has_forked_status_graph
                    ? 'Has its own stage flow'
                    : undefined,
                }))}
                placeholder="Select a template"
                emptyMessage="No templates for this type"
              />
            </div>
          )}

        </FormSection>

        <FormSection
          title="Details"
          open={sectionOpen.details}
          onOpenChange={(next) => toggleSection('details', next)}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="project-registered-company">Registered company / SPV</Label>
              <Input
                id="project-registered-company"
                value={registeredCompany}
                onChange={(event) => setRegisteredCompany(event.target.value)}
                placeholder="Optional"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-location">Location</Label>
              <Input
                id="project-location"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                placeholder="e.g. Setia Alam, Selangor"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="project-address">Address</Label>
            <Textarea
              id="project-address"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              rows={3}
              placeholder="Optional"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="project-admin-ref">Filing reference</Label>
              <Input
                id="project-admin-ref"
                value={adminRef}
                onChange={(event) => setAdminRef(event.target.value)}
                maxLength={64}
                placeholder="e.g. PS26-0143"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-value">Estimated sales value (RM)</Label>
              <Input
                id="project-value"
                type="number"
                min="0"
                step="0.01"
                inputMode="decimal"
                value={estimatedValue}
                onChange={(event) => setEstimatedValue(event.target.value)}
                placeholder="0.00"
              />
            </div>
          </div>

          {derivesDelivery ? (
            <div className="space-y-1.5">
              <Label htmlFor="project-launch">Project launch date</Label>
              <Input
                id="project-launch"
                type="date"
                value={launchDate}
                onChange={(event) => setLaunchDate(event.target.value)}
              />
            </div>
          ) : (
            // One range, one control: two date fields let "to" land before "from"
            // and stop reading as a single fact once they wrap apart.
            <div className="space-y-1.5">
              <Label htmlFor="project-delivery-range">Expected delivery</Label>
              <DateRangePicker
                id="project-delivery-range"
                from={deliveryFrom || null}
                to={deliveryTo || null}
                onChange={({ from, to }) => {
                  setDeliveryFrom(from ?? '');
                  setDeliveryTo(to ?? '');
                }}
              />
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="project-architect">Architect</Label>
              <SearchableSelect
                id="project-architect"
                value={architectId}
                onChange={setArchitectId}
                clearable
                loadError={architects.error}
                onRetry={() => void architects.refetch()}
                options={(architects.data?.data ?? []).map((party) => ({
                  value: party.id,
                  label: party.name,
                }))}
                placeholder="Search architects"
                emptyMessage="No architects yet. Add one under Parties"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-contractor">Main contractor</Label>
              <SearchableSelect
                id="project-contractor"
                value={mainContractorId}
                onChange={setMainContractorId}
                clearable
                loadError={contractors.error}
                onRetry={() => void contractors.refetch()}
                options={(contractors.data?.data ?? []).map((party) => ({
                  value: party.id,
                  label: party.name,
                }))}
                placeholder="Search main contractors"
                emptyMessage="No main contractors yet. Add one under Parties"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="project-brands">Brands</Label>
            <SearchableMultiSelect
              id="project-brands"
              value={brandIds}
              onChange={setBrandIds}
              loadError={brands.error}
              onRetry={() => void brands.refetch()}
              options={(brands.data ?? []).map((brand) => ({
                value: brand.id,
                label: brand.brand_name,
              }))}
              placeholder="Select brands"
            />
          </div>
        </FormSection>

        <FormSection
          title="Salesperson and lead"
          open={sectionOpen.salesLead}
          onOpenChange={(next) => toggleSection('salesLead', next)}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="project-owner">Salesperson</Label>
              <SearchableSelect
                id="project-owner"
                value={ownerUserId}
                onChange={setOwnerUserId}
                disabled={!canManageOwner}
                loadError={users.error}
                onRetry={() => void users.refetch()}
                options={(users.data ?? []).map((user) => ({
                  value: user.id,
                  label: user.name || 'Unnamed user',
                }))}
                placeholder="Select a salesperson"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-lead">Lead</Label>
              <SearchableSelect
                id="project-lead"
                value={leadId}
                onChange={setLeadId}
                onOptionChange={setLeadOption}
                fetchOptions={fetchLeadOptions}
                selectedOption={leadOption ?? undefined}
                clearable
                placeholder="Search leads by title or code"
                emptyMessage="No open leads match"
              />
            </div>
          </div>
        </FormSection>

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" onClick={handleCancel}>
            Cancel
          </Button>
          <Button type="submit" disabled={!canSubmit}>
            {pending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {wouldBlock
              ? 'Blocked by an existing project'
              : mode === 'create'
                ? 'Register project'
                : 'Save changes'}
          </Button>
        </div>
      </form>

      {joinTarget && (
        <TakeoverRequestDialog
          candidate={joinTarget.candidate}
          kind={joinTarget.kind}
          onDone={() => setJoinTarget(null)}
        />
      )}
    </>
  );
}

/**
 * The way out of a hard block.
 *
 * Without a recourse path, blocking produces defensive land-grabbing and pushes the
 * argument back into WhatsApp, which is the problem the module was built to solve.
 * The reason is mandatory because the owner or manager has to decide on something.
 */
function TakeoverRequestDialog({
  candidate,
  kind,
  onDone,
}: {
  candidate: ClashCandidate;
  kind: 'join' | 'dispute';
  onDone: () => void;
}) {
  const [reason, setReason] = React.useState('');
  const { request } = useTakeoverMutations(candidate.project_id);

  const isJoin = kind === 'join';

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92dvh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>{isJoin ? 'Ask to join this project' : 'Dispute this project'}</DialogTitle>
          <DialogDescription>
            {isJoin
              ? `${candidate.owner_name ?? 'The owner'} decides. If they approve, you get edit rights on ${candidate.project_code}.`
              : `A sales manager decides. If they agree, ${candidate.project_code} moves to you and ${candidate.owner_name ?? 'the current owner'} stays on as a collaborator.`}
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (!reason.trim()) return;
            await request.mutateAsync({ kind, reason: reason.trim() });
            onDone();
          }}
        >
          <DialogBody className="max-h-[60dvh] space-y-3 overflow-y-auto">
            <div className="rounded-md border border-border bg-muted/40 p-2.5 text-sm">
              <span className="text-sm text-muted-foreground">{candidate.project_code}</span>
              <p className="font-medium">{candidate.title}</p>
              <p className="text-xs text-muted-foreground">
                Owner: {candidate.owner_name ?? 'Unassigned'}
                {candidate.status_label ? ` · ${candidate.status_label}` : ''}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="takeover-reason">
                Reason <span className="text-destructive">*</span>
              </Label>
              <Textarea
                id="takeover-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={4}
                placeholder={
                  isJoin
                    ? 'e.g. I hold the specifying architect relationship on this tender.'
                    : 'e.g. I registered this with the developer in March and have the meeting notes.'
                }
                required
              />
            </div>
          </DialogBody>
          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!reason.trim() || request.isPending}>
              {request.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              {isJoin ? 'Send request' : 'Raise dispute'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
