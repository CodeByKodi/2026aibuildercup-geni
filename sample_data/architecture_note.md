# Architecture Note — Customer Profile API Caching

**Subject:** Caching approval for Customer Portal v12.4  
**Author:** Solution Architecture  
**Date:** 5 October 2026  
**Decision:** Approved  

## Decision

Caching is approved for the Customer Profile API. No architectural blocker exists for Customer Portal v12.4. The change can be deployed through the standard release pipeline.

## Scope of approval

The cache is application-level, user-scoped, and short-lived. Keys must include customer identifier and profile version. Shared or anonymous keys are not approved. The cache is a performance remediation for repeated profile reads, not a new system of record.

## Why this is not a blocker

Architecture reviewed the design against data classification, session security, and failure behaviour. A cache miss must fall back to the existing Profile Service. A cache outage must not prevent login or payment journeys. Token lifetime remains an identity concern (see SEC-217) and is outside this note.

## Deployment

Standard release pipeline: develop → pre-production → production change window after go-live approval. No emergency CAB path is required for the cache itself. Performance evidence after enablement should be attached to PERF-103.

## Residual comments

Architecture does not approve adding Cloud Run capacity as a substitute for this cache. Extra compute would increase cost without removing the repeated-read pattern documented in the performance test report.
