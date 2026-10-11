# Release Status Report — Customer Portal v12.4

**Programme:** Customer Portal modernization  
**Release:** Customer Portal v12.4  
**Target go-live:** 18 October 2026  
**Overall status:** Amber  
**Prepared by:** Release Management Office  
**Date:** 8 October 2026  

## Summary

Customer Portal v12.4 is the next production release for the customer-facing portal used by retail and commercial banking users. The release includes profile performance work, payment reconciliation fixes, and session-security hardening. The Release Manager must decide whether the organisation is ready to go live on 18 October 2026.

The current recommendation from this report is **not ready**. Three approval conditions remain open. No production change window should be booked until those conditions are verified.

## Open critical items

There are **3** open critical items that block go-live approval:

1. Security validation of finding SEC-217.
2. Performance remediation of Customer Profile API p95 latency.
3. UAT sign-off pending defect PAY-482.

## Key dependencies

- Security validation must complete before production approval.
- Performance remediation must show p95 below the 1.5 second release gate.
- UAT sign-off is pending defect verification by the business owner.

## Schedule

| Milestone | Date | Status |
| --- | --- | --- |
| Code freeze | 9 October 2026 | On track |
| Security validation target | 12 October 2026 | At risk |
| Performance retest target | 13 October 2026 | At risk |
| UAT defect verification | 11 October 2026 | At risk |
| Go / no-go review | 16 October 2026 | Not started |
| Production go-live | 18 October 2026 | Not approved |

## Release scope

In scope: Customer Profile API caching, session token consistency, payment reconciliation defect PAY-482, supporting runbooks, and standard pipeline deployment. Out of scope: new product journeys, branch migration, and any change that has not completed security or performance review.

## Residual risk

Amber is driven by incomplete approval conditions, not by an architectural blocker. Architecture has already approved the cache design. The remaining work is verification, not redesign. If SEC-217, PAY-482, and the p95 gate are closed this week, the release can return to the go-live board for human approval.
