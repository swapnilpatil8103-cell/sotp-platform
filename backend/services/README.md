# backend/services

External integration clients: SEC EDGAR client and market data client. Owns
rate limiting, on-disk response caching (`data/cache/`), retries, and raw
response parsing. Implemented starting Phase 2 (SEC pipeline) and Phase 4
(market data). Empty in Phase 1.
