'use client';

import * as React from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { WizardSteps } from '@/components/common/WizardSteps';
import { useCustomerSelectQuery } from '@/app/(protected)/order-management/shared/hooks/use-customer-select-query';
import {
  useLeadMutations,
  useProjectParties,
} from '../../_shared/hooks/useProjects';
import {
  useAssignableUsers,
  useLeadAcceptanceMutations,
} from '../../_shared/hooks/useLeadAcceptance';
import type {
  LeadNewCustomer,
  ProjectLead,
  ProjectLeadBody,
} from '../../_shared/types/project.types';
import type { LeadInformantBody } from '../../_shared/types/leadAcceptance.types';
import {
  EMPTY_INFORMANT,
  InformantFieldset,
  informantBody,
  type InformantDraft,
} from './InformantFieldset';
import { informantSourceLabel } from './acceptance';

const STEPS = [
  { id: 'development', label: 'The development' },
  { id: 'informant', label: 'Who told us' },
  { id: 'detail', label: 'Lead detail' },
  { id: 'confirm', label: 'Assign and confirm' },
] as const;

type StepId = (typeof STEPS)[number]['id'];

/**
 * Create a lead: the development, who told us, the little we know, and who it goes to.
 *
 * The development comes FIRST because it is the only required decision (AC-A3). A lead
 * anchors on the job, not on a counterparty: on day one nobody knows who will place the
 * order, so the buyer is optional and the informant is a separate thing entirely.
 *
 * A lead is never clash-checked (AC-O3), so there is no warning panel here. That check
 * happens once, at qualify, where it means something.
 */
export function LeadWizardDialog({
  onDone,
  initialTitle = '',
}: {
  /** Called with the created lead, or with nothing when the dialog is closed without one. */
  onDone: (created?: ProjectLead) => void;
  /** What the caller already knows the development as, e.g. text typed into a lead picker. */
  initialTitle?: string;
}) {
  const { create } = useLeadMutations();
  const { assign } = useLeadAcceptanceMutations();
  const [step, setStep] = React.useState<StepId>('development');

  const [title, setTitle] = React.useState(initialTitle);
  const [developerPartyId, setDeveloperPartyId] = React.useState('');
  const [location, setLocation] = React.useState('');

  const [informant, setInformant] = React.useState<InformantDraft>(EMPTY_INFORMANT);

  const [customerId, setCustomerId] = React.useState('');
  const [creatingCustomer, setCreatingCustomer] = React.useState(false);
  const [newCustomer, setNewCustomer] = React.useState<LeadNewCustomer>({
    customer_name: '',
  });
  const [estimatedValue, setEstimatedValue] = React.useState('');
  const [notes, setNotes] = React.useState('');

  const [ownerUserId, setOwnerUserId] = React.useState('');

  const developers = useProjectParties({ party_type: 'developer', limit: 200 });
  const parties = useProjectParties({ limit: 200 });

  // The shared customer-select hook, not a bespoke fetch: one definition of "the
  // customer list" across the app, per the architecture rules. SearchableSelect
  // filters it client-side, which is what every other customer picker does.
  const customers = useCustomerSelectQuery();
  const users = useAssignableUsers();

  const selectedCustomer = (customers.data ?? []).find((row) => row.id === customerId);

  const developmentReady = title.trim().length > 0;
  const stepIndex = STEPS.findIndex((candidate) => candidate.id === step);
  // Rough value may stay blank; a typed one that cannot be read stops the walk at its step.
  const valueError = numberFieldError(estimatedValue, { min: 0, optional: true });
  // The sentence stays hidden while typing; a refused press shows it, then it follows the box live.
  const [valueErrorShown, setValueErrorShown] = React.useState(false);
  const canAdvance = step === 'development' ? developmentReady : true;

  async function submit() {
    const body: ProjectLeadBody & LeadInformantBody = {
      title: title.trim(),
      customer_id: creatingCustomer ? null : customerId || null,
      new_customer: creatingCustomer ? newCustomer : null,
      developer_party_id: developerPartyId || null,
      estimated_value: estimatedValue.trim() || null,
      location: location.trim() || null,
      notes: notes.trim() || null,
      ...informantBody(informant),
    };
    const lead = await create.mutateAsync(body);

    // Assignment is its own step server-side: it stamps the clock and notifies the
    // salesperson. A failure here leaves a perfectly good unassigned lead, so it must
    // not roll the wizard back (the mutation reports it).
    if (ownerUserId) {
      try {
        await assign.mutateAsync({ id: lead.id, ownerUserId });
      } catch {
        /* reported by the mutation */
      }
    }
    onDone(lead);
  }

  const informantParty =
    (parties.data?.data ?? []).find((party) => party.id === informant.informant_party_id)
      ?.name ?? null;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent
        className="max-h-[92vh] w-full max-w-lg overflow-hidden"
        aria-describedby={undefined}
      >
        <DialogHeader>
          <DialogTitle>Create lead</DialogTitle>
        </DialogHeader>

        {/* A real form, like every other dialog in this module, so Enter advances the
            step instead of doing nothing. No `px-6`: DialogContent already pads, and
            the extra padding indented the step strip past the header above it. */}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            // React bubbles a portalled submit up the COMPONENT tree, so a caller that ever
            // renders this inside its own form must not receive the wizard's submit.
            event.stopPropagation();
            if (valueError && (step === 'detail' || step === 'confirm')) {
              setValueErrorShown(true);
              setStep('detail');
              return;
            }
            if (step === 'confirm') {
              void submit();
              return;
            }
            if (canAdvance) setStep(STEPS[stepIndex + 1].id);
          }}
        >
          <WizardSteps steps={STEPS} currentId={step} className="mb-4" />

          <DialogBody className="max-h-[60vh] space-y-4 overflow-y-auto">
            {step === 'development' && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="lead-title">
                    What is it <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    id="lead-title"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder="e.g. Tower behind the Setia showroom"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="lead-developer">Developer</Label>
                  <SearchableSelect
                    id="lead-developer"
                    value={developerPartyId}
                    onChange={setDeveloperPartyId}
                    clearable
                    loadError={developers.error}
                    onRetry={() => void developers.refetch()}
                    options={(developers.data?.data ?? []).map((party) => ({
                      value: party.id,
                      label: party.name,
                    }))}
                    placeholder="Search developers"
                    emptyMessage="No developers yet. Add one under Parties"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="lead-location">Location</Label>
                  <Input
                    id="lead-location"
                    value={location}
                    onChange={(event) => setLocation(event.target.value)}
                    placeholder="e.g. Setia Alam, Selangor"
                  />
                </div>
              </div>
            )}

            {step === 'informant' && (
              <InformantFieldset value={informant} onChange={setInformant} showTitle={false} />
            )}

            {step === 'detail' && (
              <div className="space-y-4">
                <div className="space-y-3">
                  {creatingCustomer ? (
                    <div className="space-y-3">
                      <div className="space-y-1.5">
                        <Label htmlFor="lead-new-customer">
                          Buyer name <span className="text-destructive">*</span>
                        </Label>
                        <Input
                          id="lead-new-customer"
                          value={newCustomer.customer_name}
                          onChange={(event) =>
                            setNewCustomer((previous) => ({
                              ...previous,
                              customer_name: event.target.value,
                            }))
                          }
                          placeholder="Sunway Construction Sdn Bhd"
                        />
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div className="space-y-1.5">
                          <Label htmlFor="lead-new-phone">Phone</Label>
                          <Input
                            id="lead-new-phone"
                            value={newCustomer.phone_number ?? ''}
                            onChange={(event) =>
                              setNewCustomer((previous) => ({
                                ...previous,
                                phone_number: event.target.value,
                              }))
                            }
                          />
                        </div>
                        <div className="space-y-1.5">
                          <Label htmlFor="lead-new-email">Email</Label>
                          <Input
                            id="lead-new-email"
                            type="email"
                            value={newCustomer.email ?? ''}
                            onChange={(event) =>
                              setNewCustomer((previous) => ({
                                ...previous,
                                email: event.target.value,
                              }))
                            }
                          />
                        </div>
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setCreatingCustomer(false)}
                      >
                        Choose an existing buyer
                      </Button>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <div className="space-y-1.5">
                        <Label htmlFor="lead-customer">Buyer</Label>
                        <SearchableSelect
                          id="lead-customer"
                          value={customerId}
                          onChange={setCustomerId}
                          clearable
                          options={(customers.data ?? []).map((row) => ({
                            value: row.id,
                            label: row.customer_name,
                            description: row.customer_code ?? undefined,
                          }))}
                          placeholder="Search buyers"
                          emptyMessage="No match"
                          createOption={{
                            label: (query) => <span>Create &ldquo;{query}&rdquo;</span>,
                            onCreate: (query) => {
                              setCustomerId('');
                              setNewCustomer({ customer_name: query });
                              setCreatingCustomer(true);
                            },
                          }}
                        />
                      </div>
                    </div>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="lead-value">Rough value (RM)</Label>
                  <NumberInput
                    id="lead-value"
                    min={0}
                    value={estimatedValue}
                    error={valueErrorShown && estimatedValue.trim() ? valueError : undefined}
                    onChange={(event) => setEstimatedValue(event.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="lead-notes">Notes</Label>
                  <Textarea
                    id="lead-notes"
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                    rows={3}
                  />
                </div>
              </div>
            )}

            {step === 'confirm' && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="lead-owner">Assign to</Label>
                  <SearchableSelect
                    id="lead-owner"
                    value={ownerUserId}
                    onChange={setOwnerUserId}
                    clearable
                    loadError={users.error}
                    onRetry={() => void users.refetch()}
                    options={(users.data ?? []).map((user) => ({
                      value: user.id,
                      label: user.name || 'Unnamed user',
                    }))}
                    placeholder="Select a salesperson"
                    emptyMessage="No match"
                  />
                </div>

                <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                  <Fact label="Development" value={title.trim()} />
                  <Fact
                    label="Developer"
                    value={
                      (developers.data?.data ?? []).find(
                        (party) => party.id === developerPartyId,
                      )?.name ?? null
                    }
                  />
                  <Fact label="Location" value={location.trim() || null} />
                  <Fact
                    label="Source"
                    value={
                      informantSourceLabel(informantBody(informant).informant_source) || null
                    }
                  />
                  <Fact label="Firm" value={informantParty} />
                  <Fact
                    label="Contact"
                    value={informant.informant_contact_name.trim() || null}
                  />
                  <Fact label="Reference" value={informant.informant_ref.trim() || null} />
                  <Fact
                    label="Buyer"
                    value={
                      creatingCustomer
                        ? `${newCustomer.customer_name.trim()} (new)`
                        : (selectedCustomer?.customer_name ?? null)
                    }
                    emptyText="Not known yet"
                  />
                  <Fact
                    label="Rough value"
                    value={estimatedValue.trim() ? `RM ${estimatedValue.trim()}` : null}
                  />
                </dl>
              </div>
            )}
          </DialogBody>

          {/* Back sits on the left because it walks the wizard backwards rather than
              leaving it; Cancel and the forward action keep the end alignment every
              other dialog in this module uses. */}
          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-between">
            <Button
              type="button"
              variant="outline"
              disabled={stepIndex === 0}
              onClick={() => setStep(STEPS[Math.max(stepIndex - 1, 0)].id)}
            >
              <ArrowLeft className="size-4" aria-hidden />
              Back
            </Button>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button type="button" variant="outline" onClick={() => onDone()}>
                Cancel
              </Button>
              {step === 'confirm' ? (
                <Button
                  type="submit"
                  disabled={create.isPending || assign.isPending || !developmentReady}
                >
                  Create lead
                </Button>
              ) : (
                <Button type="submit" disabled={!canAdvance}>
                  Next
                  <ArrowRight className="size-4" aria-hidden />
                </Button>
              )}
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Fact({
  label,
  value,
  emptyText = '-',
}: {
  label: string;
  value?: string | null;
  emptyText?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">
        {value ?? <span className="text-muted-foreground">{emptyText}</span>}
      </dd>
    </div>
  );
}
