# UAT Meeting Notes — Customer Portal v12.4

**Date:** 7 October 2026  
**Attendees:** Sara (Release Manager), Priya (Business owner), Arjun, Mei, QA lead  
**Subject:** User acceptance testing and go-live blockers  

## Decision

UAT sign-off is pending defect verification. The payment reconciliation scenario has one unresolved defect. No full UAT sign-off will be given until that defect is retested.

## Defect PAY-482

- **Issue:** Payment reconciliation scenario fails when a same-day reversal is posted after a partial settlement.  
- **Severity / tracker:** Medium, PAY-482  
- **Business owner:** Priya  
- **Due:** 11 October 2026  
- **Decision:** UAT sign-off pending defect verification.

Priya owns the business confirmation. QA can retest as soon as the fix is in the UAT environment. Until PAY-482 is verified, the release must not be described as UAT-complete.

## Other UAT scenarios

Profile edit, statement download, and session timeout scenarios passed in this cycle. They are not open blockers. The only UAT approval condition for 18 October 2026 is PAY-482.

## Related dependencies

Mei reported that SEC-217 remains open and is a production-approval condition. Arjun reported that Customer Profile API p95 is still 2.4 seconds against a 1.5 second target. Those items are tracked outside UAT but they affect the same go / no-go review.

## Actions captured in the meeting

1. Priya to confirm PAY-482 retest and complete UAT sign-off by 11 October 2026.  
2. QA to attach retest evidence to the defect before sign-off.  
3. Release Manager to keep go-live at Amber until UAT, security, and performance conditions are closed.
