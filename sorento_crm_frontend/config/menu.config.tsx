import {
  AlertCircle,
  Award,
  Badge,
  Bell,
  Bitcoin,
  Book,
  BookOpen,
  Briefcase,
  Building,
  Building2,
  CalendarCheck,
  Captions,
  CheckCircle,
  Clock,
  Code,
  Coffee,
  File as DocumentIcon,
  Euro,
  Eye,
  File,
  FileQuestion,
  FileText,
  Flag,
  Folder,
  Ghost,
  Gift,
  GitBranch,
  Grid,
  Heart,
  HelpCircle,
  Kanban,
  Key,
  Layout,
  LayoutGrid,
  LifeBuoy,
  Lightbulb,
  Megaphone,
  MessageSquare,
  Monitor,
  Network,
  Package,
  Users as PeopleIcon,
  Plug,
  Settings,
  Share2,
  Shield,
  ShieldUser,
  ShoppingBag,
  ShoppingCart,
  SquareMousePointer,
  Star,
  TrendingUp,
  UserCheck,
  UserCircle,
  Users,
  Warehouse,
  Briefcase as WorkIcon,
  Zap,
} from 'lucide-react';
import { type MenuConfig } from './types';


export const MENU_SIDEBAR: MenuConfig = [
  { heading: 'OVERVIEW' },
  {
    title: 'Dashboards',
    icon: LayoutGrid,
    path: '/',
  },
  {
    title: 'Ideas',
    icon: Lightbulb,
    path: '/ideas',
    permission: 'ideation.board.view',
  },

  { heading: 'SALES' },
  {
    // Sales module (plan 3.7, UAC S1-17, owner rulings 26 Sep (Lavish) L1 and
    // 26 Sep 06:01 (Lavish) N1). No group-level moduleKey: each child carries the module
    // that owns its route, so Sales Agents (module `product`, path unchanged) stays visible
    // when `sales` is switched off.
    title: 'Sales',
    icon: TrendingUp,
    children: [
      {
        title: 'Targets',
        path: '/sales/targets',
        permission: 'sales.targets.view',
        moduleKey: 'sales',
      },
      {
        title: 'Opportunities',
        path: '/sales/opportunities',
        permission: 'sales.opportunities.view',
        moduleKey: 'sales',
      },
      {
        // PLAN-sales-asks-todo-29sep S2: the salesperson's to-do of customer asks.
        title: 'Customer asks',
        path: '/sales/customer-asks',
        permission: 'sales.customer_asks.view',
        moduleKey: 'sales',
      },
      {
        title: 'Sales Teams',
        path: '/sales/teams',
        permission: 'sales.teams.view',
        moduleKey: 'sales',
      },
      {
        title: 'Sales Agents',
        path: '/master-data-management/sales-agents',
        permission: 'master_data.sales_agents.view',
        moduleKey: 'product',
      },
      {
        // PLAN-retail-sales-reports-26sep 5.4. The Sales report item lands with S2.
        title: 'Yearly comparison',
        path: '/sales/yearly-comparison',
        permission: 'sales.reports.view',
        moduleKey: 'sales',
      },
    ],
  },
  {
    title: 'Project Sales',
    icon: Building2,
    moduleKey: 'projects',
    children: [
      {
        title: 'Pipeline',
        path: '/project-sales/pipeline',
        permission: 'projects.projects.view',
      },
      {
        title: 'Leads',
        path: '/project-sales/leads',
        permission: 'projects.projects.view',
      },
      {
        title: 'Awaiting Acceptance',
        path: '/project-sales/lead-acceptance',
        permission: 'projects.projects.view',
      },
      {
        title: 'My Tasks',
        path: '/project-sales/my-tasks',
        permission: 'projects.projects.view',
      },
      {
        title: 'Stock Claims',
        path: '/project-sales/stock-claims',
        permission: 'projects.projects.view',
      },
      {
        title: 'AutoCount Differences',
        path: '/project-sales/divergences',
        permission: 'projects.projects.view',
      },
      {
        title: 'Parties',
        path: '/project-sales/parties',
        permission: 'projects.parties.view',
      },
      {
        title: 'Configuration',
        children: [
          {
            title: 'Setup',
            path: '/project-sales/setup',
            permission: 'projects.types.view',
          },
          {
            title: 'Series',
            path: '/project-sales/series',
            permission: 'projects.types.view',
          },
          {
            title: 'Price Floors',
            path: '/project-sales/price-floors',
            permission: 'projects.types.view',
          },
        ],
      },
    ],
  },
  {
    title: 'Delivery Orders',
    icon: ShoppingCart,
    moduleKey: 'order',
    children: [
      {
        title: 'Delivery Orders',
        path: '/order-management/orders',
        permission: 'order_management.orders.view',
      },
      {
        title: 'Delivery Order Status',
        path: '/order-management/order-statuses',
        permission: 'order_management.order_statuses.view',
      },
      {
        title: 'Customers',
        path: '/order-management/customers',
        permission: 'order_management.customers.view',
      },
      {
        title: 'Customer Groups',
        path: '/order-management/customer-groups',
        permission: 'order_management.customers.view',
      },
      {
        title: 'Customer Branches',
        path: '/order-management/branches',
        permission: 'order_management.branches.view',
      },
    ],
  },
  {
    title: 'Marketing',
    icon: Megaphone,
    moduleKey: 'marketing',
    children: [
      {
        title: 'Promotions',
        children: [
          {
            title: 'All Promotions',
            path: '/marketing-management/promotions',
          },
          {
            title: 'Promotion Attachments',
            path: '/marketing-management/promotion-attachments',
          },
          {
            title: 'Promotion Types',
            path: '/marketing-management/promotion-types',
          },
          {
            title: 'Promotion Products',
            path: '/marketing-management/promotion-products',
          },
        ],
      },
      {
        title: 'Campaigns',
        path: '/marketing-management/campaigns',
      },
    ],
  },

  { heading: 'SUPPLY CHAIN' },
  {
    title: 'Supply Chain',
    icon: TrendingUp,
    moduleKey: 'scm',
    children: [
      {
        title: 'Dashboard',
        path: '/scm',
        permission: 'scm.dashboard.view',
      },
      {
        title: 'Planning',
        children: [
          {
            title: 'Simulation',
            path: '/scm/simulation',
            permission: 'scm.reorder.run',
          },
          {
            title: 'Market Signals',
            path: '/scm/market-signals',
            permission: 'scm.dashboard.view',
          },
          {
            title: 'Policies',
            path: '/scm/policies',
            permission: 'scm.policy.manage',
          },
        ],
      },
      {
        title: 'Project Demand',
        children: [
          {
            title: 'Fulfilment Planning',
            path: '/project-sales/fulfilment-planning',
            permission: 'projects.projects.view',
          },
          {
            title: 'Plans',
            path: '/project-sales/plans',
            permission: 'projects.projects.view',
          },
          {
            title: 'Planning changes',
            path: '/project-sales/planning-changes',
            permission: 'projects.projects.view',
          },
          {
            title: 'Forecast & Reports',
            path: '/project-sales/reports',
            permission: 'projects.projects.view',
          },
        ],
      },
    ],
  },
  {
    title: 'Procurement',
    icon: ShoppingBag,
    moduleKey: 'procurement',
    children: [
      {
        title: 'Suppliers',
        path: '/procurement-management/suppliers',
        permission: 'procurement.suppliers.view',
      },
      {
        title: 'Product-Suppliers',
        path: '/procurement-management/product-suppliers',
        permission: 'procurement.product_suppliers.view',
      },
      {
        title: 'Cost Price Uploads',
        path: '/procurement-management/cost-price-uploads',
        permission: 'procurement.cost_price_changes.view',
      },
      {
        title: 'Packing Lists',
        path: '/procurement-management/packing-lists',
        permission: 'procurement.packing_lists.view',
      },
      {
        title: 'SPO Allocations',
        path: '/procurement-management/spo-allocations',
        permission: 'procurement.spo_allocations.view',
      },
      {
        title: 'GRN',
        path: '/procurement-management/grn',
        permission: 'procurement.grn.view',
      },
      {
        title: 'Picking Lines',
        path: '/procurement-management/picking-lines',
        permission: 'procurement.picking_lines.view',
      },
      {
        title: 'Stock Inquiries',
        path: '/procurement-management/stock-inquiries',
        permission: 'procurement.stock_inquiries.view',
      },
      {
        title: 'Supply Chain',
        children: [
          {
            title: 'Reorder Planning',
            path: '/scm/reorder',
            permission: 'scm.reorder.run',
            moduleKey: 'scm',
          },
          {
            // Moved from Supply Chain > Project Demand (R6, 24 Sep 2026): beside the
            // rest of purchasing's own worklists, not under the board that decides.
            title: 'Stock Debt',
            path: '/project-sales/stock-debt',
            permission: 'projects.stock_debt.view',
          },
          {
            title: 'Loading Plan',
            path: '/scm/loading-plan',
            permission: 'scm.reorder.run',
            moduleKey: 'scm',
          },
          {
            title: 'Order Inquiries',
            path: '/project-sales/order-inquiries',
            permission: 'projects.projects.view',
          },
          {
            title: 'Purchase Orders',
            path: '/scm/purchase-orders',
            permission: 'scm.dashboard.view',
            moduleKey: 'scm',
          },
          {
            title: 'Proforma Invoices',
            path: '/scm/proforma-invoices',
            permission: 'scm.dashboard.view',
            moduleKey: 'scm',
          },
        ],
      },
    ],
  },
  {
    title: 'Inventory',
    icon: Warehouse,
    moduleKey: 'inventory',
    children: [
      {
        title: 'Warehouses',
        path: '/inventory-management/warehouses',
        permission: 'inventory.warehouses.view',
      },
      {
        title: 'Storage Zones',
        path: '/inventory-management/storage-zones',
        permission: 'inventory.storage_zones.view',
      },
      {
        title: 'Stock',
        path: '/inventory-management/stock',
        permission: 'inventory.stock.view',
      },
      {
        title: 'Stock Batches',
        path: '/inventory-management/stock-batches',
        permission: 'inventory.stock_batches.view',
      },
      {
        title: 'Stock Ledger',
        path: '/inventory-management/stock-ledger',
        permission: 'inventory.stock_ledger.view',
      },
      {
        title: 'Stock Transfers',
        path: '/inventory-management/stock-transfers',
        permission: 'inventory.stock_transfers.view',
      },
    ],
  },

  { heading: 'CATALOGUE' },
  {
    title: 'Products',
    icon: Package,
    moduleKey: 'product',
    children: [
      {
        title: 'Products',
        children: [
          {
            title: 'All Products',
            path: '/master-data-management/products',
            permission: 'master_data.products.view',
          },
          {
            title: 'Product Attachments',
            path: '/master-data-management/product-attachments',
            permission: 'master_data.product_attachments.view',
          },
          {
            title: 'Product Sets',
            path: '/master-data-management/product-sets',
            permission: 'master_data.product_sets.view',
          },
        ],
      },
      {
        title: 'Specifications',
        children: [
          {
            title: 'Product Specifications',
            path: '/master-data-management/product-specifications',
            permission: 'master_data.products.view',
          },
          {
            title: 'Spec Verification',
            path: '/master-data-management/spec-verification',
            permission: 'master_data.products.view',
          },
          {
            title: 'Flyer Spec Proposals',
            path: '/master-data-management/flyer-spec-proposals',
            permission: 'master_data.products.edit',
          },
        ],
      },
      {
        title: 'Reference Data',
        children: [
          {
            title: 'Product Categories',
            path: '/master-data-management/product-categories',
            permission: 'master_data.product_categories.view',
          },
          {
            title: 'Brands',
            path: '/master-data-management/brands',
            permission: 'master_data.brands.view',
          },
          {
            title: 'Units of Measure',
            path: '/master-data-management/units-of-measure',
            permission: 'master_data.units_of_measure.view',
          },
          {
            title: 'Countries',
            path: '/master-data-management/countries',
            permission: 'master_data.countries.view',
          },
          {
            title: 'Certificates',
            path: '/master-data-management/certificates',
            permission: 'master_data.certificates.view',
          },
        ],
      },
    ],
  },
  {
    title: 'Dealer Kit',
    icon: BookOpen,
    moduleKey: 'dealer_kit',
    children: [
      {
        title: 'Catalogue Pages',
        path: '/dealer-kit',
        permission: 'dealer_kit.page.view',
      },
      {
        title: 'Editions',
        path: '/dealer-kit/editions',
        permission: 'dealer_kit.page.view',
      },
      {
        title: 'Library',
        children: [
          {
            title: 'Product Collections',
            path: '/dealer-kit/collections',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Tile Designs',
            path: '/dealer-kit/tile-designs',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Brochure Images',
            path: '/dealer-kit/brochure-images',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Flyers',
            path: '/dealer-kit/flyer-readings',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Bundles',
            path: '/dealer-kit/bundles',
            permission: 'dealer_kit.page.view',
          },
        ],
      },
      {
        title: 'Room Designer',
        children: [
          {
            title: 'Room Designer',
            path: '/dealer-kit/design',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Design Summary',
            path: '/dealer-kit/design/summary',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Price Tag Requests',
            path: '/dealer-kit/price-tag-requests',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Tag Templates',
            path: '/dealer-kit/tag-templates',
            permission: 'dealer_kit.page.view',
          },
          {
            title: 'Tag Sizes',
            path: '/dealer-kit/tag-sizes',
            permission: 'dealer_kit.page.view',
          },
        ],
      },
    ],
  },

  { heading: 'OPERATIONS' },
  {
    title: 'Complaints',
    icon: AlertCircle,
    moduleKey: 'complaints',
    children: [
      {
        title: 'Complaints',
        path: '/complaint-management/complaints',
      },
      {
        title: 'Root Causes',
        path: '/complaint-management/complaint-root-causes',
        permission: 'master_data.complaint_root_causes.view',
      },
      {
        title: 'Resolutions',
        path: '/complaint-management/complaint-resolutions',
        permission: 'master_data.complaint_resolutions.view',
      },
    ],
  },
  {
    title: 'SLA',
    icon: Clock,
    moduleKey: 'sla',
    children: [
      {
        title: 'SLA Policies',
        path: '/sla-management/sla-policies',
      },
      {
        title: 'Conversations',
        path: '/sla-management/conversations',
        permission: 'sla_management.conversations.view',
      },
      {
        title: 'Conversation SLA Tracking',
        path: '/sla-management/conversation-sla-tracking',
      },
      {
        title: 'Form SLA Tracking',
        path: '/sla-management/form-sla-tracking',
      },
      {
        title: 'My Team Tasks',
        path: '/sla-management/team-pending',
      },
      {
        title: 'Form SLA Configuration',
        path: '/sla-management/form-sla-config',
      },
      {
        title: 'SLA Event Logs',
        path: '/sla-management/escalation-logs',
      },
      {
        title: 'Message Snippets',
        path: '/sla-management/message-snippets',
        permission: 'sla_management.message_snippets.view',
      },
      {
        title: 'KPI Dashboard',
        path: '/sla-management/kpi-dashboard',
        permission: 'sla.kpi.view',
      },
    ],
  },
  {
    title: 'Project Sales Admin',
    icon: Briefcase,
    moduleKey: 'procurement',
    children: [
      {
        title: 'Sales Orders',
        path: '/scm/sales-orders',
        permission: 'scm.dashboard.view',
        moduleKey: 'scm',
      },
      {
        title: 'Purchase Requests',
        path: '/procurement-management/purchase-requests',
        permission: 'procurement.purchase_requests.view',
      },
      {
        title: 'Sponsorship Forms',
        path: '/procurement-management/sponsorship-forms',
        permission: 'procurement.sponsorship_forms.view',
      },
      {
        title: 'Sponsorship Report',
        path: '/procurement-management/sponsorship-forms/report',
        permission: 'procurement.sponsorship_forms.report',
      },
    ],
  },
  {
    title: 'Forms',
    icon: FileText,
    moduleKey: 'forms',
    children: [
      {
        title: 'Forms',
        path: '/forms-management/forms',
      },
    ],
  },
  {
    title: 'Workflow Forms',
    icon: GitBranch,
    moduleKey: 'workflow_forms',
    children: [
      {
        title: 'Definitions',
        path: '/workflow-forms-management/definitions',
        permission: 'workflow_forms.definitions.view',
      },
    ],
  },
  {
    title: 'Resources',
    icon: Folder,
    moduleKey: 'resources',
    children: [
      {
        title: 'Files',
        path: '/resource-management/attachment-directories',
      },
      {
        title: 'Trash',
        path: '/resource-management/trash',
      },
      {
        title: 'Attachment Types',
        path: '/resource-management/attachment-types',
      },
    ],
  },

  { heading: 'ADMINISTRATION' },
  {
    title: 'Users & Access',
    icon: ShieldUser,
    moduleKey: 'base',
    children: [
      {
        title: 'People',
        children: [
          {
            title: 'Administrative Users',
            path: '/user-management/users',
            permission: 'user_management.users.view',
          },
          {
            title: 'Internal Users',
            path: '/user-management/contact-access-agents',
            permission: 'user_management.contacts.view',
          },
          {
            title: 'Teams',
            path: '/user-management/teams',
            permission: 'user_management.teams.view',
          },
          {
            title: 'Onboarding Requests',
            path: '/user-management/onboarding-requests',
            permission: 'user_management.onboarding.view',
          },
        ],
      },
      {
        title: 'Access',
        children: [
          {
            title: 'Roles',
            path: '/user-management/roles',
            permission: 'user_management.roles.view',
          },
          {
            title: 'Permissions',
            path: '/user-management/permissions',
            permission: 'user_management.permissions.view',
          },
          {
            title: 'AI Agents',
            path: '/user-management/access-agents',
            permission: 'user_management.access_agents.view',
          },
          {
            title: 'Contact Access Types',
            path: '/user-management/contact-access-types',
            permission: 'user_management.access_agents.view',
          },
        ],
      },
      {
        title: 'Market Segments',
        path: '/user-management/market-segments',
        permission: 'user_management.reference_data.view',
      },
      {
        title: 'Account',
        path: '/user-management/account',
        permission: 'user_management.account.view',
      },
      {
        title: 'Logs',
        path: '/user-management/logs',
        permission: 'user_management.logs.view',
      },
      {
        title: 'Settings',
        path: '/user-management/settings',
        permission: 'user_management.settings.view',
      },
    ],
  },
  {
    title: 'System',
    icon: Settings,
    moduleKey: 'base',
    children: [
      {
        title: 'Platform',
        children: [
          {
            title: 'Companies',
            path: '/system-management/companies',
            icon: Building2,
            superadminOnly: true,
          },
          {
            title: 'App Store',
            path: '/system-management/app-store',
            permission: 'system.modules.manage',
          },
          {
            title: 'Module bundles',
            path: '/system-management/app-store/bundles',
            permission: 'system.modules.manage',
          },
        ],
      },
      {
        title: 'Operations',
        children: [
          {
            title: 'Import Jobs',
            path: '/system-management/import-jobs',
            superadminOnly: true,
          },
          {
            title: 'Import Logs',
            path: '/system-management/import-logs',
            superadminOnly: true,
          },
          {
            title: 'Tracking Validation',
            path: '/system-management/tracking-validation',
            superadminOnly: true,
          },
          {
            title: 'Audit Logs',
            path: '/system-management/audit-logs',
            superadminOnly: true,
          },
          {
            title: 'System Health',
            path: '/system-management/health',
            superadminOnly: true,
          },
          {
            title: 'Activity Timeline',
            path: '/system-management/activity',
            superadminOnly: true,
          },
          {
            title: 'Scheduled Tasks',
            path: '/system-management/scheduled-tasks',
            superadminOnly: true,
          },
          {
            title: 'API Call Log',
            path: '/system-management/api-call-logs',
            permission: 'system_management.api_call_log.view',
          },
        ],
      },
      {
        title: 'Messaging',
        children: [
          {
            title: 'Integrations',
            path: '/integration-management/integrations',
          },
          {
            title: 'Integration Logs',
            path: '/integration-management/integration-logs',
            superadminOnly: true,
          },
          {
            title: 'WhatsApp Templates',
            path: '/integration-management/whatsapp-templates',
            permission: 'integration.respond_templates.view',
          },
          {
            title: 'Outgoing Mails',
            path: '/system-management/outgoing-mails',
            permission: 'system.outgoing_mails.view',
            children: [
              {
                title: 'Email Outbox',
                path: '/system-management/email-outbox',
                permission: 'system.email_outbox.view',
              },
              {
                title: 'Respond Outbox',
                path: '/system-management/respond-outbox',
                permission: 'system.respond_outbox.view',
              },
            ],
          },
          {
            title: 'Chat History',
            path: '/system-management/chat-history',
            permission: 'system.chat_history.view',
          },
          {
            title: 'Chatbot Console',
            path: '/system-management/chatbot-console',
            permission: 'system.chat_history.view',
          },
          {
            title: 'Chatbot Domains',
            path: '/system-management/chatbot-domains',
            permission: 'system.chat_history.view',
          },
          {
            title: 'Entity Kinds',
            path: '/system-management/chatbot-entity-kinds',
            permission: 'system.chat_history.view',
          },
          {
            title: 'Email Event Configs',
            path: '/system-management/email-event-configs',
            permission: 'system.email_event_configs.view',
          },
          {
            title: 'Email Templates',
            path: '/system-management/email-templates',
            permission: 'email_templates.templates.view',
          },
          {
            title: 'Email Theme',
            path: '/system-management/email-theme',
            permission: 'email_templates.templates.view',
          },
          {
            title: 'Respond.io Workspaces',
            path: '/system-management/respond-workspaces',
            permission: 'system.respond_workspaces.view',
          },
          {
            title: 'Respond.io Contacts',
            path: '/system-management/respond-contacts',
            permission: 'system.respond_workspaces.view',
          },
        ],
      },
      {
        title: 'Configuration',
        children: [
          {
            title: 'Automation',
            path: '/system-management/automation',
            permission: 'automation.automations.view',
          },
          {
            title: 'Work Calendar',
            path: '/system-management/work-calendar',
            superadminOnly: true,
          },
          {
            title: 'Running Numbers',
            path: '/system-management/numbering-rules',
            permission: 'system.numbering_rules.view',
          },
          {
            title: 'Import Column Mappings',
            path: '/system-management/import-field-aliases',
            permission: 'system.import_field_aliases.view',
          },
          {
            title: 'Status Graphs',
            path: '/system-management/status-graphs',
            permission: 'system.statuses.view',
          },
          {
            title: 'Translations',
            path: '/system-management/translations',
            permission: 'system.translations.view',
          },
          {
            title: 'Lookup Sets',
            path: '/master-data-management/lookup-sets',
            permission: 'master_data.lookup_sets.view',
          },
        ],
      },
      {
        title: 'AI Assistant',
        children: [
          {
            title: 'Settings',
            path: '/system-management/ai-assistant',
            permission: 'system.ai_assistant_settings.view',
          },
          {
            title: 'Prompts',
            path: '/system-management/ai-assistant/prompts',
            permission: 'system.ai_assistant_settings.view',
          },
          {
            title: 'Usage',
            path: '/system-management/ai-assistant/usage',
            permission: 'system.ai_assistant_settings.view',
          },
          {
            title: 'Wishlist',
            path: '/system-management/ai-assistant/wishlist',
            permission: 'system.ai_assistant_settings.view',
          },
          {
            title: 'MCP Tools',
            path: '/system-management/mcp-tools',
            permission: 'system.ai_assistant_settings.view',
          },
        ],
      },
    ],
  },
  // Hidden: Public Profile
  // {
  //   title: 'Public Profile',
  //   icon: UserCircle,
  //   children: [
  //     {
  //       title: 'Profiles',
  //       children: [
  //         { title: 'Default', path: '/public-profile/profiles/default' },
  //         { title: 'Creator', path: '/public-profile/profiles/creator' },
  //         { title: 'Company', path: '/public-profile/profiles/company' },
  //         { title: 'NFT', path: '/public-profile/profiles/nft' },
  //         { title: 'Blogger', path: '/public-profile/profiles/blogger' },
  //         { title: 'CRM', path: '/public-profile/profiles/crm' },
  //         {
  //           title: 'More',
  //           collapse: true,
  //           collapseTitle: 'Show less',
  //           expandTitle: 'Show 4 more',
  //           children: [
  //             { title: 'Gamer', path: '/public-profile/profiles/gamer' },
  //             { title: 'Feeds', path: '/public-profile/profiles/feeds' },
  //             { title: 'Plain', path: '/public-profile/profiles/plain' },
  //             { title: 'Modal', path: '/public-profile/profiles/modal' },
  //           ],
  //         },
  //       ],
  //     },
  //     {
  //       title: 'Projects',
  //       children: [
  //         { title: '3 Columns', path: '/public-profile/projects/3-columns' },
  //         { title: '2 Columns', path: '/public-profile/projects/2-columns' },
  //       ],
  //     },
  //     { title: 'Works', path: '/public-profile/works' },
  //     { title: 'Teams', path: '/public-profile/teams' },
  //     { title: 'Network', path: '/public-profile/network' },
  //     { title: 'Activity', path: '/public-profile/activity' },
  //     {
  //       title: 'More',
  //       collapse: true,
  //       collapseTitle: 'Show less',
  //       expandTitle: 'Show 3 more',
  //       children: [
  //         { title: 'Campaigns - Card', path: '/public-profile/campaigns/card' },
  //         { title: 'Campaigns - List', path: '/public-profile/campaigns/list' },
  //         { title: 'Empty', path: '/public-profile/empty' },
  //       ],
  //     },
  //   ],
  // },
  // Hidden: My Account
  // {
  //   title: 'My Account',
  //   icon: Settings,
  //   children: [
  //     {
  //       title: 'Account',
  //       children: [
  //         { title: 'Get Started', path: '/account/home/get-started' },
  //         { title: 'User Profile', path: '/user-management/account' },
  //         { title: 'Company Profile', path: '/account/home/company-profile' },
  //         {
  //           title: 'Settings - With Sidebar',
  //           path: '/account/home/settings-sidebar',
  //         },
  //         {
  //           title: 'Settings - Enterprise',
  //           path: '/account/home/settings-enterprise',
  //         },
  //         { title: 'Settings - Plain', path: '/account/home/settings-plain' },
  //         { title: 'Settings - Modal', path: '/account/home/settings-modal' },
  //       ],
  //     },
  //     {
  //       title: 'Billing',
  //       children: [
  //         { title: 'Billing - Basic', path: '/account/billing/basic' },
  //         {
  //           title: 'Billing - Enterprise',
  //           path: '/account/billing/enterprise',
  //         },
  //         { title: 'Plans', path: '/account/billing/plans' },
  //         { title: 'Billing History', path: '/account/billing/history' },
  //       ],
  //     },
  //     {
  //       title: 'Security',
  //       children: [
  //         { title: 'Get Started', path: '/account/security/get-started' },
  //         { title: 'Security Overview', path: '/account/security/overview' },
  //         {
  //           title: 'Allowed IP Addresses',
  //           path: '/account/security/allowed-ip-addresses',
  //         },
  //         {
  //           title: 'Privacy Settings',
  //           path: '/account/security/privacy-settings',
  //         },
  //         {
  //           title: 'Device Management',
  //           path: '/account/security/device-management',
  //         },
  //         {
  //           title: 'Backup & Recovery',
  //           path: '/account/security/backup-and-recovery',
  //         },
  //         {
  //           title: 'Current Sessions',
  //           path: '/account/security/current-sessions',
  //         },
  //         { title: 'Security Log', path: '/account/security/security-log' },
  //       ],
  //     },
  //     {
  //       title: 'Members & Roles',
  //       children: [
  //         { title: 'Teams Starter', path: '/account/members/team-starter' },
  //         { title: 'Teams', path: '/account/members/teams' },
  //         { title: 'Team Info', path: '/account/members/team-info' },
  //         {
  //           title: 'Members Starter',
  //           path: '/account/members/members-starter',
  //         },
  //         { title: 'Team Members', path: '/account/members/team-members' },
  //         { title: 'Import Members', path: '/account/members/import-members' },
  //         { title: 'Roles', path: '/account/members/roles' },
  //         {
  //           title: 'Permissions - Toggler',
  //           path: '/account/members/permissions-toggle',
  //         },
  //         {
  //           title: 'Permissions - Check',
  //           path: '/account/members/permissions-check',
  //         },
  //       ],
  //     },
  //     { title: 'Integrations', path: '/account/integrations' },
  //     { title: 'Notifications', path: '/account/notifications' },
  //     { title: 'API Keys', path: '/account/api-keys' },
  //     {
  //       title: 'More',
  //       collapse: true,
  //       collapseTitle: 'Show less',
  //       expandTitle: 'Show 3 more',
  //       children: [
  //         { title: 'Appearance', path: '/account/appearance' },
  //         { title: 'Invite a Friend', path: '/account/invite-a-friend' },
  //         { title: 'Activity', path: '/account/activity' },
  //       ],
  //     },
  //   ],
  // },
  // Hidden: Network
  // {
  //   title: 'Network',
  //   icon: Users,
  //   children: [
  //     { title: 'Get Started', path: '/network/get-started' },
  //     {
  //       title: 'User Cards',
  //       children: [
  //         { title: 'Mini Cards', path: '/network/user-cards/mini-cards' },
  //         { title: 'Team Crew', path: '/network/user-cards/team-crew' },
  //         { title: 'Author', path: '/network/user-cards/author' },
  //         { title: 'NFT', path: '/network/user-cards/nft' },
  //         { title: 'Social', path: '/network/user-cards/social' },
  //       ],
  //     },
  //     {
  //       title: 'User Table',
  //       children: [
  //         { title: 'Team Crew', path: '/network/user-table/team-crew' },
  //         { title: 'App Roster', path: '/network/user-table/app-roster' },
  //         {
  //           title: 'Market Authors',
  //           path: '/network/user-table/market-authors',
  //         },
  //         { title: 'SaaS Users', path: '/network/user-table/saas-users' },
  //         { title: 'Store Clients', path: '/network/user-table/store-clients' },
  //         { title: 'Visitors', path: '/network/user-table/visitors' },
  //       ],
  //     },
  //     { title: 'Cooperations', path: '/network/cooperations', disabled: true },
  //     { title: 'Leads', path: '/network/leads', disabled: true },
  //     { title: 'Donators', path: '/network/donators', disabled: true },
  //   ],
  // },
  // Hidden: Authentication
  // {
  //   title: 'Authentication',
  //   icon: Shield,
  //   children: [
  //     {
  //       title: 'Sign In',
  //       path: '/signin',
  //     },
  //     {
  //       title: 'Check Email',
  //       path: '/signup',
  //     },
  //     {
  //       title: 'Reset Password',
  //       path: '/reset-password',
  //     },
  //     {
  //       title: '2FA',
  //       path: '/2fa',
  //     },
  //     { title: 'Welcome Message', path: '/auth/welcome-message' },
  //     { title: 'Account Deactivated', path: '/auth/account-deactivated' },
  //     { title: 'Error 404', path: '/error/404' },
  //     { title: 'Error 500', path: '/error/500' },
  //   ],
  // },
  // Hidden: Store - Client
  // {
  //   title: 'Store - Client',
  //   icon: Users,
  //   children: [
  //     { title: 'Home', path: '/store-client/home' },
  //     {
  //       title: 'Search Results - Grid',
  //       path: '/store-client/search-results-grid',
  //     },
  //     {
  //       title: 'Search Results - List',
  //       path: '/store-client/search-results-list',
  //     },
  //     { title: 'Product Details', path: '/store-client/product-details' },
  //     { title: 'Wishlist', path: '/store-client/wishlist' },
  //     {
  //       title: 'Checkout',
  //       children: [
  //         {
  //           title: 'Order Summary',
  //           path: '/store-client/checkout/order-summary',
  //         },
  //         {
  //           title: 'Shipping Info',
  //           path: '/store-client/checkout/shipping-info',
  //         },
  //         {
  //           title: 'Payment Method',
  //           path: '/store-client/checkout/payment-method',
  //         },
  //         {
  //           title: 'Order Placed',
  //           path: '/store-client/checkout/order-placed',
  //         },
  //       ],
  //     },
  //     { title: 'My Orders', path: '/store-client/my-orders' },
  //     { title: 'Order Receipt', path: '/store-client/order-receipt' },
  //   ],
  // },
  // Hidden: Store - Services, AI Promt, Invoice Generator
  // { title: 'Store - Services', icon: Codepen, disabled: true },
  // { title: 'AI Promt', icon: Theater, disabled: true },
  // { title: 'Invoice Generator', icon: ScrollText, disabled: true },
];

export const MENU_SIDEBAR_CUSTOM: MenuConfig = [
  {
    title: 'Store - Client',
    icon: Users,
    children: [
      { title: 'Home', path: '/store-client/home' },
      {
        title: 'Search Results',
        children: [
          {
            title: 'Search Results - Grid',
            path: '/store-client/search-results-grid',
          },
          {
            title: 'Search Results - List',
            path: '/store-client/search-results-list',
          },
        ],
      },
      {
        title: 'Overlays',
        children: [
          { title: 'Product Details', path: '/store-client/product-details' },
          { title: 'Wishlist', path: '/store-client/wishlist' },
        ],
      },
      {
        title: 'Checkout',
        children: [
          {
            title: 'Order Summary',
            path: '/store-client/checkout/order-summary',
          },
          {
            title: 'Shipping Info',
            path: '/store-client/checkout/shipping-info',
          },
          {
            title: 'Payment Method',
            path: '/store-client/checkout/payment-method',
          },
          {
            title: 'Order Placed',
            path: '/store-client/checkout/order-placed',
          },
        ],
      },
      { title: 'My Orders', path: '/store-client/my-orders' },
      { title: 'Order Receipt', path: '/store-client/order-receipt' },
    ],
  },
];

export const MENU_SIDEBAR_COMPACT: MenuConfig = [
  {
    title: 'Dashboards',
    icon: LayoutGrid,
    path: '/',
  },
  {
    title: 'Public Profile',
    icon: UserCircle,
    children: [
      {
        title: 'Profiles',
        children: [
          { title: 'Default', path: '/public-profile/profiles/default' },
          { title: 'Creator', path: '/public-profile/profiles/creator' },
          { title: 'Company', path: '/public-profile/profiles/company' },
          { title: 'NFT', path: '/public-profile/profiles/nft' },
          { title: 'Blogger', path: '/public-profile/profiles/blogger' },
          { title: 'CRM', path: '/public-profile/profiles/crm' },
          {
            title: 'More',
            collapse: true,
            collapseTitle: 'Show less',
            expandTitle: 'Show 4 more',
            children: [
              { title: 'Gamer', path: '/public-profile/profiles/gamer' },
              { title: 'Feeds', path: '/public-profile/profiles/feeds' },
              { title: 'Plain', path: '/public-profile/profiles/plain' },
              { title: 'Modal', path: '/public-profile/profiles/modal' },
            ],
          },
        ],
      },
      {
        title: 'Projects',
        children: [
          { title: '3 Columns', path: '/public-profile/projects/3-columns' },
          { title: '2 Columns', path: '/public-profile/projects/2-columns' },
        ],
      },
      { title: 'Works', path: '/public-profile/works' },
      { title: 'Teams', path: '/public-profile/teams' },
      { title: 'Network', path: '/public-profile/network' },
      { title: 'Activity', path: '/public-profile/activity' },
      {
        title: 'More',
        collapse: true,
        collapseTitle: 'Show less',
        expandTitle: 'Show 3 more',
        children: [
          { title: 'Campaigns - Card', path: '/public-profile/campaigns/card' },
          { title: 'Campaigns - List', path: '/public-profile/campaigns/list' },
          { title: 'Empty', path: '/public-profile/empty' },
        ],
      },
    ],
  },
  {
    title: 'My Account',
    icon: Settings,
    children: [
      {
        title: 'Account',
        children: [
          { title: 'Get Started', path: '/account/home/get-started' },
          { title: 'User Profile', path: '/user-management/account' },
          { title: 'Company Profile', path: '/account/home/company-profile' },
          {
            title: 'Settings - With Sidebar',
            path: '/account/home/settings-sidebar',
          },
          {
            title: 'Settings - Enterprise',
            path: '/account/home/settings-enterprise',
          },
          { title: 'Settings - Plain', path: '/account/home/settings-plain' },
          { title: 'Settings - Modal', path: '/account/home/settings-modal' },
        ],
      },
      {
        title: 'Billing',
        children: [
          { title: 'Billing - Basic', path: '/account/billing/basic' },
          {
            title: 'Billing - Enterprise',
            path: '/account/billing/enterprise',
          },
          { title: 'Plans', path: '/account/billing/plans' },
          { title: 'Billing History', path: '/account/billing/history' },
        ],
      },
      {
        title: 'Security',
        children: [
          { title: 'Get Started', path: '/account/security/get-started' },
          { title: 'Security Overview', path: '/account/security/overview' },
          {
            title: 'Allowed IP Addresses',
            path: '/account/security/allowed-ip-addresses',
          },
          {
            title: 'Privacy Settings',
            path: '/account/security/privacy-settings',
          },
          {
            title: 'Device Management',
            path: '/account/security/device-management',
          },
          {
            title: 'Backup & Recovery',
            path: '/account/security/backup-and-recovery',
          },
          {
            title: 'Current Sessions',
            path: '/account/security/current-sessions',
          },
          { title: 'Security Log', path: '/account/security/security-log' },
        ],
      },
      {
        title: 'Members & Roles',
        children: [
          { title: 'Teams Starter', path: '/account/members/team-starter' },
          { title: 'Teams', path: '/account/members/teams' },
          { title: 'Team Info', path: '/account/members/team-info' },
          {
            title: 'Members Starter',
            path: '/account/members/members-starter',
          },
          { title: 'Team Members', path: '/account/members/team-members' },
          { title: 'Import Members', path: '/account/members/import-members' },
          { title: 'Roles', path: '/account/members/roles' },
          {
            title: 'Permissions - Toggler',
            path: '/account/members/permissions-toggle',
          },
          {
            title: 'Permissions - Check',
            path: '/account/members/permissions-check',
          },
        ],
      },
      { title: 'Integrations', path: '/account/integrations' },
      { title: 'Notifications', path: '/account/notifications' },
      { title: 'API Keys', path: '/account/api-keys' },
      {
        title: 'More',
        collapse: true,
        collapseTitle: 'Show less',
        expandTitle: 'Show 3 more',
        children: [
          { title: 'Appearance', path: '/account/appearance' },
          { title: 'Invite a Friend', path: '/account/invite-a-friend' },
          { title: 'Activity', path: '/account/activity' },
        ],
      },
    ],
  },
  {
    title: 'Network',
    icon: Users,
    children: [
      { title: 'Get Started', path: '/network/get-started' },
      {
        title: 'User Cards',
        children: [
          { title: 'Mini Cards', path: '/network/user-cards/mini-cards' },
          { title: 'Team Crew', path: '/network/user-cards/team-crew' },
          { title: 'Author', path: '/network/user-cards/author' },
          { title: 'NFT', path: '/network/user-cards/nft' },
          { title: 'Social', path: '/network/user-cards/social' },
        ],
      },
      {
        title: 'User Table',
        children: [
          { title: 'Team Crew', path: '/network/user-table/team-crew' },
          { title: 'App Roster', path: '/network/user-table/app-roster' },
          {
            title: 'Market Authors',
            path: '/network/user-table/market-authors',
          },
          { title: 'SaaS Users', path: '/network/user-table/saas-users' },
          { title: 'Store Clients', path: '/network/user-table/store-clients' },
          { title: 'Visitors', path: '/network/user-table/visitors' },
        ],
      },
      { title: 'Cooperations', path: '/network/cooperations', disabled: true },
      { title: 'Leads', path: '/network/leads', disabled: true },
      { title: 'Donators', path: '/network/donators', disabled: true },
    ],
  },
  {
    title: 'Store - Client',
    icon: ShoppingCart,
    children: [
      { title: 'Home', path: '/store-client/home' },
      {
        title: 'Search Results - Grid',
        path: '/store-client/search-results-grid',
      },
      {
        title: 'Search Results - List',
        path: '/store-client/search-results-list',
      },
      { title: 'Product Details', path: '/store-client/product-details' },
      { title: 'Wishlist', path: '/store-client/wishlist' },
      {
        title: 'Checkout',
        children: [
          {
            title: 'Order Summary',
            path: '/store-client/checkout/order-summary',
          },
          {
            title: 'Shipping Info',
            path: '/store-client/checkout/shipping-info',
          },
          {
            title: 'Payment Method',
            path: '/store-client/checkout/payment-method',
          },
          {
            title: 'Order Placed',
            path: '/store-client/checkout/order-placed',
          },
        ],
      },
      { title: 'My Orders', path: '/store-client/my-orders' },
      { title: 'Order Receipt', path: '/store-client/order-receipt' },
    ],
  },
  {
    title: 'User Management',
    icon: ShieldUser,
    moduleKey: 'base',
    children: [
      {
        title: 'Administrative Users',
        path: '/user-management/users',
      },
      {
        title: 'Onboarding Requests',
        path: '/user-management/onboarding-requests',
      },
      {
        title: 'Roles',
        path: '/user-management/roles',
      },
      {
        title: 'Permissions',
        path: '/user-management/permissions',
      },
      {
        title: 'AI Agents',
        path: '/user-management/access-agents',
      },
      {
        title: 'Teams',
        path: '/user-management/teams',
      },
      {
        title: 'Internal Users',
        path: '/user-management/contact-access-agents',
      },
      {
        title: 'Contact Access Types',
        path: '/user-management/contact-access-types',
      },
      {
        title: 'Market Segments',
        path: '/user-management/market-segments',
      },
      {
        title: 'Account',
        path: '/user-management/account',
      },
      {
        title: 'Logs',
        path: '/user-management/logs',
      },
      {
        title: 'Settings',
        path: '/user-management/settings',
      },
    ],
  },
  {
    title: 'Delivery Order Management',
    icon: ShoppingCart,
    moduleKey: 'order',
    children: [
      {
        title: 'Delivery Orders',
        path: '/order-management/orders',
      },
      {
        title: 'Delivery Order Status',
        path: '/order-management/order-statuses',
      },
      {
        title: 'Customers',
        path: '/order-management/customers',
      },
      {
        title: 'Customer Groups',
        path: '/order-management/customer-groups',
      },
      {
        title: 'Customer Branches',
        path: '/order-management/branches',
      },
    ],
  },
  {
    title: 'Complaint Management',
    icon: AlertCircle,
    moduleKey: 'complaints',
    children: [
      {
        title: 'Complaints',
        path: '/complaint-management/complaints',
      },
      {
        title: 'Root Causes',
        path: '/complaint-management/complaint-root-causes',
        permission: 'master_data.complaint_root_causes.view',
      },
      {
        title: 'Resolutions',
        path: '/complaint-management/complaint-resolutions',
        permission: 'master_data.complaint_resolutions.view',
      },
    ],
  },
  {
    title: 'SLA Management',
    icon: Clock,
    moduleKey: 'sla',
    children: [
      {
        title: 'SLA Policies',
        path: '/sla-management/sla-policies',
      },
      {
        title: 'Conversations',
        path: '/sla-management/conversations',
        permission: 'sla_management.conversations.view',
      },
      {
        title: 'Conversation SLA Tracking',
        path: '/sla-management/conversation-sla-tracking',
      },
      {
        title: 'Form SLA Tracking',
        path: '/sla-management/form-sla-tracking',
      },
      {
        title: 'My Team Tasks',
        path: '/sla-management/team-pending',
      },
      {
        title: 'Form SLA Configuration',
        path: '/sla-management/form-sla-config',
      },
      {
        title: 'SLA Event Logs',
        path: '/sla-management/escalation-logs',
      },
      {
        title: 'Message Snippets',
        path: '/sla-management/message-snippets',
        permission: 'sla_management.message_snippets.view',
      },
      {
        title: 'KPI Dashboard',
        path: '/sla-management/kpi-dashboard',
        permission: 'sla.kpi.view',
      },
    ],
  },
  {
    title: 'Product Management',
    icon: Package,
    moduleKey: 'product',
    children: [
    {
      title: 'Products',
      children: [
        {
          title: 'All Products',
          path: '/master-data-management/products',
          permission: 'master_data.products.view',
        },
        {
          title: 'Product Attachments',
          path: '/master-data-management/product-attachments',
          permission: 'master_data.product_attachments.view',
        },
        {
          title: 'Product Sets',
          path: '/master-data-management/product-sets',
          permission: 'master_data.product_sets.view',
        },
      ],
    },
    {
      title: 'Certificates',
      path: '/master-data-management/certificates',
      permission: 'master_data.certificates.view',
    },
    {
      title: 'Product Categories',
      path: '/master-data-management/product-categories',
      permission: 'master_data.product_categories.view',
    },
    {
      title: 'Product Specifications',
      path: '/master-data-management/product-specifications',
      permission: 'master_data.products.view',
    },
    {
      title: 'Spec Verification',
      path: '/master-data-management/spec-verification',
      permission: 'master_data.products.view',
    },
    {
      title: 'Flyer Spec Proposals',
      path: '/master-data-management/flyer-spec-proposals',
      permission: 'master_data.products.edit',
    },
    {
      title: 'Brands',
      path: '/master-data-management/brands',
      permission: 'master_data.brands.view',
    },
    {
      title: 'Units of Measure',
      path: '/master-data-management/units-of-measure',
      permission: 'master_data.units_of_measure.view',
    },
    {
      title: 'Countries',
      path: '/master-data-management/countries',
      permission: 'master_data.countries.view',
    },
    ],
  },
  {
    title: 'Procurement',
    icon: ShoppingBag,
    moduleKey: 'procurement',
    children: [
      {
        title: 'Suppliers',
        path: '/procurement-management/suppliers',
        permission: 'procurement.suppliers.view',
      },
      {
        title: 'Product-Suppliers',
        path: '/procurement-management/product-suppliers',
        permission: 'procurement.product_suppliers.view',
      },
      {
        title: 'Cost Price Uploads',
        path: '/procurement-management/cost-price-uploads',
        permission: 'procurement.cost_price_changes.view',
      },
      {
        title: 'Packing Lists',
        path: '/procurement-management/packing-lists',
        permission: 'procurement.packing_lists.view',
      },
      {
        title: 'SPO Allocations',
        path: '/procurement-management/spo-allocations',
        permission: 'procurement.spo_allocations.view',
      },
      {
        title: 'GRN',
        path: '/procurement-management/grn',
        permission: 'procurement.grn.view',
      },
      {
        title: 'Picking Lines',
        path: '/procurement-management/picking-lines',
        permission: 'procurement.picking_lines.view',
      },
      {
        title: 'Stock Inquiries',
        path: '/procurement-management/stock-inquiries',
        permission: 'procurement.stock_inquiries.view',
      },
    ],
  },
  {
    title: 'Project Sales Admin',
    icon: Briefcase,
    moduleKey: 'procurement',
    children: [
      {
        title: 'Purchase Requests',
        path: '/procurement-management/purchase-requests',
        permission: 'procurement.purchase_requests.view',
      },
      {
        title: 'Sponsorship Forms',
        path: '/procurement-management/sponsorship-forms',
        permission: 'procurement.sponsorship_forms.view',
      },
      {
        title: 'Sponsorship Report',
        path: '/procurement-management/sponsorship-forms/report',
        permission: 'procurement.sponsorship_forms.report',
      },
    ],
  },
  {
    title: 'Inventory Management',
    icon: Warehouse,
    moduleKey: 'inventory',
    children: [
      {
        title: 'Warehouses',
        path: '/inventory-management/warehouses',
        permission: 'inventory.warehouses.view',
      },
      {
        title: 'Storage Zones',
        path: '/inventory-management/storage-zones',
        permission: 'inventory.storage_zones.view',
      },
      {
        title: 'Stock',
        path: '/inventory-management/stock',
        permission: 'inventory.stock.view',
      },
      {
        title: 'Stock Batches',
        path: '/inventory-management/stock-batches',
        permission: 'inventory.stock_batches.view',
      },
      {
        title: 'Stock Ledger',
        path: '/inventory-management/stock-ledger',
        permission: 'inventory.stock_ledger.view',
      },
      {
        title: 'Stock Transfers',
        path: '/inventory-management/stock-transfers',
        permission: 'inventory.stock_transfers.view',
      },
    ],
  },
  {
    // Sales module (plan 3.7, UAC S1-17, owner rulings 26 Sep (Lavish) L1 and
    // 26 Sep 06:01 (Lavish) N1). No group-level moduleKey: each child carries the module
    // that owns its route, so Sales Agents (module `product`, path unchanged) stays visible
    // when `sales` is switched off.
    title: 'Sales',
    icon: TrendingUp,
    children: [
      {
        title: 'Targets',
        path: '/sales/targets',
        permission: 'sales.targets.view',
        moduleKey: 'sales',
      },
      {
        title: 'Opportunities',
        path: '/sales/opportunities',
        permission: 'sales.opportunities.view',
        moduleKey: 'sales',
      },
      {
        title: 'Sales Teams',
        path: '/sales/teams',
        permission: 'sales.teams.view',
        moduleKey: 'sales',
      },
      {
        title: 'Sales Agents',
        path: '/master-data-management/sales-agents',
        permission: 'master_data.sales_agents.view',
        moduleKey: 'product',
      },
    ],
  },
  {
    title: 'Project Sales',
    icon: Building2,
    moduleKey: 'projects',
    children: [
      {
        // Pipeline carries both views behind its own Board / Grid toggle, and the Grid
        // is the same list a separate "Projects" entry used to show. Two menu entries
        // onto one screen is a choice the user has to make and cannot get right.
        // /project-sales/projects still resolves, it redirects here.
        title: 'Pipeline',
        path: '/project-sales/pipeline',
        permission: 'projects.projects.view',
      },
      {
        title: 'Leads',
        path: '/project-sales/leads',
        permission: 'projects.projects.view',
      },
      {
        // Marketing's worklist: leads assigned but not yet accepted. Its own entry
        // because a lead nobody has accepted is nobody's job, and it stays invisible
        // if you have to go looking for it inside the leads list.
        title: 'Awaiting Acceptance',
        path: '/project-sales/lead-acceptance',
        permission: 'projects.projects.view',
      },
      {
        title: 'My Tasks',
        path: '/project-sales/my-tasks',
        permission: 'projects.projects.view',
      },
      {
        // Cross-project by nature: a claim raised on one project's sales order is
        // answered by another project's CS, so it cannot live under either project.
        title: 'Stock Claims',
        path: '/project-sales/stock-claims',
        permission: 'projects.projects.view',
      },
      {
        // Cross-project for the same reason claims are: a reconciliation belongs to one
        // sales order, but the point of the list is that a STACK of unanswered ones is
        // visible to management rather than discovered (AC-N6).
        title: 'AutoCount Differences',
        path: '/project-sales/divergences',
        permission: 'projects.projects.view',
      },
      {
        // Cross-project for the same reason the two entries above are: CS works the whole
        // published order book down in one pass, and a reconciliation that only exists
        // inside its own project is one nobody goes looking for.
        title: 'Fulfilment Planning',
        path: '/project-sales/fulfilment-planning',
        permission: 'projects.projects.view',
      },
      {
        // Directly under the board (R15), and cross-project for the same reason it is:
        // debt is read per product across the whole book, not inside one project.
        title: 'Stock Debt',
        path: '/project-sales/stock-debt',
        permission: 'projects.stock_debt.view',
      },
      {
        // "Is the plan stored, how do I review it" (captain, 19 Aug 2026 demo): the board
        // writes the decision, this is where it is reviewed afterwards - cross-project for
        // the same reason Fulfilment Planning beside it is.
        title: 'Plans',
        path: '/project-sales/plans',
        permission: 'projects.projects.view',
      },
      {
        // Purchasing's own worklist, and cross-project because purchasing is: the rows an
        // ADOPTED AutoCount order raises belong to no project at all, so inside a project
        // they are reachable nowhere. Shaped like the spreadsheet they work from today,
        // one tab per delivery month.
        title: 'Order Inquiries',
        path: '/project-sales/order-inquiries',
        permission: 'projects.projects.view',
      },
      {
        // Where the re-uploaded book's reaction to a planned line is reviewed and
        // applied - born on the sales-order upload confirmation, listed here after.
        title: 'Planning changes',
        path: '/project-sales/planning-changes',
        permission: 'projects.projects.view',
      },
      {
        title: 'Forecast & Reports',
        path: '/project-sales/reports',
        permission: 'projects.projects.view',
      },
      {
        title: 'Parties',
        path: '/project-sales/parties',
        permission: 'projects.parties.view',
      },
      {
        title: 'Setup',
        path: '/project-sales/setup',
        permission: 'projects.types.view',
      },
      {
        title: 'Series',
        path: '/project-sales/series',
        permission: 'projects.types.view',
      },
      {
        title: 'Price Floors',
        path: '/project-sales/price-floors',
        permission: 'projects.types.view',
      },
    ],
  },
  {
    title: 'Marketing Management',
    icon: Megaphone,
    moduleKey: 'marketing',
    children: [
      {
        title: 'Promotions',
        children: [
          {
            title: 'All Promotions',
            path: '/marketing-management/promotions',
          },
          {
            title: 'Promotion Attachments',
            path: '/marketing-management/promotion-attachments',
          },
          {
            title: 'Promotion Types',
            path: '/marketing-management/promotion-types',
          },
        ],
      },
      {
        title: 'Promotion Products',
        path: '/marketing-management/promotion-products',
      },
      {
        title: 'Campaigns',
        path: '/marketing-management/campaigns',
      },
    ],
  },
  {
    title: 'Forms Management',
    icon: FileText,
    moduleKey: 'forms',
    children: [
      {
        title: 'Forms',
        path: '/forms-management/forms',
      },
    ],
  },
  {
    title: 'Workflow Forms',
    icon: GitBranch,
    moduleKey: 'workflow_forms',
    children: [
      {
        title: 'Definitions',
        path: '/workflow-forms-management/definitions',
        permission: 'workflow_forms.definitions.view',
      },
    ],
  },
  {
    title: 'Resource Management',
    icon: Folder,
    moduleKey: 'resources',
    children: [
      {
        title: 'Files',
        path: '/resource-management/attachment-directories',
      },
      {
        title: 'Trash',
        path: '/resource-management/trash',
      },
      {
        title: 'Attachment Types',
        path: '/resource-management/attachment-types',
      },
    ],
  },
  {
    title: 'System Management',
    icon: Settings,
    moduleKey: 'base',
    children: [
      {
        title: 'Companies',
        path: '/system-management/companies',
        icon: Building2,
        superadminOnly: true,
      },
      {
        title: 'App Store',
        path: '/system-management/app-store',
        permission: 'system.modules.manage',
      },
      {
        title: 'Module bundles',
        path: '/system-management/app-store/bundles',
        permission: 'system.modules.manage',
      },
      {
        title: 'Import Jobs',
        path: '/system-management/import-jobs',
        superadminOnly: true,
      },
      {
        title: 'Import Logs',
        path: '/system-management/import-logs',
        superadminOnly: true,
      },
      {
        title: 'Tracking Validation',
        path: '/system-management/tracking-validation',
        superadminOnly: true,
      },
      {
        title: 'Audit Logs',
        path: '/system-management/audit-logs',
        superadminOnly: true,
      },
      {
        title: 'System Health',
        path: '/system-management/health',
        superadminOnly: true,
      },
      {
        title: 'Activity Timeline',
        path: '/system-management/activity',
        superadminOnly: true,
      },
      {
        title: 'Integrations',
        path: '/integration-management/integrations',
      },
      {
        title: 'Integration Logs',
        path: '/integration-management/integration-logs',
        superadminOnly: true,
      },
      {
        title: 'WhatsApp Templates',
        path: '/integration-management/whatsapp-templates',
        permission: 'integration.respond_templates.view',
      },
      {
        title: 'Scheduled Tasks',
        path: '/system-management/scheduled-tasks',
        superadminOnly: true,
      },
      {
        title: 'Outgoing Mails',
        path: '/system-management/outgoing-mails',
        permission: 'system.outgoing_mails.view',
      },
      {
        title: 'Email Outbox',
        path: '/system-management/email-outbox',
        permission: 'system.email_outbox.view',
      },
      {
        title: 'Respond Outbox',
        path: '/system-management/respond-outbox',
        permission: 'system.respond_outbox.view',
      },
      {
        title: 'Chat History',
        path: '/system-management/chat-history',
        permission: 'system.chat_history.view',
      },
      {
        title: 'Chatbot Console',
        path: '/system-management/chatbot-console',
        permission: 'system.chat_history.view',
      },
      {
        title: 'Chatbot Domains',
        path: '/system-management/chatbot-domains',
        permission: 'system.chat_history.view',
      },
      {
        title: 'Entity Kinds',
        path: '/system-management/chatbot-entity-kinds',
        permission: 'system.chat_history.view',
      },
      {
        title: 'API Call Log',
        path: '/system-management/api-call-logs',
        permission: 'system_management.api_call_log.view',
      },
      {
        title: 'Email Event Configs',
        path: '/system-management/email-event-configs',
        permission: 'system.email_event_configs.view',
      },
      {
        title: 'Email Templates',
        path: '/system-management/email-templates',
        permission: 'email_templates.templates.view',
      },
      {
        title: 'Email Theme',
        path: '/system-management/email-theme',
        permission: 'email_templates.templates.view',
      },
      {
        title: 'Automation',
        path: '/system-management/automation',
        permission: 'automation.automations.view',
      },
      {
        title: 'Work Calendar',
        path: '/system-management/work-calendar',
        superadminOnly: true,
      },
      {
        title: 'Running Numbers',
        path: '/system-management/numbering-rules',
        permission: 'system.numbering_rules.view',
      },
      {
        title: 'Import Column Mappings',
        path: '/system-management/import-field-aliases',
        permission: 'system.import_field_aliases.view',
      },
      {
        title: 'Status Graphs',
        path: '/system-management/status-graphs',
        permission: 'system.statuses.view',
      },
      {
        title: 'Translations',
        path: '/system-management/translations',
        permission: 'system.translations.view',
      },
      {
        title: 'Lookup Sets',
        path: '/master-data-management/lookup-sets',
        permission: 'master_data.lookup_sets.view',
      },
      {
        title: 'Respond.io Workspaces',
        path: '/system-management/respond-workspaces',
        permission: 'system.respond_workspaces.view',
      },
      {
        title: 'Respond.io Contacts',
        path: '/system-management/respond-contacts',
        permission: 'system.respond_workspaces.view',
      },
    ],
  },
  {
    title: 'Authentication',
    icon: Shield,
    children: [
      {
        title: 'Sign In',
        path: '/signin',
      },
      {
        title: 'Check Email',
        path: '/signup',
      },
      {
        title: 'Reset Password',
        path: '/reset-password',
      },
      {
        title: '2FA',
        path: '/2fa',
      },
      { title: 'Welcome Message', path: '/auth/welcome-message' },
      { title: 'Account Deactivated', path: '/auth/account-deactivated' },
      { title: 'Error 404', path: '/error/404' },
      { title: 'Error 500', path: '/error/500' },
    ],
  },
];

export const MENU_MEGA: MenuConfig = [
  { title: 'Home', path: '/' },
  {
    title: 'Profiles',
    children: [
      {
        title: 'Profiles',
        children: [
          {
            children: [
              {
                title: 'Default',
                icon: Badge,
                path: '/public-profile/profiles/default',
              },
              {
                title: 'Creator',
                icon: Coffee,
                path: '/public-profile/profiles/creator',
              },
              {
                title: 'Company',
                icon: Building,
                path: '/public-profile/profiles/company',
              },
              {
                title: 'NFT',
                icon: Bitcoin,
                path: '/public-profile/profiles/nft',
              },
              {
                title: 'Blogger',
                icon: MessageSquare,
                path: '/public-profile/profiles/blogger',
              },
              {
                title: 'CRM',
                icon: Monitor,
                path: '/public-profile/profiles/crm',
              },
              {
                title: 'Gamer',
                icon: Ghost,
                path: '/public-profile/profiles/gamer',
              },
            ],
          },
          {
            children: [
              {
                title: 'Feeds',
                icon: Book,
                path: '/public-profile/profiles/feeds',
              },
              {
                title: 'Plain',
                icon: File,
                path: '/public-profile/profiles/plain',
              },
              {
                title: 'Modal',
                icon: SquareMousePointer,
                path: '/public-profile/profiles/modal',
              },
              {
                title: 'Freelancer',
                icon: Briefcase,
                path: '#',
                disabled: true,
              },
              { title: 'Developer', icon: Code, path: '#', disabled: true },
              { title: 'Team', icon: Users, path: '#', disabled: true },
              {
                title: 'Events',
                icon: CalendarCheck,
                path: '#',
                disabled: true,
              },
            ],
          },
        ],
      },
      {
        title: 'Other Pages',
        children: [
          {
            children: [
              {
                title: 'Projects - 3 Cols',
                icon: Layout,
                path: '/public-profile/projects/3-columns',
              },
              {
                title: 'Projects - 2 Cols',
                icon: Grid,
                path: '/public-profile/projects/2-columns',
              },
              { title: 'Works', icon: WorkIcon, path: '/public-profile/works' },
              {
                title: 'Teams',
                icon: PeopleIcon,
                path: '/public-profile/teams',
              },
              {
                title: 'Network',
                icon: Network,
                path: '/public-profile/network',
              },
              {
                title: 'Activity',
                icon: TrendingUp,
                path: '/public-profile/activity',
              },
              {
                title: 'Campaigns - Card',
                icon: LayoutGrid,
                path: '/public-profile/campaigns/card',
              },
            ],
          },
          {
            children: [
              {
                title: 'Campaigns - List',
                icon: Kanban,
                path: '/public-profile/campaigns/list',
              },
              { title: 'Empty', icon: FileText, path: '/public-profile/empty' },
              {
                title: 'Documents',
                icon: DocumentIcon,
                path: '#',
                disabled: true,
              },
              { title: 'Badges', icon: Award, path: '#', disabled: true },
              { title: 'Awards', icon: Gift, path: '#', disabled: true },
            ],
          },
        ],
      },
    ],
  },
  {
    title: 'My Account',
    children: [
      {
        title: 'General Pages',
        children: [
          { title: 'Integrations', icon: Plug, path: '/account/integrations' },
          {
            title: 'Notifications',
            icon: Bell,
            path: '/account/notifications',
          },
          { title: 'API Keys', icon: Key, path: '/account/api-keys' },
          { title: 'Appearance', icon: Eye, path: '/account/appearance' },
          {
            title: 'Invite a Friend',
            icon: UserCheck,
            path: '/account/invite-a-friend',
          },
          { title: 'Activity', icon: LifeBuoy, path: '/account/activity' },
          { title: 'Brand', icon: CheckCircle, disabled: true },
          { title: 'Get Paid', icon: Euro, disabled: true },
        ],
      },
      {
        title: 'Other pages',
        children: [
          {
            title: 'Account Home',
            children: [
              { title: 'Get Started', path: '/account/home/get-started' },
              { title: 'User Profile', path: '/user-management/account' },
              {
                title: 'Company Profile',
                path: '/account/home/company-profile',
              },
              { title: 'With Sidebar', path: '/account/home/settings-sidebar' },
              {
                title: 'Enterprise',
                path: '/account/home/settings-enterprise',
              },
              { title: 'Plain', path: '/account/home/settings-plain' },
              { title: 'Modal', path: '/account/home/settings-modal' },
            ],
          },
          {
            title: 'Billing',
            children: [
              { title: 'Basic Billing', path: '/account/billing/basic' },
              { title: 'Enterprise', path: '/account/billing/enterprise' },
              { title: 'Plans', path: '/account/billing/plans' },
              { title: 'Billing History', path: '/account/billing/history' },
              { title: 'Tax Info', disabled: true },
              { title: 'Invoices', disabled: true },
              { title: 'Gateaways', disabled: true },
            ],
          },
          {
            title: 'Security',
            children: [
              { title: 'Get Started', path: '/account/security/get-started' },
              {
                title: 'Security Overview',
                path: '/account/security/overview',
              },
              {
                title: 'IP Addresses',
                path: '/account/security/allowed-ip-addresses',
              },
              {
                title: 'Privacy Settings',
                path: '/account/security/privacy-settings',
              },
              {
                title: 'Device Management',
                path: '/account/security/device-management',
              },
              {
                title: 'Backup & Recovery',
                path: '/account/security/backup-and-recovery',
              },
              {
                title: 'Current Sessions',
                path: '/account/security/current-sessions',
              },
              { title: 'Security Log', path: '/account/security/security-log' },
            ],
          },
          {
            title: 'Members & Roles',
            children: [
              { title: 'Teams Starter', path: '/account/members/team-starter' },
              { title: 'Teams', path: '/account/members/teams' },
              { title: 'Team Info', path: '/account/members/team-info' },
              {
                title: 'Members Starter',
                path: '/account/members/members-starter',
              },
              { title: 'Team Members', path: '/account/members/team-members' },
              {
                title: 'Import Members',
                path: '/account/members/import-members',
              },
              { title: 'Roles', path: '/account/members/roles' },
              {
                title: 'Permissions - Toggler',
                path: '/account/members/permissions-toggle',
              },
              {
                title: 'Permissions - Check',
                path: '/account/members/permissions-check',
              },
            ],
          },
          {
            title: 'Other Pages',
            children: [
              { title: 'Integrations', path: '/account/integrations' },
              { title: 'Notifications', path: '/account/notifications' },
              { title: 'API Keys', path: '/account/api-keys' },
              { title: 'Appearance', path: '/account/appearance' },
              { title: 'Invite a Friend', path: '/account/invite-a-friend' },
              { title: 'Activity', path: '/account/activity' },
            ],
          },
        ],
      },
    ],
  },
  {
    title: 'Network',
    children: [
      {
        title: 'General Pages',
        children: [
          { title: 'Get Started', icon: Flag, path: '/network/get-started' },
          { title: 'Colleagues', icon: Users, path: '#', disabled: true },
          { title: 'Donators', icon: Heart, path: '#', disabled: true },
          { title: 'Leads', icon: Zap, path: '#', disabled: true },
        ],
      },
      {
        title: 'Other pages',
        children: [
          {
            title: 'User Cards',
            children: [
              { title: 'Mini Cards', path: '/network/user-cards/mini-cards' },
              { title: 'Team Members', path: '/network/user-cards/team-crew' },
              { title: 'Authors', path: '/network/user-cards/author' },
              { title: 'NFT Users', path: '/network/user-cards/nft' },
              { title: 'Social Users', path: '/network/user-cards/social' },
              { title: 'Gamers', path: '#', disabled: true },
            ],
          },
          {
            title: 'User Base',
            badge: 'Datatables',
            children: [
              { title: 'Team Crew', path: '/network/user-table/team-crew' },
              { title: 'App Roster', path: '/network/user-table/app-roster' },
              {
                title: 'Market Authors',
                path: '/network/user-table/market-authors',
              },
              { title: 'SaaS Users', path: '/network/user-table/saas-users' },
              {
                title: 'Store Clients',
                path: '/network/user-table/store-clients',
              },
              { title: 'Visitors', path: '/network/user-table/visitors' },
            ],
          },
        ],
      },
    ],
  },
  {
    title: 'Apps',
    children: [
      {
        title: 'Store - Client',
        children: [
          {
            children: [
              { title: 'Home', path: '/store-client/home' },
              {
                title: 'Search Results - Grid',
                path: '/store-client/search-results-grid',
              },
              {
                title: 'Search Results - List',
                path: '/store-client/search-results-list',
              },
              {
                title: 'Product Details',
                path: '/store-client/product-details',
              },
              { title: 'Wishlist', path: '/store-client/wishlist' },
              { title: 'My Orders', path: '/store-client/my-orders' },
            ],
          },
          {
            children: [
              {
                title: 'Checkout - Order Summary',
                path: '/store-client/checkout/order-summary',
              },
              {
                title: 'Checkout - Shipping Info',
                path: '/store-client/checkout/shipping-info',
              },
              {
                title: 'Checkout - Payment Method',
                path: '/store-client/checkout/payment-method',
              },
              {
                title: 'Checkout - Order Placed',
                path: '/store-client/checkout/order-placed',
              },
              { title: 'Order Receipt', path: '/store-client/order-receipt' },
            ],
          },
        ],
      },
      {
        title: 'User Management',
        children: [
          {
            children: [
              {
                title: 'Administrative Users',
                path: '/user-management/users',
              },
              {
                title: 'Roles',
                path: '/user-management/roles',
              },
              {
                title: 'Permissions',
                path: '/user-management/permissions',
              },
              {
                title: 'Account',
                path: '/user-management/account',
              },
              {
                title: 'Logs',
                path: '/user-management/logs',
              },
              {
                title: 'Settings',
                path: '/user-management/settings',
              },
            ],
          },
        ],
      },
    ],
  },
];

export const MENU_MEGA_MOBILE: MenuConfig = [
  { title: 'Home', path: '/' },
  {
    title: 'Profiles',
    children: [
      {
        title: 'Profiles',
        children: [
          {
            title: 'Default',
            icon: Badge,
            path: '/public-profile/profiles/default',
          },
          {
            title: 'Creator',
            icon: Coffee,
            path: '/public-profile/profiles/creator',
          },
          {
            title: 'Company',
            icon: Building,
            path: '/public-profile/profiles/company',
          },
          { title: 'NFT', icon: Bitcoin, path: '/public-profile/profiles/nft' },
          {
            title: 'Blogger',
            icon: MessageSquare,
            path: '/public-profile/profiles/blogger',
          },
          { title: 'CRM', icon: Monitor, path: '/public-profile/profiles/crm' },
          {
            title: 'Gamer',
            icon: Ghost,
            path: '/public-profile/profiles/gamer',
          },
          {
            title: 'Feeds',
            icon: Book,
            path: '/public-profile/profiles/feeds',
          },
          {
            title: 'Plain',
            icon: File,
            path: '/public-profile/profiles/plain',
          },
          {
            title: 'Modal',
            icon: SquareMousePointer,
            path: '/public-profile/profiles/modal',
          },
          { title: 'Freelancer', icon: Briefcase, path: '#', disabled: true },
          { title: 'Developer', icon: Code, path: '#', disabled: true },
          { title: 'Team', icon: Users, path: '#', disabled: true },
          { title: 'Events', icon: CalendarCheck, path: '#', disabled: true },
        ],
      },
      {
        title: 'Other Pages',
        children: [
          {
            title: 'Projects - 3 Cols',
            icon: Layout,
            path: '/public-profile/projects/3-columns',
          },
          {
            title: 'Projects - 2 Cols',
            icon: Grid,
            path: '/public-profile/projects/2-columns',
          },
          { title: 'Works', icon: WorkIcon, path: '/public-profile/works' },
          { title: 'Teams', icon: PeopleIcon, path: '/public-profile/teams' },
          { title: 'Network', icon: Network, path: '/public-profile/network' },
          {
            title: 'Activity',
            icon: TrendingUp,
            path: '/public-profile/activity',
          },
          {
            title: 'Campaigns - Card',
            icon: LayoutGrid,
            path: '/public-profile/campaigns/card',
          },
          {
            title: 'Campaigns - List',
            icon: Kanban,
            path: '/public-profile/campaigns/list',
          },
          { title: 'Empty', icon: FileText, path: '/public-profile/empty' },
          { title: 'Documents', icon: DocumentIcon, path: '#', disabled: true },
          { title: 'Badges', icon: Award, path: '#', disabled: true },
          { title: 'Awards', icon: Gift, path: '#', disabled: true },
        ],
      },
    ],
  },
  {
    title: 'My Account',
    children: [
      {
        title: 'General Pages',
        children: [
          { title: 'Integrations', icon: Plug, path: '/account/integrations' },
          {
            title: 'Notifications',
            icon: Bell,
            path: '/account/notifications',
          },
          { title: 'API Keys', icon: Key, path: '/account/api-keys' },
          { title: 'Appearance', icon: Eye, path: '/account/appearance' },
          {
            title: 'Invite a Friend',
            icon: UserCheck,
            path: '/account/invite-a-friend',
          },
          { title: 'Activity', icon: LifeBuoy, path: '/account/activity' },
          { title: 'Brand', icon: CheckCircle, disabled: true },
          { title: 'Get Paid', icon: Euro, disabled: true },
        ],
      },
      {
        title: 'Other pages',
        children: [
          {
            title: 'Account Home',
            children: [
              { title: 'Get Started', path: '/account/home/get-started' },
              { title: 'User Profile', path: '/user-management/account' },
              {
                title: 'Company Profile',
                path: '/account/home/company-profile',
              },
              { title: 'With Sidebar', path: '/account/home/settings-sidebar' },
              {
                title: 'Enterprise',
                path: '/account/home/settings-enterprise',
              },
              { title: 'Plain', path: '/account/home/settings-plain' },
              { title: 'Modal', path: '/account/home/settings-modal' },
            ],
          },
          {
            title: 'Billing',
            children: [
              { title: 'Basic Billing', path: '/account/billing/basic' },
              { title: 'Enterprise', path: '/account/billing/enterprise' },
              { title: 'Plans', path: '/account/billing/plans' },
              { title: 'Billing History', path: '/account/billing/history' },
              { title: 'Tax Info', disabled: true },
              { title: 'Invoices', disabled: true },
              { title: 'Gateaways', disabled: true },
            ],
          },
          {
            title: 'Security',
            children: [
              { title: 'Get Started', path: '/account/security/get-started' },
              {
                title: 'Security Overview',
                path: '/account/security/overview',
              },
              {
                title: 'IP Addresses',
                path: '/account/security/allowed-ip-addresses',
              },
              {
                title: 'Privacy Settings',
                path: '/account/security/privacy-settings',
              },
              {
                title: 'Device Management',
                path: '/account/security/device-management',
              },
              {
                title: 'Backup & Recovery',
                path: '/account/security/backup-and-recovery',
              },
              {
                title: 'Current Sessions',
                path: '/account/security/current-sessions',
              },
              { title: 'Security Log', path: '/account/security/security-log' },
            ],
          },
          {
            title: 'Members & Roles',
            children: [
              { title: 'Teams Starter', path: '/account/members/team-starter' },
              { title: 'Teams', path: '/account/members/teams' },
              { title: 'Team Info', path: '/account/members/team-info' },
              {
                title: 'Members Starter',
                path: '/account/members/members-starter',
              },
              { title: 'Team Members', path: '/account/members/team-members' },
              {
                title: 'Import Members',
                path: '/account/members/import-members',
              },
              { title: 'Roles', path: '/account/members/roles' },
              {
                title: 'Permissions - Toggler',
                path: '/account/members/permissions-toggle',
              },
              {
                title: 'Permissions - Check',
                path: '/account/members/permissions-check',
              },
            ],
          },
          {
            title: 'Other Pages',
            children: [
              { title: 'Integrations', path: '/account/integrations' },
              { title: 'Notifications', path: '/account/notifications' },
              { title: 'API Keys', path: '/account/api-keys' },
              { title: 'Appearance', path: '/account/appearance' },
              { title: 'Invite a Friend', path: '/account/invite-a-friend' },
              { title: 'Activity', path: '/account/activity' },
            ],
          },
        ],
      },
    ],
  },
  {
    title: 'Network',
    children: [
      {
        title: 'General Pages',
        children: [
          { title: 'Get Started', icon: Flag, path: '/network/get-started' },
          { title: 'Colleagues', icon: Users, path: '#', disabled: true },
          { title: 'Donators', icon: Heart, path: '#', disabled: true },
          { title: 'Leads', icon: Zap, path: '#', disabled: true },
        ],
      },
      {
        title: 'Other pages',
        children: [
          {
            title: 'User Cards',
            children: [
              { title: 'Mini Cards', path: '/network/user-cards/mini-cards' },
              { title: 'Team Members', path: '/network/user-cards/team-crew' },
              { title: 'Authors', path: '/network/user-cards/author' },
              { title: 'NFT Users', path: '/network/user-cards/nft' },
              { title: 'Social Users', path: '/network/user-cards/social' },
              { title: 'Gamers', path: '#', disabled: true },
            ],
          },
          {
            title: 'User Base',
            badge: 'Datatables',
            children: [
              { title: 'Team Crew', path: '/network/user-table/team-crew' },
              { title: 'App Roster', path: '/network/user-table/app-roster' },
              {
                title: 'Market Authors',
                path: '/network/user-table/market-authors',
              },
              { title: 'SaaS Users', path: '/network/user-table/saas-users' },
              {
                title: 'Store Clients',
                path: '/network/user-table/store-clients',
              },
              { title: 'Visitors', path: '/network/user-table/visitors' },
            ],
          },
        ],
      },
    ],
  },
  {
    title: 'User Management',
    icon: Users,
    children: [
      {
        title: 'Users',
        path: '/user-management/users',
      },
      {
        title: 'Roles',
        path: '/user-management/roles',
      },
      {
        title: 'Permissions',
        path: '/user-management/permissions',
      },
      {
        title: 'Account',
        path: '/user-management/account',
      },
      {
        title: 'Logs',
        path: '/user-management/logs',
      },
      {
        title: 'Settings',
        path: '/user-management/settings',
      },
    ],
  },
  {
    title: 'Store - Client',
    children: [
      { title: 'Home', path: '/store-client/home' },
      {
        title: 'Search Results - Grid',
        path: '/store-client/search-results-grid',
      },
      {
        title: 'Search Results - List',
        path: '/store-client/search-results-list',
      },
      { title: 'Product Details', path: '/store-client/product-details' },
      { title: 'Wishlist', path: '/store-client/wishlist' },
      {
        title: 'Checkout',
        children: [
          {
            title: 'Order Summary',
            path: '/store-client/checkout/order-summary',
          },
          {
            title: 'Shipping Info',
            path: '/store-client/checkout/shipping-info',
          },
          {
            title: 'Payment Method',
            path: '/store-client/checkout/payment-method',
          },
          {
            title: 'Order Placed',
            path: '/store-client/checkout/order-placed',
          },
        ],
      },
      { title: 'My Orders', path: '/store-client/my-orders' },
      { title: 'Order Receipt', path: '/store-client/order-receipt' },
    ],
  },
];

export const MENU_HELP: MenuConfig = [
  {
    title: 'Getting Started',
    icon: Coffee,
    path: 'https://keenthemes.com/metronic/tailwind/docs/getting-started/installation',
  },
  {
    title: 'Support Forum',
    icon: AlertCircle,
    children: [
      {
        title: 'All Questions',
        icon: FileQuestion,
        path: 'https://devs.keenthemes.com',
      },
      {
        title: 'Popular Questions',
        icon: Star,
        path: 'https://devs.keenthemes.com/popular',
      },
      {
        title: 'Ask Question',
        icon: HelpCircle,
        path: 'https://devs.keenthemes.com/question/create',
      },
    ],
  },
  {
    title: 'Licenses & FAQ',
    icon: Captions,
    path: 'https://keenthemes.com/metronic/tailwind/docs/getting-started/license',
  },
  {
    title: 'Documentation',
    icon: FileQuestion,
    path: 'https://keenthemes.com/metronic/tailwind/docs',
  },
  { separator: true },
  { title: 'Contact Us', icon: Share2, path: 'https://keenthemes.com/contact' },
];

export const MENU_ROOT: MenuConfig = [
  {
    title: 'Public Profile',
    icon: UserCircle,
    rootPath: '/public-profile/',
    path: '/public-profile/profiles/default',
    childrenIndex: 2,
  },
  {
    title: 'Account',
    icon: Settings,
    rootPath: '/account/',
    path: '/',
    childrenIndex: 3,
  },
  {
    title: 'Network',
    icon: Users,
    rootPath: '/network/',
    path: '/network/get-started',
    childrenIndex: 4,
  },
  {
    title: 'Authentication',
    icon: Shield,
    rootPath: '/authentication/',
    path: '/authentication/get-started',
    childrenIndex: 5,
  },
  {
    title: 'Store - Client',
    icon: ShoppingCart,
    rootPath: '/store-client/',
    path: '/store-client/home',
    childrenIndex: 6,
  },
  {
    title: 'User Management',
    icon: ShieldUser,
    rootPath: '/user-management/',
    path: '/user-management/users',
    childrenIndex: 7,
  },
];
