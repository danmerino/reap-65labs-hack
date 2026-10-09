# Scout agent dashboard

Live dashboard: https://sshi.tail4d8e5a.ts.net:8491/dashboard/

Three agent tabs show inventory, per-agent chat and chronological activity.
Europe has a live Kwal sandbox card; US and Australia are previews. Fund covers
only the verified quote shortfall. Pay purchases a fixed catalog substitute;
after verified settlement, Buy again prepares another cycle. No goods ship.
Each settled payment adds one fictional watch to the linked demo storefront.

## Active files

- `scout/`: dashboard, storefront, inventory scanner, job/event ledger and chat.
- `card-funding-test/`: Kwal adapter, financial worker, card observer and tests.
- `durable-demo/`: shared Pi model integration and Scout dialogue/chat helpers.
- `ink_wallet.py`, `requirements.lock`: sandbox wallet and pinned Python dependencies.
- `tools/`: optional existing tunnel runtime metadata; local binary is ignored.

Earlier API illustrations, Prolog hypotheses, push test, standalone Pi demo,
sourcing prototypes, historical evidence and screenshots live in `archive/`.
The archive is ignored by Git. Live databases, wallet keys, installed runtimes
and mutable demo orders are also ignored. No wallet or provider credential is
included in the source tree intended for commit.

## Local setup

Use the existing Python environment or install the pinned dependencies:

```bash
uv venv --python python3 .venv
uv pip install --python .venv/bin/python --require-hashes --only-binary :all: -r requirements.lock
cp scout/demo-stock.example.json scout/demo-stock.json
python3 scout/server.py --role dashboard --port 18141
```

Copy the stock example only for a fresh checkout; preserve existing stock and
ledgers on this host. Node 22 and the dependencies pinned in
`durable-demo/package-lock.json` are required for Pi. Existing local model auth
is read only by `models.mjs`; sandbox provider credentials remain in
`/var/tmp/dan-reap-kwal-test`, and the wallet stays in ignored `.wallet/`.

## Verification

```bash
(cd scout && python3 -m unittest -q test_scout.py test_chat.py)
(cd card-funding-test && ../.venv/bin/python -m unittest -q test_app.py test_workflow.py test_export_status.py)
```

`scout/verify-chat.cjs` exercises real model conversations via Tailscale without
funding or purchasing. It needs the host's existing Playwright installation.
Historical browser fixtures and screenshots are under `archive/scout/`.
See `scout/README.md` for service and behavior details.
