"""SQLAlchemy models."""
# Import all models here so Alembic can discover them
# Import order matters for relationships - import base models first
from app.models.company import Company, UserCompany, RespondContactCompany
from app.models.user import User, UserRole, UserRoleAssignment, UserPermission, UserRolePermission, SystemLog, SystemSetting, UserQuickAccess, UserListColumnConfig
from app.models.auth import VerificationToken
from app.models.product import Product, ProductCategory, Brand, UnitOfMeasure
from app.models.country import Country
from app.models.product_set import ProductSet, ProductSetMember
from app.models.product_companion import ProductCompanionRule, ProductCompanionRuleHost
from app.models.product_combo import ProductCombo, ProductComboPart
from app.models.product_set_proposal import ProductSetProposalBatch, ProductSetProposal
from app.models.product_spec import ProductSpecRegistry, ProductSpecifications, ProductSpecException, ProductSpecVerification, ProductSpecFlyerBatch, ProductSpecFlyerProposal
from app.models.order import Order, OrderStatus, Customer, CustomerContact, OrderLine, SalesOrder, SalesOrderLine
from app.models.sales_agent import SalesAgent
from app.models.sales import SalesTeam, SalesTeamMember
from app.models.stock_ask import StockAsk
from app.models.finance import BillingDocument, BillingDocumentLine
from app.models.autocount_branch import Branch
from app.models.autocount_compare_mapping import AutocountCompareMapping
from app.models.inventory import Warehouse, StorageZone, Stock, StockBatch, StockLedger
from app.models.procurement import Supplier, ProductSupplier, InboundShipment, InboundShipmentLine, SPOAllocation, PickingHeader, PickingLine, StockInquiry, PurchaseRequestHeader, PurchaseRequestLine, PurchaseOrder, PurchaseOrderLine
from app.models.cost_price import ProductSupplierCost, CostPriceChangeSet, CostPriceChangeLine, SupplierPriceLink
from app.models.supplier_notice import SupplierNotice, SupplierNoticeLine
from app.models.marketing import Promotion, PromotionGroup, PromotionProduct, CampaignType, MarketingCampaign
from app.models.forms import Form, FormSection, FormField, FormVersion, FormSubmission
from app.models.workflow_forms import (
    WorkflowFormDefinition,
    WorkflowFormVersion,
    WorkflowSubmission,
    WorkflowSubmissionLine,
    WorkflowSubmissionTransitionLog,
)
from app.models.complaints import Complaint, ComplaintAttachment, ComplaintManualAttachment, ComplaintFulfilmentOrder
from app.models.complaint_master_data import ComplaintRootCause, ComplaintResolution
from app.models.entity_attachment import EntityAttachmentLink
from app.models.attachment_field_link import AttachmentFieldLink
from app.models.sla import SLAPolicy, SLAPolicyTier, ConversationSLATracking, ConversationSLAEventLog, FormSLAConfig
from app.models.ticket_comment import ConversationTicketComment
from app.models.message_snippet import MessageSnippet
from app.models.resources import Attachment, AttachmentType
from app.models.certificate import Certificate, CertificateRevision, CertificateProduct
from app.models.access import AccessAgent, ContactAgentAccess, ContactAccessType, RespondContact, RespondContactCustomer, StockVisibilityPolicy, respond_contact_access_types, MarketSegment, respond_contact_market_segments, team_member_market_segments, team_member_brands
from app.models.respond_workspace import RespondWorkspace
from app.models.respond_template import (
    RespondChannel,
    RespondMessageTemplate,
    RespondTemplateDefault,
)
from app.models.status import Status, StatusTransition
from app.models.projects import (
    Project,
    ProjectTask,
    ProjectTemplateTask,
    ProjectBrand,
    ProjectCollaborator,
    ProjectParty,
    ProjectSalesProfile,
    ProjectStakeholder,
    ProjectTakeoverRequest,
    ProjectTemplate,
    ProjectTemplateRole,
    ProjectType,
)
from app.models.entity_conversation import EntityConversationMessage
from app.models.integration import Integration, IntegrationApiKey, IntegrationLog
from app.models.integration_reference import IntegrationReference
from app.models.ideation_status_event import IdeationStatusEventCursor
from app.models.import_log import ImportLog
from app.models.import_alias import ImportFieldAlias
from app.models.translation_memory import TranslationMemory
from app.models.calendar import PublicHoliday, WorkCalendarConfig
from app.models.job import ImportJob, ImportJobRow
from app.models.download import UserDownload, DownloadStatus
from app.models.report_view import ReportView
from app.models.saved_view import SavedView
from app.models.audit import AuditLog, AuditTrailGap
from app.models.notification import Notification, NotificationDelivery, PushSubscription
from app.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from app.models.health_alert_state import HealthAlertState
from app.models.numbering import DocumentNumberingRule
from app.models.app_modules import AppModuleCatalog, AppModuleBundle, TenantModule, ModuleInstallEvent
from app.models.list_query_metadata import ListQueryResource, ListQueryField
from app.models.embeddings import EmbeddingQueue, EmbeddingDocument, EmbeddingChunk
from app.models.ai_assistant import (
    AIAssistantConfig,
    AIAssistantConversation,
    AIAssistantMessage,
    AIAssistantGovernanceEvent,
    AIAssistantUsageLog,
    AIAssistantWishlistCluster,
    AIAssistantUnansweredQuery,
)
from app.models.ai_prompt import AIPromptVersion, AIPromptLabel
from app.models.chat_history import ChatHistory
from app.models.chatbot_turn import ChatbotTurn
from app.models.conversation_frame import ConversationFrame
from app.models.chatbot_policy import ChatbotDomain, ChatbotDomainWord, ChatbotEntityKind, ChatbotStatusWord
from app.models.lookup import LookupSet, LookupOption, LookupOptionKeyword, LookupBinding
from app.models.portal import (
    PortalToken,
    PortalOtpCode,
    PortalFormRevision,
    PortalRevisionConfig,
)
from app.models.media import (
    ContactMediaLimit,
    ContactMediaUsage,
    MediaExtractionJob,
)
from app.models.user_session import UserSession
from app.models.activities import ActivityEvent, InternalNote, ActivityMention
from app.models.tickets import Ticket, TicketWatcher, TicketRespondContactLink
from app.models.email_template import EmailTemplate
from app.models.automation import Automation, AutomationRun
from app.models.impersonation import ImpersonationSession, ContactImpersonationSession
from app.models.email_outbox import EmailOutbox, EmailEventConfig
from app.models.dealer_kit import (
    Page,
    PageVersion,
    PageLabel,
    TileTemplate,
    Asset,
    Collection,
    Bundle,
    BundleComponent,
    FlyerReadingRecord,
    Selection,
    SelectionLine,
    Edition,
    ExportRequest,
    TagTemplate,
)
from app.models.price_tag import (
    ContactPortalFormOverride,
    PriceTagRequest,
    PriceTagRequestLine,
)
from app.models.onboarding import (
    OnboardingTemplate,
    OnboardingRequest,
    OnboardingPerson,
)
from app.models.scm import (
    ReorderPolicy,
    ItemClassification,
    AbcXyzPolicy,
    SupplierScoringPolicy,
    SupplierPerformance,
    PurchasingBudget,
    ReorderRun,
    ReorderRecommendation,
    RecommendationOverride,
    OverrideReason,
    ReasonActionMap,
    CashRankingPolicy,
    DemandNatureMap,
    DemandStat,
    MarketResearchTopic,
    MarketSignal,
    ScmAnalyticsRun,
    MarketResearchRun,
    PriorityPolicy,
)
from app.models.project_so import (  # noqa: F401
    ProjectPOVersion,
    ProjectPOLine,
    ProjectPOAnnotation,
    DeliverySchedule,
    DeliveryScheduleVersion,
    ProjectDeliveryPhase,
    DeliveryScheduleCell,
    CustomerItemCodeMap,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SODraftFinding,
    OrderChangeNotice,
    SOAmendment,
    OrderInquiry,
    OrderInquiryRow,
)
from app.models.stock_transfer import StockTransfer  # noqa: F401
from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow  # noqa: F401

__all__ = [
    "IdeationStatusEventCursor",
    "ImportFieldAlias",
    "PriorityPolicy",
    "Company",
    "UserCompany",
    "RespondContactCompany",
    "User",
    "UserRole",
    "UserRoleAssignment",
    "UserPermission",
    "UserRolePermission",
    "SystemLog",
    "SystemSetting",
    "UserQuickAccess",
    "UserListColumnConfig",
    "Product",
    "ProductSet",
    "ProductSetMember",
    "ProductSetProposalBatch",
    "ProductSetProposal",
    "ProductCategory",
    "Brand",
    "UnitOfMeasure",
    "Order",
    "OrderStatus",
    "Customer",
    "CustomerContact",
    "OrderLine",
    "SalesOrder",
    "SalesOrderLine",
    "StockTransfer",
    "SalesAgent",
    "Warehouse",
    "StorageZone",
    "Stock",
    "StockBatch",
    "StockLedger",
    "Supplier",
    "SupplierNotice",
    "SupplierNoticeLine",
    "ProductSupplier",
    "InboundShipment",
    "InboundShipmentLine",
    "SPOAllocation",
    "PickingHeader",
    "PickingLine",
    "StockInquiry",
    "PurchaseRequestHeader",
    "PurchaseRequestLine",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "Promotion",
    "PromotionGroup",
    "PromotionProduct",
    "CampaignType",
    "MarketingCampaign",
    "Form",
    "FormSection",
    "FormField",
    "FormVersion",
    "FormSubmission",
    "WorkflowFormDefinition",
    "WorkflowFormVersion",
    "WorkflowSubmission",
    "WorkflowSubmissionLine",
    "WorkflowSubmissionTransitionLog",
    "Complaint",
    "ComplaintAttachment",
    "ComplaintManualAttachment",
    "ComplaintFulfilmentOrder",
    "EntityAttachmentLink",
    "AttachmentFieldLink",
    "SLAPolicy",
    "SLAPolicyTier",
    "ConversationSLATracking",
    "ConversationSLAEventLog",
    "ConversationTicketComment",
    "MessageSnippet",
    "FormSLAConfig",
    "Attachment",
    "AttachmentType",
    "Certificate",
    "CertificateRevision",
    "CertificateProduct",
    "AccessAgent",
    "ContactAgentAccess",
    "ContactAccessType",
    "RespondContact",
    "RespondContactCustomer",
    "respond_contact_access_types",
    "StockVisibilityPolicy",
    "RespondWorkspace",
    "RespondChannel",
    "RespondMessageTemplate",
    "RespondTemplateDefault",
    "Status",
    "StatusTransition",
    "Project",
    "ProjectTask",
    "ProjectTemplateTask",
    "ProjectBrand",
    "ProjectCollaborator",
    "ProjectParty",
    "ProjectSalesProfile",
    "ProjectStakeholder",
    "ProjectTakeoverRequest",
    "ProjectTemplate",
    "ProjectTemplateRole",
    "ProjectType",
    "EntityConversationMessage",
    "IntegrationLog",
    "ImportLog",
    "ImportJob",
    "ImportJobRow",
    "UserDownload",
    "ReportView",
    "DownloadStatus",
    "VerificationToken",
    "PublicHoliday",
    "WorkCalendarConfig",
    "AuditLog",
    "AuditTrailGap",
    "Notification",
    "NotificationDelivery",
    "PushSubscription",
    "ScheduledTask",
    "ScheduledTaskRun",
    "DocumentNumberingRule",
    "AppModuleCatalog",
    "AppModuleBundle",
    "TenantModule",
    "ModuleInstallEvent",
    "ListQueryResource",
    "ListQueryField",
    "EmbeddingQueue",
    "EmbeddingDocument",
    "EmbeddingChunk",
    "AIAssistantConfig",
    "AIAssistantConversation",
    "AIAssistantMessage",
    "AIAssistantGovernanceEvent",
    "AIAssistantUsageLog",
    "AIAssistantWishlistCluster",
    "AIAssistantUnansweredQuery",
    "AIPromptVersion",
    "AIPromptLabel",
    "ChatHistory",
    "ConversationFrame",
    "ChatbotDomain",
    "ChatbotEntityKind",
    "ChatbotStatusWord",
    "ChatbotDomainWord",
    "LookupSet",
    "LookupOption",
    "LookupOptionKeyword",
    "LookupBinding",
    "PortalToken",
    "PortalOtpCode",
    "PortalFormRevision",
    "PortalRevisionConfig",
    "ContactMediaLimit",
    "ContactMediaUsage",
    "MediaExtractionJob",
    "UserSession",
    "ActivityEvent",
    "InternalNote",
    "ActivityMention",
    "Ticket",
    "TicketWatcher",
    "TicketRespondContactLink",
    "EmailTemplate",
    "Automation",
    "AutomationRun",
    "ImpersonationSession",
    "ContactImpersonationSession",
    "EmailOutbox",
    "EmailEventConfig",
    "ReorderPolicy",
    "ItemClassification",
    "AbcXyzPolicy",
    "SupplierScoringPolicy",
    "SupplierPerformance",
    "PurchasingBudget",
    "ReorderRun",
    "ReorderRecommendation",
    "RecommendationOverride",
    "OverrideReason",
    "ReasonActionMap",
    "CashRankingPolicy",
    "DemandNatureMap",
    "DemandStat",
    "MarketResearchTopic",
    "MarketSignal",
    "ScmAnalyticsRun",
    "MarketResearchRun",
    # Sales (schema: sales)
    "SalesTeam",
    "SalesTeamMember",
    # Finance (schema: finance)
    "BillingDocument",
    "BillingDocumentLine",
    # AutoCount branch table (#1354 S2)
    "Branch",
    "AutocountCompareMapping",
    # Dealer Kit (schema: dealer_kit)
    "Page",
    "PageVersion",
    "PageLabel",
    "TileTemplate",
    "Asset",
    "Collection",
    "Bundle",
    "Selection",
    "SelectionLine",
    "BundleComponent",
    "FlyerReadingRecord",
    "Edition",
    "ExportRequest",
    "TagTemplate",
    # Price Tag Request
    "ContactPortalFormOverride",
    "PriceTagRequest",
    "PriceTagRequestLine",
    # Onboarding intake / review / provisioning
    "OnboardingTemplate",
    "OnboardingRequest",
    "OnboardingPerson",
    # Chatbot stock ask v2 S5
    "StockAsk",
]

# Auto-discovery: import models.py from each app/modules/<key>/ so Alembic + SQLAlchemy
# see them. Safe with shimmed legacy paths above (re-imports are no-ops).
from app.modules.runtime.discovery import discover_module_models  # noqa: E402
discover_module_models()
