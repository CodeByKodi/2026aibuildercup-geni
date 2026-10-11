# Performance Test Report — Customer Portal v12.4

**Test cycle:** Soak and peak, 6–7 October 2026  
**Environment:** Pre-production, production-like data volume  
**Owner for remediation:** Arjun  
**Target retest:** 13 October 2026  

## Customer Profile API

The Customer Profile API is the primary latency gate for this release.

- **Current p95:** 2.4 seconds  
- **Release target:** less than 1.5 seconds  
- **Current p99:** 3.1 seconds  
- **Error rate:** 0.2 percent (inside the 1 percent budget)

The API fails the release performance gate. Peak-hour traces show repeated reads of the same customer profile within a single page render. The query pattern, not infrastructure headroom, is the dominant cost.

## Recommended action

Enable the approved application cache for Customer Profile API and rerun the performance test. Architecture has already approved caching for this API. The change can be deployed through the standard release pipeline. Do not add compute as the first response; extra Cloud Run capacity will raise cost without fixing the repeated-read pattern.

### Expected outcome

p95 latency below 1.5 seconds on the same soak and peak profile used in this report. Owner: Arjun. Target: 13 October 2026.

## Other services

Payment reconciliation and session services met their latency budgets in this cycle. They are not release blockers. The only performance approval condition for go-live is Customer Profile API p95.

## Test notes

Load model: 1,200 concurrent customers, 30-minute soak, 10-minute peak. Cache-off baseline is the 2.4 second p95 cited above. A successful retest should attach the new p95, p99, and error-rate evidence to the go / no-go pack.
