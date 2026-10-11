# Issue Tracker Export — Customer Portal v12.4

**Export date:** 8 October 2026  
**Filter:** Open or in-progress items tagged release:v12.4  

## Release-blocking items

| ID | Title | Severity | Status | Owner | Due | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-217 | Session token expiration inconsistent across two services | High | Open | Mei | 12 Oct 2026 | Must be validated before production approval |
| PERF-103 | Customer Profile API p95 above release gate | High | In Progress | Arjun | 13 Oct 2026 | Current p95 2.4s; target < 1.5s; cache approved |
| PAY-482 | Payment reconciliation same-day reversal defect | Medium | Awaiting Retest | Priya | 11 Oct 2026 | UAT sign-off pending verification |

## Status summary

- SEC-217 — High — Open  
- PERF-103 — High — In Progress  
- PAY-482 — Medium — Awaiting Retest  

These three items are the approval conditions for go-live. There is no fourth open critical item on this board.

## Non-blocking items

Several low defects remain in the backlog (copy fixes, non-critical telemetry). They are not go-live conditions and should not be presented as release blockers.

## Recommended priority this week

1. PAY-482 defect closure and UAT sign-off (due 11 October).  
2. SEC-217 security validation (due 12 October).  
3. PERF-103 cache deploy and performance retest (due 13 October).
