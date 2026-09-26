# Data Sources

The project ships structured, versioned input files in `data/raw/` so a fresh
clone can generate a dataset locally. The files cover the player list, Serie A
calendar, historical statistics, club priors, likely starters, set-piece order,
and auction tiers.

The private fantasy-league calendar is deliberately not committed. It is
optional for generating dashboard, projection, and auction data, but required
to simulate a season. Upload it from **Impostazioni**, then regenerate the
dataset before simulation. Its teams and matchday count are checked against the
profile during generation.

## League calendar

Download the sanitized workbook model from **Impostazioni**. The importer reads
the legacy Leghe Fantacalcio layout, not an arbitrary spreadsheet:

- Use an `.xlsx` workbook with a worksheet named exactly `Calendario`.
- Keep the two fixture blocks in columns A:D and G:J. The other columns may be
  used for display or score values and are ignored.
- Begin each block with `Nª Giornata lega` in column A or G and `Mª Giornata
  serie a` in column C or I.
- Put each home team in A and away team in D for the left block; use G and J
  for the right block. Fixture rows continue until the next header.
- Blank rows are allowed. Each fixture needs two distinct teams, and a team
  cannot play twice in one matchday.

For example:

```text
A: 1ª Giornata lega       C: 1ª Giornata serie a
A: Squadra 1              D: Squadra 2
A: Squadra 3              D: Squadra 4

G: 2ª Giornata lega       I: 2ª Giornata serie a
G: Squadra 1              J: Squadra 3
G: Squadra 2              J: Squadra 4
```

Generation requires consecutive league matchdays starting at 1 and exactly the
configured number of fantasy matchdays. The team names must match the profile;
the API adopts valid uploaded calendar names as the profile participants when
the data is generated.

The application treats the files as input data, not as a remote scraping layer.
If you replace them for another season, update the profile source declarations
and retain attribution required by the data license.

## Goalkeeper hierarchy

`titolari.csv` carries `gerarchia_portiere` for active goalkeepers. Canonical
values are `PRIMO`, `SECONDO`, `TERZO`, and contiguous slash-separated contests
such as `PRIMO/SECONDO` or `SECONDO/TERZO`. Generation rejects unknown,
non-contiguous, unresolved, duplicate, or outfield assignments and preserves
the hierarchy in `auction_data.json`.

The source note remains the evidence and observation context. Missing ranks are
left unknown rather than inferred from FVM, names, or absence from an article.

## Historical seasons for the projection engine

`advisor.engine` trains on every `statistiche_YYYY_YY.xlsx` and
`listone_YYYY_YY.xlsx` pair it finds in `data/raw`, not only on the history
declared in the profile. The historical lists were saved at the end of their
season: only the opening price `Qt.I` is used from them, since `Qt.A` and
`FVM` would carry the outcome. See `docs/MOTORE.md`.

## Matchday votes (season in progress)

The model folds in every matchday played (`advisor.inseason`). It accepts
either of two inputs:

1. **Matchday files**, one per matchday, in
   `data/updates/voti/<season>/giornata_NN.csv` (git-ignored). Columns:

   ```text
   giornata, id_fantacalcio, nome, squadra, ruolo, voto, gol, assist,
   ammonizioni, espulsioni, autogol, gol_subiti, rigori_parati,
   rigori_sbagliati, rigori_segnati
   ```

   One row per player who took the field; a blank `voto` is a player without
   a vote (s.v.); a player not in the file did not play. `id_fantacalcio` is
   the `Id` of the player list. `python -m advisor.aggiorna --profile ...
   --importa <file> --giornata N` validates and writes them, from this CSV or
   from a workbook laid out like the Fantacalcio "voti" download (club blocks
   with a `Cod. / Ruolo / Nome / Voto / Gf / Gs / Rp / Rs / Rf / Au / Amm /
   Esp / Ass` header). That layout is written from its public description and
   has not been checked against a real file yet.
2. **Season-to-date statistics**: `data/raw/statistiche_<season>.xlsx`, the
   same format as the historical files, downloaded during the season. Totals
   are sufficient for the update; the matchday-by-matchday check and recency
   weighting need the matchday files.

No source is read automatically. Which one to use is the user's decision; the
candidates below were checked on 26/09/2026 against their published terms.

| Source | What it has | Terms, as read on 26/09/2026 | Fit |
|---|---|---|---|
| Fantacalcio.it ("voti" and statistics downloads) | Official Classic votes and events per matchday; season statistics in the historical format | Terms of use (Sept. 2026, art. 3 and 8): no software or automatic means to copy or access pages, no copying, reproducing or processing content without written consent; personal viewing and copies on personal devices allowed. [Terms](https://www.fantacalcio.it/termini-e-condizioni), [iubenda terms](https://www.iubenda.com/termini-e-condizioni/7893975) | The right numbers, and the source the league scores with. Only a manual download for personal use is compatible; no scraping. Whether processing a downloaded file locally is covered is for the user to judge, or to ask them in writing. |
| Fantacalcio-Online (F.C.O. Srl) | Official Fantacalcio.it votes per matchday, Excel exports inside a league | Terms (15/06/2023): no copying, downloading, processing or derivative works beyond the stated limits; download only where expressly offered, for personal non-commercial use; no bots. [Terms](https://www.fantacalcio-online.com/it/termini-e-condizioni) | Same situation as Fantacalcio.it. |
| The league's own app (Leghe Fantacalcio) | The votes the league actually scores with | Same owner and terms as Fantacalcio.it | Manual export for personal use only. |
| openfootball / football.json | Serie A fixtures and results, no player data | Public domain (CC0). [Repository](https://github.com/openfootball/football.json) | Free to use and redistribute; useful for team strength match by match (Dixon-Coles), not for votes. |
| football-data.co.uk | Serie A results, shots, odds; no player data | No license statement on the data notes page. [Notes](https://www.football-data.co.uk/notes.txt) | Results only; terms unclear. |
| FBref (Sports Reference) | Player match stats (minutes, starts), no Italian fantasy votes | Terms forbid automated access that affects the site; bots limited to 10 requests per minute; no copying a significant part or building a competing database. [Terms](https://static.fbref.com/termsofuse.html), [bot policy](https://www.sports-reference.com/bot-traffic.html) | Minutes and starts, useful for presence; only light, rate-limited personal use. |
| API-Football (api-sports.io) | Player match statistics and ratings (not Italian fantasy votes) | Commercial API with a key: free tier around 100 requests per day, paid plans from about 19 USD per month (third-party summaries; check the provider's page before subscribing) | Licensed and automatable, but a new account and not the league's votes. |

**Recommendation.** The simplest compliant path is a manual download, by the
user, for personal use, of either the matchday votes or the season-to-date
statistics from the source the league scores with, then
`python -m advisor.aggiorna`. Nothing here scrapes a site.

**Note on the committed historical files.** `DATA_LICENSE.md` (from upstream)
places `data/raw/` under CC BY 4.0. The player lists and statistics carry the
columns of the Fantacalcio.it downloads (`Qt.A M`, `FVM M`, `Fm`), and those
terms forbid reproducing content without written consent. Whether committing
them to a public repository is allowed is a question for the user; nothing
has been changed.
