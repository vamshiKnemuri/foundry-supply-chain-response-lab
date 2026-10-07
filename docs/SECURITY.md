# Security and deployment limits

The dashboard is a **single-user local demo**. It binds to `127.0.0.1`, checks host/origin, limits request size, escapes dynamic UI text, and writes only to a local SQLite database. The `approver` field is a demonstration label, not authenticated identity. The ledger hash chain detects accidental or unsophisticated tampering; without an external trust anchor it is not a tamper-proof audit system.

Do not expose this server to a network or use the approval field as production authorization. A production Foundry implementation needs SSO-backed approver identity, least-privilege actions, concurrency control at the data layer, stronger audit storage, monitoring, rate limits, and tenant-specific tests. The optional OpenAI call sends candidate metadata to OpenAI; use only approved data and organizational policies. It does not send supplier free-text notes.
