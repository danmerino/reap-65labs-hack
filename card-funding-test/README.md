# Scout sandbox payment adapter

`app.py` and `vendor/kwal/` provide the fixed sandbox adapter. `workflow_worker.py`
executes operator-requested funding and purchases for the dashboard;
`export_status.py` observes card capacity, saved payments and vault transfers.
The dedicated owner key stays in `../.wallet/`; provider sessions and durable
funding/payment records stay in `/var/tmp/dan-reap-kwal-test`.

Each purchase cycle has its own deposit filename and Pi request IDs. Retries
retain that cycle's quote/payment identities. New cycles require verified
settlement, then the dashboard's Buy again action. Chat has no financial tools.
The sandbox quote is capped at 10 test USDC; the fixed sticker-pack stand-in
has been quoted at 3.96. Actual physical orders are not placed.

Run isolated checks from this directory:

```bash
../.venv/bin/python -m unittest -q test_app.py test_workflow.py test_export_status.py
```

Historical provider research, funding snapshots and older walkthroughs live in
`../archive/card-funding-test/`. The vendored adapter's provenance is recorded
in `vendor/kwal/SOURCE.json`. Preserve runtime receipts when updating source.
