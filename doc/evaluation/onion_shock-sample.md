# Simulation report: `onion_shock`

Mode `inprocess` · seed 42 · 45 days · generated 2026-10-24 06:35 UTC

**1282** reports generated: 1269 accepted, 9 duplicates, 4 rejected. Replay took 67.7 s (19 reports/s); 0 dead letters.

## Targets

| Target | Value | Threshold | Result |
|---|---|---|---|
| MARKET_SHIFT detected within (h of the shock start) | 41.046 | 24 | ❌ fail |
| MARKET_SHIFT anomalies per shock (product, direction) ≤ | 1.000 | 1 | ✅ pass |

## Confusion matrix (truth × classification)

| truth | NORMAL | PENDING | SHOP_OUTLIER | MARKET_SHIFT | BAD_REPORT | EXPIRED | OPEN | REJECTED | DUPLICATE | UNSCORED | total |
|---|---|---|---|---|---|---|---|---|---|---|---|
| normal | 1121 | 0 | 5 | 31 | 0 | 1 | 0 | 0 | 3 | 0 | 1161 |
| bad_unit | 0 | 0 | 0 | 1 | 26 | 4 | 0 | 4 | 0 | 0 | 35 |
| malicious | 1 | 0 | 13 | 1 | 37 | 1 | 1 | 0 | 6 | 0 | 60 |
| shop_outlier | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 9 |
| market_shift | 1 | 0 | 9 | 6 | 0 | 1 | 0 | 0 | 0 | 0 | 17 |

| class | predicted | expected | correct | precision | recall |
|---|---|---|---|---|---|
| NORMAL | 1123 | 1158 | 1121 | 99.8% | 96.8% |
| BAD_REPORT | 63 | 84 | 63 | 100.0% | 75.0% |
| SHOP_OUTLIER | 36 | 9 | 9 | 25.0% | 100.0% |
| MARKET_SHIFT | 39 | 17 | 6 | 15.4% | 35.3% |

BAD_REPORT recall on scored `bad_unit` reports: **83.9%** (REJECTED by the guardrails and still-OPEN reports are outside the denominator).

## True events

| event | expected | detected | time to detect | alerts (expected kind / all) |
|---|---|---|---|---|
| market_shock onion ×1.4 (day 20, 00:00) | MARKET_SHIFT | yes | 41.0 h | 1 / 4 |
| shop_gouging eggs@shop-c ×1.3 (day 30, 08:00, 5 d) | SHOP_OUTLIER | yes | 5.1 h | 1 / 1 |

## False alarms

| detected at | kind | product | shop | direction | cause |
|---|---|---|---|---|---|
| 2026-01-08 13:08 | MARKET_SHIFT | rice | * | up | bad_data |
| 2026-01-10 15:27 | MARKET_SHIFT | onion | * | up | noise |
| 2026-01-11 09:18 | SHOP_OUTLIER | onion | shop-b | up | noise |
| 2026-01-15 09:21 | SHOP_OUTLIER | rice | shop-a | up | bad_data |
| 2026-01-24 10:14 | SHOP_OUTLIER | rice | shop-a | up | bad_data |
| 2026-02-03 05:56 | SHOP_OUTLIER | rice | shop-a | up | bad_data |
| 2026-02-09 03:17 | SHOP_OUTLIER | eggs | shop-e | up | noise |
| 2026-02-12 05:03 | SHOP_OUTLIER | onion | shop-b | down | noise |

False SHOP_OUTLIER per product per 30 days: onion 1.33, rice 2.00, eggs 0.67.

## Band accuracy

MAPE of the band p50 against the fair area price, from day 3 (end-of-day snapshots): **2.71%**; a band existed on 100% of product-days.

| product | MAPE | median band mean age | median age of newest report |
|---|---|---|---|
| onion | 4.16% | 48.1 h | 3.3 h |
| rice | 1.07% | 220.5 h | 2.7 h |
| eggs | 2.89% | 133.3 h | 3.3 h |

## Guardrail rejections

| code | count |
|---|---|
| QUANTITY_OUT_OF_RANGE | 4 |

Anomalies opened: MARKET_SHIFT 3, SHOP_OUTLIER 10.
