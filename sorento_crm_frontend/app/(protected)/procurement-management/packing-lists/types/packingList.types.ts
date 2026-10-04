export interface InboundShipmentLine {
  id: string;
  shipment_id: string;
  product_id: string;
  quantity_shipped: number;
  /**
   * Whose line this is. One container is routinely loaded by two or three factories,
   * and the header supplier is null once it is mixed - the line is then the only
   * place the attribution survives. Null on a line nobody has claimed.
   */
  supplier_id?: string | null;
  /** Volume as the packing list stated it. Decimal on the wire, so a string is possible. */
  cbm?: number | string | null;
  /** The supplier's own note on the line. */
  remarks?: string | null;
  /**
   * The supplier's own wording for the item (S9), copied off the proforma invoice line at
   * convert. Editable on the Shipment lines tab. Null on a line drafted before this field
   * existed or uploaded with no description column of its own - the grid and the export
   * both fall back to the product's name then.
   */
  description?: string | null;
  uom_id?: string | null;
  batch_number?: string | null;
  serial_number_range_from?: string | null;
  serial_number_range_to?: string | null;
  carton_number?: string | null;
  cartons_count: number;
  weight_per_carton?: number | null;
  unit_cost?: number | null;
  /**
   * What `unit_cost` is stated in. On the line since S3b; carried on this type since the
   * container workbook, because a save that round-tripped the price and dropped the
   * currency handed the backend a number with no meaning.
   */
  currency?: string | null;
  /**
   * What the container workbook measures the line by. Editable on the Shipment lines tab -
   * they come off the supplier's own file and it is not always right.
   * Lengths in CENTIMETRES; the weights are per CARTON, and the sheet multiplies them by
   * the carton count itself rather than storing a total that could disagree with them.
   * Decimal on the wire, so a string is possible.
   */
  material?: string | null;
  pcs_per_carton?: number | string | null;
  carton_length_cm?: number | string | null;
  carton_width_cm?: number | string | null;
  carton_height_cm?: number | string | null;
  net_weight_per_carton?: number | string | null;
  gross_weight_per_carton?: number | string | null;
  /** Total SPO allocated quantity for this product on this shipment (all warehouses). */
  spo_allocated_quantity?: number | null;
  /** Sum of quantity_received from SPO allocations for this line. */
  quantity_received?: number | null;
  /** Stored in DB: in_transit, allocated, partially_allocated, received, partially_received (for n8n/API). */
  line_status?: string | null;
  product?: {
    id: string;
    product_code: string;
    product_name: string;
  };
  related_spo_allocations?: Array<{
    id: string;
    spo_number?: string | null;
    allocated_quantity?: number | null;
    receipt_status?: string | null;
  }>;
  related_grns?: Array<{
    id: string;
    picking_number?: string | null;
    spo_number?: string | null;
    picking_status?: string | null;
    picking_date?: string | Date | null;
  }>;
}

export interface PackingList extends ClearanceFields {
  id: string;
  shipment_number: string | null;
  /** Null on a mixed container - the header names no factory once two of them loaded it. */
  supplier_id: string | null;
  shipment_date: Date;
  estimated_arrival_date?: Date | null;
  actual_arrival_date?: Date | null;
  bill_of_lading_number?: string | null;
  shipping_container_number?: string | null;
  invoice_number?: string | null;
  shipment_status: string;
  total_items_shipped?: number | null;
  total_cartons?: number | null;
  notes?: string | null;
  /** 'west' / 'east'; never empty. */
  regions?: string[];
  /**
   * The container's own paperwork and its costs, as the workbook prints them.
   *
   * NOT on `ClearanceFields`: that interface mirrors the backend's one-for-one and a
   * pytest parity check fails if either side gains a field the Container Status sheet
   * does not contribute. These come from the container workbook instead.
   */
  seal_number?: string | null;
  shipper?: string | null;
  /** The forwarder's booking reference (`SO :` on the sheet). "SO" here is a sales order. */
  forwarder_order_ref?: string | null;
  /** Typed per container: the sheet apportions them between the companies itself. */
  clearance_cost?: number | string | null;
  china_freight_cost?: number | string | null;
  insurance_rate?: number | string | null;
  /**
   * Which box this container is being loaded into (S5, ruling 1). Null = the tenant
   * default. The fill gauge fields below are computed server-side onto the create /
   * update / detail read - `container_size_code`, `container_cbm`, `total_cbm`,
   * `fill_pct`, `over_by_cbm` are absent on the LIST row (the list endpoint's response
   * does not carry them; only `getPackingList` / create / update do).
   */
  container_size_id?: string | null;
  container_size_code?: string | null;
  container_cbm?: number | null;
  /** Sum of the shipment lines' cbm. Null when none of them state a volume. */
  total_cbm?: number | null;
  unmeasured_lines?: number;
  fill_pct?: number | null;
  /** Only when it is over: the cbm above capacity. */
  over_by_cbm?: number | null;
  created_at: Date;
  created_by?: string | null;
  updated_at: Date;
  attachment_id?: string | null;
  synced_to_excel: boolean;
  last_synced_to_excel?: Date | null;
  supplier?: {
    id: string;
    supplier_code: string;
    supplier_name: string;
  };
  attachment?: {
    id: string;
    original_filename: string;
    stored_filename: string;
    file_path: string;
    file_size_bytes: number | null;
    mime_type: string | null;
    attachment_type?: {
      id: string;
      type_name: string;
    } | null;
  } | null;
  lines_count?: number;
  spo_allocations_count?: number;
  display_total_items?: number | null;
  display_total_cartons?: number | null;
}

/**
 * Container-status clearance fields.
 *
 * PHASE 1 CONTRACT (mocked). These arrive on the same `inbound_shipments` row as
 * the packing list: one Container Status sheet row = one packing list. Every field
 * is optional because a caller without entitlement receives the keys **absent**,
 * not null - absent means "you may not see this", null means "not reached yet".
 * See documentation/plans/purchasing/container-status-tracking-acceptance-criteria.md
 * sections B and C.
 */
export interface ClearanceFields {
  /** Yard / warehouse location code (sheet column LOC). */
  loc?: string | null;
  liner_code?: string | null;
  china_forwarder?: string | null;
  malaysia_forwarder?: string | null;
  consignee?: string | null;
  /** Free days before demurrage / detention starts. */
  free_days_available?: number | null;
  stacked?: string | null;

  loading_date?: string | null;
  etc_date?: string | null;
  etd_date?: string | null;
  /** First-published ETA. */
  /** Revised ETA - the accurate one. Doubles as de-facto arrival. */
  eta_delay_date?: string | null;
  inspection_date?: string | null;
  approval_date?: string | null;
  gatepass_date?: string | null;
  delivery_warehouse?: string | null;
  warehouse_arrival_date?: string | null;
  informed_collection_date?: string | null;
  collection_date?: string | null;

  /*
   * ATA, ORI DOC RECEIVED, K1 SUBMISSION and YARD ARRIVALS are deliberately absent.
   * They exist on the sheet but are filled 6 / 4 / 4 / 4 times across 407
   * containers and nothing reads them, so they are not columns, not imported and
   * not shown. The retained source file keeps the history (D34).
   */

  coa_permit_no?: string | null;
  /** Which workbook tab the row came from. Traceability only - never derives status. */
  source_sheet?: string | null;
}

export interface PackingListDetail extends PackingList {
  shipment_lines?: InboundShipmentLine[];
}

export interface PackingListFormData {
  /**
   * `null` CLEARS the field; omitting it leaves it alone (the backend PUT is
   * `exclude_unset`). Every optional field here is nullable for that reason - an edit that
   * could set a value but never unset one is half an edit, and it reads as a save that did
   * not work.
   */
  shipment_number?: string | null;
  supplier_id?: string | null;
  /** Nullable like every other date here (AC-F3): the backend's update schema already treats
   *  it as optional, and sending the raw empty string on a clear used to 422 instead. */
  shipment_date?: string | null;
  estimated_arrival_date?: string | null;
  actual_arrival_date?: string | null;
  bill_of_lading_number?: string | null;
  shipping_container_number?: string | null;
  invoice_number?: string | null;
  shipment_status?: string;
  total_items_shipped?: number;
  total_cartons?: number;
  notes?: string | null;
  /** Omitted on create = West only; on update omitted = unchanged. Never empty. */
  regions?: string[];
  attachment_id?: string | null;
  seal_number?: string | null;
  shipper?: string | null;
  forwarder_order_ref?: string | null;
  clearance_cost?: number | null;
  china_freight_cost?: number | null;
  insurance_rate?: number | null;
  /** The Container card's own select (S5). Null clears it back to the tenant default. */
  container_size_id?: string | null;
  shipment_lines?: Array<{
    /** The existing line's id; absent on a line just added. The backend claims by id
     *  first, which is the only way to edit two lines of one product from one supplier. */
    id?: string;
    product_id: string;
    quantity_shipped: number;
    /** Round-tripped on save so an edit does not strip a line's factory. */
    supplier_id?: string;
    uom_id?: string;
    batch_number?: string;
    serial_number_range_from?: string;
    serial_number_range_to?: string;
    carton_number?: string;
    cartons_count?: number;
    weight_per_carton?: number;
    unit_cost?: number;
    /** Round-tripped on save, or the PUT hands the backend a price with no unit. */
    currency?: string;
    /** Read off the supplier's file, corrected here. Centimetres; weights per carton. */
    material?: string;
    pcs_per_carton?: number;
    carton_length_cm?: number;
    carton_width_cm?: number;
    carton_height_cm?: number;
    net_weight_per_carton?: number;
    gross_weight_per_carton?: number;
    /** Volume, editable in place on the Lines tab since F9 - the column has existed on the
     *  line since S3b and only the importer could ever fill it. */
    cbm?: number;
    /** Absent = unchanged, `null` = clear it - the same absent-vs-null rule the header
     *  fields above use, so a blanked Remarks/Description on save actually clears. */
    remarks?: string | null;
    /** The supplier's own wording for the item (S9), editable in the lines grid. */
    description?: string | null;
  }>;
}
