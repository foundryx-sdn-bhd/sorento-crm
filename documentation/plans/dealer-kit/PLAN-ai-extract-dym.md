# PLAN: AI Extract did-you-mean for misread product codes (AI-EXTRACT-DYM)

Status: Plan (behaviour card filed as crew-ask, awaiting owner answers). Track: small fix (expected diff under ~300 lines, no migration, no auth/RBAC change).

## Problem

AI Extract on the Price tag request portal matches product codes exact-only
(`sorento_crm_backend/app/services/ai_extract/extract_service.py` `_resolve_product_codes`).
A code the model misread (SRTK1634SS-BL for SRTKT1634SS-BL) lands as "no match" and the
salesman has to retype it.

## Rule (owner, binding)

Suggest, never auto-apply. The user confirms each suggestion. Every failure ends in a clear state.

Behaviour card and decisions: see the crew-ask on the PR; this file is updated with the answers.
