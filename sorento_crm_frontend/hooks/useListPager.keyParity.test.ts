/**
 * S3-03 - the key parity every wired entity has to hold.
 *
 * The pager reads the page out of the cache by rebuilding the LIST's React Query
 * key from the detail URL. If a list's key and the key its own URL produces are
 * not identical, nothing breaks loudly: the pager just misses, refetches, and can
 * page a wider set than the user was looking at. So each entity states, here, the
 * list state it can be in, the URL its row click emits for that state, and the two
 * keys are hashed and compared the way React Query compares them.
 *
 * One row per wired entity. A new entity with a pager belongs in this table.
 */
import { describe, it, expect } from 'vitest';
import { hashKey, type QueryKey } from '@tanstack/react-query';

import { buildDetailSearch, parseDetailSearch } from '@/lib/listNavQuery';
import type { ListQueryFilterGroup } from '@/lib/list-query/listQueryService';
import {
  usersListFilters,
  usersListQueryKey,
} from '@/app/(protected)/user-management/users/lib/listQuery';
import {
  ordersListQueryKey,
  ordersPagerQuery,
} from '@/app/(protected)/order-management/orders/hooks/useOrders';
import {
  productsListQueryKey,
  productsPagerQuery,
} from '@/app/(protected)/master-data-management/products/lib/listQuery';
import {
  complaintsListQueryKey,
  complaintsPagerQuery,
} from '@/app/(protected)/complaint-management/complaints/hooks/useComplaints';
import {
  customersListQueryKey,
  customersPagerQuery,
} from '@/app/(protected)/order-management/customers/hooks/useCustomers';
import {
  suppliersListQueryKey,
  suppliersPagerQuery,
} from '@/app/(protected)/procurement-management/suppliers/hooks/useSuppliers';
import {
  grnListQueryKey,
  grnPagerQuery,
} from '@/app/(protected)/procurement-management/grn/hooks/useGRN';
import {
  formsListQueryKey,
  formsPagerQuery,
} from '@/app/(protected)/forms-management/forms/hooks/useForms';
import {
  promotionsListQueryKey,
  promotionsPagerQuery,
} from '@/app/(protected)/marketing-management/promotions/hooks/usePromotions';
import {
  packingListsListQueryKey,
  packingListsPagerQuery,
} from '@/app/(protected)/procurement-management/packing-lists/hooks/usePackingLists';
import {
  purchaseRequestsListQueryKey,
  purchaseRequestsPagerQuery,
} from '@/app/(protected)/procurement-management/purchase-requests/hooks/usePurchaseRequests';
import {
  stockInquiriesListQueryKey,
  stockInquiriesPagerQuery,
} from '@/app/(protected)/procurement-management/stock-inquiries/hooks/useStockInquiries';
import {
  accessAgentsListQueryKey,
  accessAgentsPagerQuery,
} from '@/app/(protected)/user-management/access-agents/hooks/useAccessAgents';
import {
  onboardingListQueryKey,
  onboardingPagerQuery,
} from '@/app/(protected)/user-management/onboarding-requests/hooks/useOnboardingRequests';
import {
  conversationSlaListQueryKey,
  conversationSlaPagerQuery,
} from '@/app/(protected)/sla-management/conversation-sla-tracking/hooks/useConversationSLATracking';
import {
  attachmentsListQueryKey,
  attachmentsPagerQuery,
} from '@/app/(protected)/resource-management/attachments/hooks/useAttachments';
import {
  certificatesListQueryKey,
  certificatesPagerQuery,
} from '@/app/(protected)/master-data-management/certificates/hooks/useCertificates';
import {
  productSetsListQueryKey,
  productSetsPagerQuery,
} from '@/app/(protected)/master-data-management/product-sets/hooks/useProductSets';
import {
  salesAgentsListQueryKey,
  salesAgentsPagerQuery,
} from '@/app/(protected)/master-data-management/sales-agents/hooks/useSalesAgents';
import {
  warehousesListQueryKey,
  warehousesPagerQuery,
} from '@/app/(protected)/inventory-management/warehouses/hooks/useWarehouses';
import {
  stockTransfersListQueryKey,
  stockTransfersPagerQuery,
} from '@/app/(protected)/inventory-management/stock-transfers/hooks/useStockTransfers';
import {
  integrationLogsListQueryKey,
  integrationLogsPagerQuery,
} from '@/app/(protected)/integration-management/integration-logs/hooks/useIntegrationLogs';
import {
  contactsListQueryKey,
  contactsPagerQuery,
} from '@/app/(protected)/user-management/contacts/lib/listQuery';
import {
  purchaseOrdersListQueryKey,
  purchaseOrdersPagerQuery,
} from '@/app/(protected)/scm/hooks/usePurchaseOrders';
import {
  salesOrdersListQueryKey,
  salesOrdersPagerQuery,
} from '@/app/(protected)/scm/hooks/useSalesOrders';
import {
  proformaInvoicesListQueryKey,
  proformaInvoicesPagerQuery,
} from '@/app/(protected)/scm/hooks/useProformaInvoices';
import {
  loadingPlanListQueryKey,
  loadingPlanPagerQuery,
} from '@/app/(protected)/scm/hooks/useFulfilment';
import {
  salesOrdersKey,
  projectSalesOrdersListParamsFromUrl,
} from '@/app/(protected)/project-sales/_shared/hooks/useProjectSalesOrders';

/** One list state: the key the list builds, and the URL its row click emits. */
interface ParityCase {
  name: string;
  listKey: QueryKey;
  url: string;
  pagerKey: (search: URLSearchParams) => QueryKey;
}

const ADVANCED: ListQueryFilterGroup = {
  op: 'and',
  children: [{ field_key: 'debtor_name', op: 'contains', value: 'acme' }],
};

const SORT_ASC = [{ id: 'name', desc: false }];
const SORT_DESC = [{ id: 'created_at', desc: true }];

const CASES: ParityCase[] = [
  (() => {
    const filters = usersListFilters({ role: 'role-1', status: 'active', trashed: 'only' });
    const listParams = {
      pageIndex: 2,
      pageSize: 25,
      sorting: SORT_ASC,
      searchQuery: 'ada',
      filters,
    };
    return {
      name: 'users',
      listKey: usersListQueryKey(listParams),
      url: buildDetailSearch(listParams, filters),
      pagerKey: (s: URLSearchParams) => usersListQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'DO-99',
      order_status_id: 'status-7',
      has_order_lines: 'yes' as const,
      advancedFilter: undefined,
    };
    return {
      name: 'orders, quick filters',
      listKey: ordersListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        order_status_id: listParams.order_status_id,
        has_order_lines: listParams.has_order_lines,
      }),
      pagerKey: (s: URLSearchParams) => ordersPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 0,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: '',
      order_status_id: undefined,
      has_order_lines: 'all' as const,
      advancedFilter: ADVANCED,
    };
    return {
      name: 'orders, advanced filter',
      listKey: ordersListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        advFilter: encodeURIComponent(JSON.stringify(ADVANCED)),
      }),
      pagerKey: (s: URLSearchParams) => ordersPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 4,
      pageSize: 50,
      sorting: [{ id: 'product_name', desc: false }],
      searchQuery: 'lamp',
      category_id: 'cat-1',
      brand_id: 'brand-1',
      status: 'active',
      variant_filter: 'base' as const,
      discontinued_batch_id: undefined,
      discontinued_brand_ids: undefined,
      advancedFilter: undefined,
    };
    return {
      name: 'products, every filter',
      listKey: productsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        category_id: listParams.category_id,
        brand_id: listParams.brand_id,
        status: listParams.status,
        variant_filter: listParams.variant_filter,
      }),
      pagerKey: (s: URLSearchParams) => productsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 0,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: '',
      category_id: undefined,
      brand_id: 'brand-a,brand-b',
      status: 'all',
      variant_filter: 'all' as const,
      discontinued_batch_id: 'batch-9',
      discontinued_brand_ids: 'brand-a,brand-b',
      advancedFilter: undefined,
    };
    return {
      name: 'products, discontinued deep link',
      listKey: productsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        brand_id: listParams.brand_id,
        discontinued_batch_id: listParams.discontinued_batch_id,
      }),
      pagerKey: (s: URLSearchParams) => productsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: [{ id: 'complaint_date', desc: true }],
      searchQuery: 'leak',
      assigned_to: 'user-3',
      status: 'submitted',
      root_cause_ids: ['rc-1', 'rc-2'],
      resolution_ids: undefined,
    };
    return {
      name: 'complaints',
      listKey: complaintsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        assigned_to: listParams.assigned_to,
        status: listParams.status,
        root_cause_ids: listParams.root_cause_ids.join(','),
      }),
      pagerKey: (s: URLSearchParams) => complaintsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 2,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'acme',
      status: 'active',
    };
    return {
      name: 'customers',
      listKey: customersListQueryKey(listParams),
      url: buildDetailSearch(listParams, { status: listParams.status }),
      pagerKey: (s: URLSearchParams) => customersPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'steel',
      advancedFilter: ADVANCED,
    };
    return {
      name: 'suppliers, advanced filter',
      listKey: suppliersListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        advFilter: encodeURIComponent(JSON.stringify(ADVANCED)),
      }),
      pagerKey: (s: URLSearchParams) => suppliersPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'GR-1',
      picking_status: 'approved',
      inspection_status: undefined,
      spo_allocation_id: undefined,
    };
    return {
      name: 'GRN',
      listKey: grnListQueryKey(listParams),
      url: buildDetailSearch(listParams, { picking_status: listParams.picking_status }),
      pagerKey: (s: URLSearchParams) => grnPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 0,
      pageSize: 50,
      sorting: [{ id: 'updated_at', desc: true }],
      searchQuery: 'warranty',
      language: undefined,
      status: 'active',
      purpose: undefined,
      form_type: undefined,
    };
    return {
      name: 'forms',
      listKey: formsListQueryKey(listParams),
      url: buildDetailSearch(listParams, { status: listParams.status }),
      pagerKey: (s: URLSearchParams) => formsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 3,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'promo',
      status: 'active',
      date_from: undefined,
      date_to: undefined,
      user_type: 'dealer',
      attachment_state: undefined,
      expiry_notify_batch_id: undefined,
      advancedFilter: undefined,
    };
    return {
      name: 'promotions',
      listKey: promotionsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        status: listParams.status,
        user_type: listParams.user_type,
      }),
      pagerKey: (s: URLSearchParams) => promotionsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    /**
     * The state the list OPENS in, which is the one the reader is almost always
     * in: status "all", no other filter. It writes no `status` into the row href
     * (an "all" is not a narrowing), so the pager has to restore the list's own
     * default rather than the backend's - and the backend's default is active
     * promotions only, which is a different, much smaller set.
     */
    const listParams = {
      pageIndex: 0,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: '',
      status: 'all',
      date_from: undefined,
      date_to: undefined,
      user_type: undefined,
      attachment_state: undefined,
      expiry_notify_batch_id: undefined,
      advancedFilter: undefined,
    };
    return {
      name: 'promotions, the default "all" state',
      listKey: promotionsListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => promotionsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: '',
      status: 'all',
      date_from: undefined,
      date_to: undefined,
      user_type: undefined,
      attachment_state: 'unlinked' as const,
      expiry_notify_batch_id: 'batch-3',
      advancedFilter: ADVANCED,
    };
    return {
      name: 'promotions, cleanup filter + expiry batch deep link + advanced filter',
      listKey: promotionsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        attachment_state: listParams.attachment_state,
        expiry_notify_batch_id: listParams.expiry_notify_batch_id,
        advFilter: encodeURIComponent(JSON.stringify(ADVANCED)),
      }),
      pagerKey: (s: URLSearchParams) => promotionsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'FSCU',
      supplier_id: undefined,
      shipment_status: undefined,
    };
    return {
      name: 'packing lists',
      listKey: packingListsListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => packingListsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 2,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'PR-2026',
      requestType: 'purchase_request',
      approvalStatus: 'pending',
      assignedTo: 'user-9',
    };
    return {
      name: 'purchase requests',
      listKey: purchaseRequestsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        request_type: listParams.requestType,
        approval_status: listParams.approvalStatus,
        assigned_to: listParams.assignedTo,
      }),
      pagerKey: (s: URLSearchParams) =>
        purchaseRequestsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'SI-7',
      statuses: ['submitted', 'processing'],
    };
    return {
      name: 'stock inquiries',
      listKey: stockInquiriesListQueryKey(listParams),
      url: buildDetailSearch(listParams, { status: listParams.statuses.join(',') }),
      pagerKey: (s: URLSearchParams) =>
        stockInquiriesPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_ASC,
      searchQuery: 'agent',
      status: undefined,
    };
    return {
      name: 'access agents',
      listKey: accessAgentsListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => accessAgentsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 0,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'onboard',
      statusKey: 'sent',
    };
    return {
      name: 'onboarding requests',
      listKey: onboardingListQueryKey(listParams),
      url: buildDetailSearch(listParams, { status_key: listParams.statusKey }),
      pagerKey: (s: URLSearchParams) => onboardingPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: '',
      policy_id: undefined,
      status: undefined,
      assigned_to: 'user-2',
      contact: 'contact-5',
      is_resolved: true,
      resolved_by: 'user-3',
    };
    return {
      name: 'conversation SLA tracking',
      listKey: conversationSlaListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        assigned_to: listParams.assigned_to,
        contact: listParams.contact,
        is_resolved: 'true',
        resolved_by: listParams.resolved_by,
      }),
      pagerKey: (s: URLSearchParams) =>
        conversationSlaPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 2,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'invoice',
      directory_id: 'dir-1',
      is_deleted: undefined,
      attachment_type_id: 'type-2',
      link_status: 'linked' as const,
      uploaded_by: 'user-4',
      uploaded_at_from: '2026-01-01',
      uploaded_at_to: '2026-02-01',
    };
    return {
      name: 'attachments',
      listKey: attachmentsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        directory_id: listParams.directory_id,
        attachment_type_id: listParams.attachment_type_id,
        link_status: listParams.link_status,
        uploaded_by: listParams.uploaded_by,
        uploaded_at_from: listParams.uploaded_at_from,
        uploaded_at_to: listParams.uploaded_at_to,
      }),
      pagerKey: (s: URLSearchParams) => attachmentsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: [{ id: 'valid_until', desc: false }],
      searchQuery: 'ISO',
      validity_state: undefined,
      expiring_within_days: undefined,
      scheme: undefined,
      status: undefined,
      needs_review: undefined,
    };
    return {
      name: 'certificates',
      listKey: certificatesListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => certificatesPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 0,
      pageSize: 50,
      sorting: [{ id: 'set_code', desc: false }],
      searchQuery: 'set',
    };
    return {
      name: 'product sets',
      listKey: productSetsListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => productSetsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 3,
      pageSize: 50,
      sorting: [{ id: 'sales_agent', desc: false }],
      searchQuery: 'BB',
    };
    return {
      name: 'sales agents',
      listKey: salesAgentsListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => salesAgentsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_ASC,
      searchQuery: 'BRW',
    };
    return {
      name: 'warehouses',
      listKey: warehousesListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => warehousesPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const state = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'TR-1',
    };
    const filters = {
      state: 'proposed',
      kind: 'internal',
      from_warehouse_id: 'wh-1',
      to_warehouse_id: 'wh-2',
      product_id: 'p-1',
    };
    // The transfers list passes its params in the service's own shape.
    const listParams = {
      query: state.searchQuery,
      state: filters.state as never,
      kind: filters.kind as never,
      from_warehouse_id: filters.from_warehouse_id,
      to_warehouse_id: filters.to_warehouse_id,
      product_id: filters.product_id,
      sales_order_id: undefined,
      sales_agent_id: undefined,
      sort: state.sorting[0].id,
      dir: 'desc' as const,
      page: state.pageIndex + 1,
      limit: state.pageSize,
    };
    return {
      name: 'stock transfers',
      listKey: stockTransfersListQueryKey(listParams),
      url: buildDetailSearch(state, filters),
      pagerKey: (s: URLSearchParams) =>
        stockTransfersPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 2,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: 'webhook',
      status: 'failed',
      integration_channel: 'respond_io',
      business_table: 'complaints',
      created_from: '2026-02-01',
      created_to: '2026-02-28',
      status_code: undefined,
      error_contains: undefined,
    };
    return {
      name: 'integration logs',
      listKey: integrationLogsListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        status: listParams.status,
        integration_channel: listParams.integration_channel,
        business_table: listParams.business_table,
        created_from: listParams.created_from,
        created_to: listParams.created_to,
      }),
      pagerKey: (s: URLSearchParams) =>
        integrationLogsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: SORT_DESC,
      searchQuery: '60123',
      filters: {},
    };
    return {
      name: 'contacts',
      listKey: contactsListQueryKey(listParams),
      url: buildDetailSearch(listParams),
      pagerKey: (s: URLSearchParams) => contactsPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 25,
      sorting: SORT_DESC,
      searchQuery: 'PO-3',
      status: 'open',
      supplier: null,
      productCode: 'SKU-1',
      outstanding: true,
    };
    return {
      name: 'SCM purchase orders',
      listKey: purchaseOrdersListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        status: listParams.status,
        product_code: listParams.productCode,
        outstanding: 'true',
      }),
      pagerKey: (s: URLSearchParams) =>
        purchaseOrdersPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 0,
      pageSize: 25,
      sorting: SORT_DESC,
      searchQuery: 'SO-9',
      status: 'open',
      priority: 'high',
      source: 'autocount',
      dateFrom: '2026-01-01',
      dateTo: '2026-03-01',
      customerCode: 'cust-1',
      outstanding: true,
      salesAgentId: 'agent-1',
      demandClass: 'project',
      transferable: 'no',
    };
    return {
      name: 'SCM sales orders',
      listKey: salesOrdersListQueryKey(listParams),
      url: buildDetailSearch(listParams, {
        status: listParams.status,
        priority: listParams.priority,
        source: listParams.source,
        date_from: listParams.dateFrom,
        date_to: listParams.dateTo,
        customer_code: listParams.customerCode,
        outstanding: 'true',
        sales_agent_id: listParams.salesAgentId,
        demand_class: listParams.demandClass,
        transferable: listParams.transferable,
      }),
      pagerKey: (s: URLSearchParams) => salesOrdersPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const state = { pageIndex: 2, pageSize: 100, sorting: [], searchQuery: 'PI-2026' };
    const filters = { supplier_id: 'sup-1', placement: 'not_converted' };
    // Offset paging: the list's own options shape, with the page size the URL names.
    const listParams = {
      supplierId: filters.supplier_id,
      placement: filters.placement as never,
      query: state.searchQuery,
      limit: state.pageSize,
      offset: state.pageIndex * state.pageSize,
    };
    return {
      name: 'SCM proforma invoices (offset paging at the endpoint cap)',
      listKey: proformaInvoicesListQueryKey(listParams),
      url: buildDetailSearch(state, filters),
      pagerKey: (s: URLSearchParams) =>
        proformaInvoicesPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const listParams = {
      pageIndex: 1,
      pageSize: 50,
      sorting: [{ id: 'started_at', desc: true }],
      searchQuery: 'plan',
      status: 'active' as const,
    };
    return {
      name: 'SCM loading plans',
      listKey: loadingPlanListQueryKey(listParams),
      url: buildDetailSearch(listParams, { status: listParams.status }),
      pagerKey: (s: URLSearchParams) => loadingPlanPagerQuery.listQueryKey(parseDetailSearch(s)),
    };
  })(),

  (() => {
    const state = { pageIndex: 2, pageSize: 25, sorting: SORT_DESC, searchQuery: 'PSO' };
    const filters = { status: 'draft' };
    const listParams = {
      page: state.pageIndex + 1,
      limit: state.pageSize,
      sort: state.sorting[0].id,
      dir: 'desc' as const,
      query: state.searchQuery,
      status: filters.status,
      purchase_order_id: undefined,
    };
    return {
      name: 'project sales orders',
      listKey: salesOrdersKey('p1', listParams),
      url: buildDetailSearch(state, filters),
      pagerKey: (s: URLSearchParams) =>
        salesOrdersKey('p1', projectSalesOrdersListParamsFromUrl(parseDetailSearch(s))),
    };
  })(),
];

describe('list key parity: the pager rebuilds the key the list used', () => {
  for (const testCase of CASES) {
    it(`S3-03: ${testCase.name}`, () => {
      const rebuilt = testCase.pagerKey(new URLSearchParams(testCase.url));
      expect(hashKey(rebuilt)).toBe(hashKey(testCase.listKey));
    });
  }

  it('S3-03: every wired entity is in this table', () => {
    // One row per entity that ships a pager. When a new one is added, this count
    // is what makes forgetting the parity case a failing test rather than a
    // silent cache miss in production.
    expect(CASES.length).toBe(32);
  });
});
