# Methodology

This document describes the current implementation of time semantics, price adjustment, provider fallback and LLM boundaries. It is a method description, not a promise that an upstream provider is always complete.

## Point-in-time financial data

`end_date` is the reporting-period end. `ann_date` and `f_ann_date` describe when a record was publicly disclosed; `f_ann_date` takes precedence when both are available. `as_of` is the analysis information cutoff.

With an `as_of` date, records from later reporting periods or later public announcements are excluded. For duplicate `ts_code + end_date` rows, the canonical selection prefers the latest announcement, `update_flag=1`, consolidated report type, and then stable input order. Missing announcement metadata is retained for legacy-cache compatibility but produces a degraded-quality warning; it is not evidence of a complete PIT guarantee. A future announcement remains a blocking error.

## Price adjustment

Raw, forward-adjusted (`qfq`) and backward-adjusted (`hfq`) OHLC values are distinct modes. `hfq` multiplies OHLC by the provider factor; `qfq` uses the last factor in the requested range as the base and multiplies each row by `factor / last_factor`. Volume and amount remain provider-native. Missing or invalid factors fail the request instead of silently returning raw prices. The implementation version is `tushare-factor-v1`.

## Providers, cache and quality

Tushare Pro is the structured primary source and AkShare is a controlled fallback. Fallback changes provenance, never invents values. Dataset descriptors retain provider, as-of date, row count, quality, warnings and adjustment mode. Cache reuse is bounded by trading-day and freshness checks.

## LLM boundary

Python computes financial filtering, revisions, ratios, indicators, valuation, risk, sentiment proxies, adjustments, rankings and validation. The LLM receives only the compact package fields (`meta`, `stock`, calculated `technical`, `valuation`, `fundamental`, `risk`, `sentiment`, `price_levels`, change summary and `validation`) plus relevant knowledge context; it summarizes and organizes the report. Complete historical series, raw financial tables, provider responses, adjustment factors, local cache paths and machine paths are not sent to the model. A blocked validation gate means no LLM call.

## Audit outputs

Analysis packages preserve schema version, run ID, snapshot reference, data gaps, warnings and validation results. Public benchmarks expose only manifests, source URLs, announcement IDs, hashes and expected rules. Raw PDFs, full market histories and local run outputs remain user-supplied or local-only.

See [the Chinese guide](methodology.md) for field-level details and the [benchmark guide](../benchmarks/README.md) for the offline workflow.
