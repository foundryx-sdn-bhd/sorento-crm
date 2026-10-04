'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { Badge } from '@/components/ui/badge';
import { PACKING_LIST_REGION_OPTIONS, regionLabel } from '@/lib/packing-list-regions';
import { ContainerVolumeFill } from '@/components/common/ContainerVolumeFill';
import { formatDate } from '@/lib/helpers';
import { useContainerSizes } from '@/app/(protected)/scm/hooks/useFulfilment';
import { CLEARANCE_ATTRIBUTE_FIELDS } from '../forms/packing-list-schema';
import { PackingListField } from './PackingListField';
import { SupplierCombobox } from './SupplierCombobox';
import { usePackingListRecord } from '../[id]/components/packing-list-context';

/** The header field this checkpoint is already edited elsewhere, so it is not asked for
 *  twice. `loading_date` and `etd_date` are explicit fields in the Container card (AC-F5),
 *  same reason `estimated_arrival_date` already was: it is also this record's own ETA. */
const CHECKPOINTS_RENDERED_ELSEWHERE = new Set([
  'loading_date',
  'etd_date',
  'estimated_arrival_date',
]);

/** The three attributes that print on the workbook's own header block (AC-F5 card 1)
 *  rather than the generic clearance-attributes grid below - moved out of the loop so
 *  they are not asked for twice. */
const CARD1_ATTRIBUTE_NAMES = new Set([
  'consignee',
  'china_forwarder',
  'delivery_warehouse',
  'free_days_available',
]);

/**
 * Everything about the container itself: what it is, when it moves, when it clears.
 *
 * View and edit are the SAME layout throughout - the same fields in the same order, and an
 * input where the value was. Every card and every row is rendered whether it has a value or
 * not: per the CRUD standard a section is never hidden on missing data, and "-" is the
 * honest answer for a container that has not cleared yet.
 *
 * Card order and field order mirror the RMB workbook top to bottom (AC-F5, ruling 3): the
 * header block she reads first (Container), then the clearance trail, then the document-level
 * fields the workbook does not print at all.
 *
 * No Costs card (R17, purchasing consolidation batch 6 Sep 2026): clearance / freight /
 * insurance are not needed on screen. The three columns stay in the DB untouched - the
 * AutoCount ingest contract may still read them - only this view and the export drop them.
 */
export function PackingListDetailsTab() {
  const {
    packingList,
    editing,
    draft,
    setField,
    suppliers,
    lineSupplierNames,
    checkpoints,
    canReadScm,
  } = usePackingListRecord();

  // An SCM read (403 without SCM read). The view reads `container_size_code` off the
  // record, so only the edit select's options go without it (PL-TABS-ACCESS).
  const containerSizes = useContainerSizes({ enabled: canReadScm });

  if (!packingList) return null;
  const record = packingList as unknown as Record<string, unknown>;

  const containerSizeOptions = (containerSizes.data ?? []).map((s) => ({
    value: s.id,
    label: `${s.code} - ${s.cbm} cbm${s.is_default ? ' (default)' : ''}`,
  }));
  const defaultContainerSize = (containerSizes.data ?? []).find((s) => s.is_default) ?? null;

  // Total items from the lines when there are any (the source of truth), else the header's
  // own figure. Derived by the backend, so it has no input counterpart.
  const totalItemsFromLines =
    packingList.shipment_lines?.reduce(
      (sum, line) => sum + (line.quantity_shipped ?? 0),
      0,
    ) ?? 0;
  const displayTotalItems =
    packingList.shipment_lines?.length && totalItemsFromLines > 0
      ? totalItemsFromLines
      : packingList.total_items_shipped ?? 0;

  const text = (value: unknown) =>
    value === null || value === undefined || value === '' ? '-' : String(value);

  /** A checkpoint's admin-configured label (Status Graphs), falling back to the sheet's own
   *  wording while the config has not loaded yet. */
  const checkpointLabel = (field: string, fallback: string) =>
    checkpoints.find((cp) => cp.field === field)?.label ?? fallback;

  const card1Attributes = CLEARANCE_ATTRIBUTE_FIELDS.filter((f) =>
    CARD1_ATTRIBUTE_NAMES.has(f.name),
  );
  const card1AttributeByName = new Map(card1Attributes.map((f) => [f.name, f]));

  return (
    <div className="space-y-6">
      {/* The workbook's own header block, in its own printed order: Loading, ETD, ETA,
          Container, Seal, SO, Consignee, Shipper, China agent, Factory, Free days,
          Delivery warehouse. */}
      <Card>
        <CardHeader>
          <CardTitle>Container</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <PackingListField
              label={checkpointLabel('loading_date', 'Loading date')}
              name="loading_date"
              type="date"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={
                record.loading_date
                  ? formatDate(new Date(String(record.loading_date)))
                  : '-'
              }
            />
            <PackingListField
              label={checkpointLabel('etd_date', 'ETD')}
              name="etd_date"
              type="date"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={record.etd_date ? formatDate(new Date(String(record.etd_date))) : '-'}
            />
            <PackingListField
              label={checkpointLabel('estimated_arrival_date', 'ETA')}
              name="estimated_arrival_date"
              type="date"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={
                packingList.estimated_arrival_date
                  ? formatDate(new Date(packingList.estimated_arrival_date))
                  : '-'
              }
            />
            <PackingListField
              label="Container no"
              name="shipping_container_number"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={packingList.shipping_container_number || '-'}
            />
            <PackingListField
              label="Seal no"
              name="seal_number"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={packingList.seal_number || '-'}
            />
            <PackingListField
              label="SO"
              name="forwarder_order_ref"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={packingList.forwarder_order_ref || '-'}
            />
            <PackingListField
              label={card1AttributeByName.get('consignee')?.label ?? 'Consignee'}
              name="consignee"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={text(record.consignee)}
            />
            <div className="min-w-0">
              <p className="text-sm text-muted-foreground">Regions</p>
              {editing ? (
                <SearchableMultiSelect
                  triggerClassName="mt-1"
                  size="sm"
                  value={(draft.regions ?? '').split(',').filter(Boolean)}
                  onChange={(next) => {
                    if (next.length > 0) setField('regions', next.join(','));
                  }}
                  options={PACKING_LIST_REGION_OPTIONS}
                />
              ) : (
                <div className="mt-1 flex flex-wrap gap-1">
                  {(packingList.regions ?? []).length === 0
                    ? '-'
                    : (packingList.regions ?? []).map((code) => (
                        <Badge key={code} variant="outline" size="sm">
                          {regionLabel(code)}
                        </Badge>
                      ))}
                </div>
              )}
            </div>
            <PackingListField
              label="Shipper"
              name="shipper"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={packingList.shipper || '-'}
            />
            <PackingListField
              label="China agent"
              name="china_forwarder"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={text(record.china_forwarder)}
            />
            <div className="min-w-0">
              {/* Derived from the lines' own suppliers - who loaded a mixed container is a
                  fact of what shipped, not something typed on the header. */}
              <p className="text-sm text-muted-foreground">Factory</p>
              <p className="font-medium break-words">{lineSupplierNames || '-'}</p>
            </div>
            <PackingListField
              label={card1AttributeByName.get('free_days_available')?.label ?? 'Free days'}
              name="free_days_available"
              type="number"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={text(record.free_days_available)}
            />
            <PackingListField
              label={card1AttributeByName.get('delivery_warehouse')?.label ?? 'Delivery warehouse'}
              name="delivery_warehouse"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={text(record.delivery_warehouse)}
            />
            <div className="min-w-0">
              <p className="text-sm text-muted-foreground">Container size</p>
              {/* Without SCM read the sizes cannot be listed, so the field stays a value:
                  an empty select would show the placeholder over a set size, with a clear
                  button that wipes it unseen (PL-TABS-ACCESS). */}
              {editing && canReadScm ? (
                <SearchableSelect
                  triggerClassName="mt-1"
                  size="sm"
                  value={draft.container_size_id ?? ''}
                  onChange={(v: string) => setField('container_size_id', v)}
                  loadError={containerSizes.error}
                  onRetry={() => void containerSizes.refetch()}
                  options={containerSizeOptions}
                  placeholder={
                    defaultContainerSize
                      ? `${defaultContainerSize.code} (default)`
                      : 'Default size'
                  }
                  clearable
                />
              ) : (
                <p className="font-medium break-words">
                  {packingList.container_size_code ?? '-'}
                </p>
              )}
            </div>
          </div>
          {/* The fill gauge (S5, ruling 1): the shipment's own lines against the size
              above, moved here from the proforma invoice - a packing list routinely
              consolidates several PIs, so capacity is a fact about the container, not any
              one of them. */}
          <ContainerVolumeFill
            className="mt-4 max-w-xl"
            totalCbm={packingList.total_cbm ?? null}
            containerCbm={packingList.container_cbm ?? null}
            containerLabel={packingList.container_size_code ?? null}
            unmeasuredLines={packingList.unmeasured_lines ?? 0}
          />
        </CardContent>
      </Card>

      {/* The rest of the clearance trail. The workbook import normally fills these; before
          the first import - or when a liner revises an ETA between imports - somebody types
          one, and the ADR asks for the record to be edited where it is read. */}
      <Card>
        <CardHeader>
          <CardTitle>Clearance</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {checkpoints
              .filter((cp) => !CHECKPOINTS_RENDERED_ELSEWHERE.has(cp.field))
              .map((cp) => {
                const value = record[cp.field];
                return (
                  <PackingListField
                    key={cp.field}
                    label={cp.label}
                    name={cp.field}
                    type="date"
                    editing={editing}
                    draft={draft}
                    onChange={setField}
                    view={value ? formatDate(new Date(String(value))) : '-'}
                  />
                );
              })}
            {CLEARANCE_ATTRIBUTE_FIELDS.filter((f) => !CARD1_ATTRIBUTE_NAMES.has(f.name)).map(
              (f) => (
                <PackingListField
                  key={f.name}
                  label={f.label}
                  name={f.name}
                  editing={editing}
                  draft={draft}
                  onChange={setField}
                  view={text(record[f.name])}
                />
              ),
            )}
            <div className="min-w-0">
              {/* Provenance, not an editable attribute - it says which workbook tab the
                  row came from, so it is shown but never typed. */}
              <p className="text-sm text-muted-foreground">Source sheet</p>
              <p className="font-medium break-words">{packingList.source_sheet || '-'}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Document-level fields the workbook itself does not print - our own record of the
          shipment, never asked twice against the header block above. */}
      <Card>
        <CardHeader>
          <CardTitle>Document</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <PackingListField
              label="Shipment number"
              name="shipment_number"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={packingList.shipment_number || '-'}
            />
            <div>
              <p className="text-sm text-muted-foreground">Supplier</p>
              {editing ? (
                <SupplierCombobox
                  className="mt-1"
                  value={draft.supplier_id ?? ''}
                  onChange={(v) => setField('supplier_id', v)}
                  suppliers={suppliers}
                  supplierFallback={packingList.supplier ?? null}
                  placeholder="No supplier on the header"
                />
              ) : (
                <p className="font-medium">
                  {packingList.supplier?.supplier_name || lineSupplierNames || '-'}
                </p>
              )}
            </div>
            <PackingListField
              label="Shipment date"
              name="shipment_date"
              type="date"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={
                packingList.shipment_date
                  ? formatDate(new Date(packingList.shipment_date))
                  : '-'
              }
            />
            <PackingListField
              label="Actual arrival"
              name="actual_arrival_date"
              type="date"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={
                packingList.actual_arrival_date
                  ? formatDate(new Date(packingList.actual_arrival_date))
                  : '-'
              }
            />
            <PackingListField
              label="Bill of lading"
              name="bill_of_lading_number"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={packingList.bill_of_lading_number || '-'}
            />
            <PackingListField
              label="Invoice number"
              name="invoice_number"
              editing={editing}
              draft={draft}
              onChange={setField}
              view={packingList.invoice_number || '-'}
            />
            <div>
              {/* Derived from the lines by the backend, so it has no input counterpart -
                  typing our own here would let the two disagree. */}
              <p className="text-sm text-muted-foreground">Total items</p>
              <p className="font-medium">{displayTotalItems}</p>
            </div>
          </div>
          {/* Always rendered, not only when it has a value: a field that appears solely in
              edit mode is a field nobody knows exists. */}
          <div>
            <p className="text-sm text-muted-foreground">Notes</p>
            {editing ? (
              <Textarea
                className="mt-1"
                rows={3}
                value={draft.notes ?? ''}
                onChange={(e) => setField('notes', e.target.value)}
                aria-label="Notes"
              />
            ) : (
              <p className="font-medium">{packingList.notes || '-'}</p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default PackingListDetailsTab;
