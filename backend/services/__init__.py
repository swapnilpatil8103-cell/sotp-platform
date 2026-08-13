"""
External integration clients.

This package will house the SEC EDGAR client (Phase 2) and market data client
(Phase 4): rate limiting, on-disk caching under data/cache/, retries, and
response parsing into raw dicts (normalization into FinancialFact rows
happens in backend/data/, not here). No external HTTP calls are made in
Phase 1.
"""
