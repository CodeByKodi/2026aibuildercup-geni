# Security Assessment — Customer Portal v12.4

**Assessment window:** 1–7 October 2026  
**Assessor:** Application Security  
**Release impact:** Must be validated before production approval  

## Finding SEC-217

- **Severity:** High  
- **Status:** Open  
- **Owner:** Mei  
- **Target closure:** 12 October 2026  

### Issue

Session token expiration configuration is inconsistent across two services. The Customer Portal BFF expires idle sessions after 15 minutes. The Profile Service accepts a longer token lifetime from a shared identity library. A user who remains active in one service can retain a longer-lived session against the other service.

This is not a theoretical finding. The assessment reproduced a session that remained valid against Profile Service after the portal BFF had already marked the user idle. The risk is session continuation after an intended expiry, especially on shared devices in branch and contact-centre environments.

### Required outcome

Security finding SEC-217 must be verified closed. The expected control is a single token lifetime applied by both services, with idle expiry no longer than the portal policy. Evidence required: updated configuration, regression test, and security sign-off.

### Release impact

SEC-217 **must be validated before production approval**. It is a go-live condition, not a post-release task. The Release Manager should not approve production until Mei confirms the finding is closed.

## Related observations

A second medium finding on overly broad service permission was already remediating and is not a v12.4 approval condition. No critical vulnerability was found in the payment reconciliation path. Caching for Customer Profile API does not introduce a new data-classification issue if the cache key remains user-scoped.

## Recommendation

Treat SEC-217 as the security gate for 18 October 2026. Owner Mei. Due 12 October 2026. Do not approve go-live while the finding remains open.
