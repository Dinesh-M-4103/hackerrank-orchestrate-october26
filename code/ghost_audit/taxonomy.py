"""Resource-ID, system and policy-category vocabulary derived from the dataset (activity targets, evidence collections)."""
from __future__ import annotations

import re
from typing import Optional

PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "support": "support_platform",
    "analytics": "analytics_platform",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "legal_hold": "legal_service",
    "tombstone": "identity_service",
    "marketing": "marketing_platform",
    "object": "object_storage",
}

PREFIX_CATEGORY = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "support": "SUPPORT_MESSAGE",
    "analytics": "ANALYTICS_PROFILE",
    "cache": "CACHE_ENTRY",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "export": "USER_EXPORT",
    "data_flow": "DATA_FLOW",
    "backup": "BACKUP_ARCHIVE",
    "audit_record": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "marketing": "MARKETING_PROFILE",
    "object": "SUPPORT_ATTACHMENT",
}

COLLECTION_CATEGORY = {
    "customer_profiles": "USER_PROFILE",
    "customer_profiles_primary": "USER_PROFILE",
    "customer_profiles_failover_replica": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "search_documents_primary": "SEARCH_INDEX_ENTRY",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "support_records": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_export_jobs": "USER_EXPORT",
    "customer_exports": "USER_EXPORT",
    "connector_configurations": "DATA_FLOW",
    "connector_subscriptions": "DATA_FLOW",
    "backup_catalog": "BACKUP_ARCHIVE",
    "backup_restore_entries": "BACKUP_ARCHIVE",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "security_audit_records": "SECURITY_AUDIT_LOG",
    "retained_order_records": "LEGAL_HOLD_RECORD",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
}

COLLECTION_SYSTEM = {
    "customer_profiles": "account_service",
    "customer_profiles_primary": "account_service",
    "customer_profiles_failover_replica": "account_service",
    "customer_search_documents": "search_service",
    "search_documents_primary": "search_service",
    "customer_analytics_reports": "analytics_platform",
    "customer_analytics_profiles": "analytics_platform",
    "support_conversations": "support_platform",
    "support_records": "support_platform",
    "ticket_attachments": "support_platform",
    "customer_export_jobs": "export_service",
    "customer_exports": "export_service",
    "connector_configurations": "integration_service",
    "connector_subscriptions": "integration_service",
    "backup_catalog": "backup_service",
    "backup_restore_entries": "backup_service",
    "security_audit_events": "security_service",
    "security_audit_records": "security_service",
    "retained_order_records": "legal_service",
    "deleted_customer_references": "identity_service",
}

_ID_RE = re.compile(r"^([a-z]+(?:_[a-z]+)*)_(\d+)$")
RESOURCE_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(sorted(PREFIX_SYSTEM, key=len, reverse=True)) + r")_\d+\b"
)


def resource_prefix(resource_id: str) -> Optional[str]:
    m = _ID_RE.match(resource_id or "")
    return m.group(1) if m else None


def system_for_resource(resource_id: str) -> Optional[str]:
    return PREFIX_SYSTEM.get(resource_prefix(resource_id) or "")


def category_for_resource(resource_id: str, collection: Optional[str] = None, fields: Optional[dict] = None) -> Optional[str]:
    """Policy category of a resource. The collection wins over the ID prefix; attachment-shaped support records are attachments."""
    if collection and collection in COLLECTION_CATEGORY:
        cat = COLLECTION_CATEGORY[collection]
    else:
        cat = PREFIX_CATEGORY.get(resource_prefix(resource_id) or "")
    if cat == "SUPPORT_MESSAGE" and fields and any(k in fields for k in ("file_name", "uploaded_by", "attachment_id")):
        return "SUPPORT_ATTACHMENT"
    return cat
