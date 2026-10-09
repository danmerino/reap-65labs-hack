# Scout dashboard and demo storefront

Dashboard: https://sshi.tail4d8e5a.ts.net:8491/dashboard/

`server.py` serves an explicit file allowlist and sanitized API projections.
`web/dashboard/agent.js` drives tabs, Fund, Pay and Buy again;
`web/dashboard/chat.js` manages separate visitor/agent threads.
`agent-dossiers.json` contains the detailed watch history, specifications,
FAQs and illustrative delivery plans. Chat uses real Pi responses through
`chat_worker.py` and cannot make financial transactions.

`agent_flow.py` persists active jobs, archived purchase cycles and chronological
events. Completed cycles retain their receipts; retries use the same deposit
and payment identities. Buy again is available only after verified settlement
and worker completion. It clears active quote/payment state and queues a fresh
quote without sending funds. Each successful payment adds one fictional unit
once, deduplicated by payment ID. The fixed substitute is EVRYJEWELS It Girl
Sticker Pack, observed at 3.96 test USDC including shipping and tax.

Background observer failures do not disable Fund. The worker verifies live
quote and capacity before transferring and permits reconciliation of an
existing deposit. Pending payments block new funding. Pay requires a fresh
card observation and sufficient capacity.

`inventory.py` manages the ignored `demo-stock.json` ledger under a file lock.
`demo-stock.example.json` is a clean seed with fictional products and no orders
or payment receipts. The existing cron runs `scanner.py` every five minutes.
The public shop assets and backend remain active because settled sandbox
purchases update its stock. Its purchases are simulated and charge no money.

## Existing user services

- `scout-storefront.service`: loopback 18140, public role with `/dashboard/`.
- `scout-dashboard.service`: loopback 18141, dashboard role via Tailscale.
- `scout-agent-worker.service`: fixed Fund/Pay/prepare job execution.
- `scout-chat-worker.service`: isolated Pi item discussion.
- `scout-card-observer.timer`: read-only card and chain observations every 15 seconds.

Runtime databases, observations, conversations and logs stay in ignored `data/`.
Do not reset these or the private deposit/payment files to repeat a demo; use
Buy again. Credentials remain outside public projections and source control.

Tests: `python3 -m unittest -q test_scout.py test_chat.py` from this folder.
`verify-chat.cjs` checks live replies and mobile layouts through Tailscale.
Earlier prototypes, adapter experiments, browser fixtures, documentation and
screenshots are preserved under `../archive/scout/`.
