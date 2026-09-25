# fishertiger - A Fantacalcio Auction Advisor

Local-first advisor for a Classic Fantacalcio Serie A auction. It builds player
projections, supports a live auction, replays randomized auctions, and runs a
season-level Monte Carlo simulation using the configured league rules.

> **This is a fork of [`Zannael/fishertiger`](https://github.com/Zannael/fishertiger).**
> Upstream answers "given this roster, what is it worth?"; the fork adds the
> inverse — the cheapest roster that reaches a target, the surplus a player
> offers over his price, and the maximum bid to pay for him while the auction is
> running. Working notes live in Italian under `docs/`: `DECISIONI.md` for why
> this base was chosen, `REQUISITI.md` for what was added and what was measured,
> `ARCHITETTURA_ASTA.md` for the live-auction design and the alternatives that
> were rejected, `VISIONE.md` for where the tool goes after the auction — weekly
> lineups, trades, loans, the January window. `CLAUDE.md` is the agent guide.

## License

- Software: [MIT](LICENSE)
- Structured base data in `data/raw/`: [CC BY 4.0](DATA_LICENSE.md)
- Model choices: [MODEL.md](MODEL.md)
- Input data and private calendar: [DATA_SOURCES.md](DATA_SOURCES.md)

## Requirements

- Python 3.10+
- Node.js 22+

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm install
```

## Run Locally

Start the local API from the repository root:

```bash
.venv/bin/python -m advisor.server --host 127.0.0.1 --port 8000
```

In another terminal, start Vite:

```bash
cd web
npm run dev
```

Open the Vite URL. On first launch the application opens **Impostazioni**.
The included sources are enough to use **Genera dati** for the dashboard,
projections, and auction tools. Upload a compatible private
`calendario_lega.xlsx` and regenerate the dataset only before running the
season simulation. Generated datasets and simulations stay local under
`data/processed/<profile_id>/<season>/`.

## Inputs And Profiles

`config/default_profile.json` is the single public default profile. The API
serves it to the client; there is no duplicate browser profile. The fork leaves
it untouched so upstream merges stay clean and keeps its own league in
`config/profiles/lega-2026-27.json`; use that one in every command below to work
against real league rules.

That location is what the UI reads: profiles are listed from `config/profiles/`
and resolved as `profiles_dir / "<profile_id>.json"`, so **the filename must
match the profile's own `profile_id`**, hyphens included, or the profile exists
without ever appearing in the picker. `config/profiles/` is gitignored, since a
saved profile carries team names, so back that file up outside the repository.

Every league parameter comes from the profile — credits, slots, scoring,
modifiers, the virtual-goal threshold and step, the matchday window. Nothing is
written into the code. The target in fantasy points is always
`virtual_goals.threshold + k * virtual_goals.step`, never a literal.

The repository includes base inputs in `data/raw/`. The only excluded input is
`data/raw/calendario_lega.xlsx`, because it identifies a user's fantasy league.
It is optional for generation and required for season simulation. Download the
sanitized model from **Impostazioni** when needed. The profile source
declarations identify the expected files and seasons.

The Serie A input is always a 20-team, 38-matchday, 380-match calendar. The
fantasy league can use a shorter configured interval through
`fantasy_start_matchday`, `fantasy_end_matchday`, and `fantasy_matchdays`.

## CLI

The UI is the normal workflow. The pipeline command works with the included
sources; supply a matching private calendar before the simulation command:

```bash
.venv/bin/python -m advisor.pipeline --profile config/default_profile.json --raw-dir data/raw --output-dir data/processed
.venv/bin/python -m advisor.simulate --profile config/default_profile.json --raw-dir data/raw --output-dir data/processed --iterations 1000 --seed 202627
```

## Roster Construction (fork)

`advisor.optimize` reads the pipeline output and answers the auction's own
questions. Unspent credits score nothing, so it maximises expected points inside
the budget; the target is a floor it reports against, never a stopping rule.

```bash
DATA=data/processed/lega-2026-27/2026-27/auction_data.json

# best use of the credits available, with k=1 as the minimum target
.venv/bin/python -m advisor.optimize --data $DATA --profile config/profiles/lega-2026-27.json --k 1

# what a point costs: the one question where minimising spend is the point
.venv/bin/python -m advisor.optimize --data $DATA --profile config/profiles/lega-2026-27.json --curve

# during the auction: my purchases, and everyone else's
.venv/bin/python -m advisor.optimize --data $DATA --profile config/profiles/lega-2026-27.json \
  --owned "Berardi:60,Mandragora:12" --taken "Martinez L.:180,Hojlund:150"

# a player has just been called: his exact ceiling, in about a second
.venv/bin/python -m advisor.optimize --data $DATA --profile config/profiles/lega-2026-27.json \
  --player "Malen" --owned "Berardi:60"
```

Recording what other managers buy is not bookkeeping: their players leave the
listing **and** their credits leave the market, so expected prices are re-derived
from the credits and slots the league still has to fill.

`--owned` and `--taken` take the price actually paid. Names must identify exactly
one player and an ambiguous one is refused rather than guessed — eighteen
surnames are shared in the listing (`Martinez Jo.` and `Martinez L.`,
`Terracciano` and `Terracciano F.`). Pass `#id:price` to be certain.

## Auction API (fork)

The client does not run the solver. Two stateless routes score the board the
browser owns, with players identified by id:

| Route | Answers | Cost |
|---|---|---|
| `POST /api/auction/plan` | best use of the remaining credits, target status, open slots per role | ~1.1 s |
| `POST /api/auction/bid` | exact maximum bid for the player under the hammer | ~1.2 s |

```bash
curl -X POST http://127.0.0.1:8000/api/auction/bid -H 'Content-Type: application/json' \
  -d '{"profile_id":"lega-2026-27","playerId":5585,
       "owned":[{"playerId":2170,"price":12}],
       "taken":[{"playerId":5841,"price":90}]}'
```

Nothing is precomputed and nothing goes stale: pricing the single player just
called is far cheaper than pricing a whole role in advance.
`docs/ARCHITETTURA_ASTA.md` has the measurements behind that choice.

## Verification

```bash
.venv/bin/python -m pytest
cd web && npm test && npm run build
```
