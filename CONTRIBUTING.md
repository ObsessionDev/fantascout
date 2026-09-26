# Contributing

Thanks for helping improve Fishertiger.

## Workflow

- Fork the repository and create a focused feature branch from the latest `main`.
- Keep commits small and limited to one logical change.
- Preserve existing behavior unless the pull request explicitly changes it.
- Open a pull request with a clear summary, testing notes, and screenshots for user-facing changes.
- Do not commit generated datasets or private league data.

## Setup

Fishertiger requires Python 3.10+ and Node.js 22+.

From the repository root, install the Python dependencies in a virtual environment:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Install the web dependencies:

```bash
cd web
npm install
```

## Run Locally

Start the API from the repository root:

```bash
.venv/bin/python -m advisor.server --host 127.0.0.1 --port 8441
```

In another terminal, start the web client:

```bash
cd web
npm run dev
```

The normal UI workflow starts in **Impostazioni**. Upload the private league
calendar and verify the configured sources before generating a dataset.

## Validation Before a Pull Request

Run the Python test suite from the repository root:

```bash
.venv/bin/python -m pytest
```

Run the web tests and production build:

```bash
cd web
npm test
node --test src/profile-client.test.js
npm run build
```

The profile client test is separate from `npm test`, which only runs files under
`web/tests/`.

When changing deployment behavior, also rebuild and inspect the local stack:

```bash
cd deploy
docker compose down
docker compose up -d --build
docker compose ps
docker compose logs api
docker compose logs web
```

There are no configured lint or type-check scripts. Do not report unconfigured
checks as having run.

## Frontend Changes

- Keep the interface mobile-first; add larger layouts with `min-width` media queries.
- Keep shared design tokens and styles under `web/src/styles/`.
- Keep screen components under `web/src/views/` and shared primitives in `web/src/ui.jsx`.
- Preserve the established color semantics: indigo for navigation and focus, and green, amber, or red for auction verdicts.
- Keep persisted browser state behind its owning module; do not call `localStorage` from a view. Auction state goes through `web/src/auction-store.js`, and any new profile-scoped key must be registered in `web/src/profile-storage.js` so deleting a profile stays complete.
- Verify user-facing changes on both mobile and desktop viewport sizes.

## Data, Profiles, and Privacy

- Do not commit `data/raw/calendario_lega.*`; it can identify a private league.
- Do not commit files under `data/processed/`, `data/uploads/`, or `config/profiles/`.
- Do not commit `.env` files, logs, local build output, or virtual environments.
- `config/default_profile.json` is the only public profile committed to the repository.
- Sanitize team names, participant names, paths, and other identifying information in issues, fixtures, screenshots, and logs.
- Keep generated JSON and CSV files UTF-8 encoded.

## Data Pipeline

Generation must run before simulation. After supplying compatible local source
files, the command-line workflow is:

```bash
.venv/bin/python -m advisor.pipeline --profile config/default_profile.json --raw-dir data/raw --output-dir data/processed
.venv/bin/python -m advisor.simulate --profile config/default_profile.json --raw-dir data/raw --output-dir data/processed --iterations 1000 --seed 202627
```

Source declarations in the active profile are authoritative. Do not add fallback
paths or bundled private inputs to make a local setup pass.
