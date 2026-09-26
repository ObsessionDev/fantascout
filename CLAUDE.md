# CLAUDE.md

Guida operativa per agenti che lavorano su questo repo. Deriva da `AGENTS.md`
(che resta la fonte upstream) e aggiunge le convenzioni specifiche del fork.

## Cos'è questo repo

Fork di [`Zannael/fishertiger`](https://github.com/Zannael/fishertiger) —
advisor per asta Fantacalcio Classic. `origin` è il fork
(`ObsessionDev/fantascout`), `upstream` è il progetto originale.
Le decisioni che hanno portato qui sono in `docs/DECISIONI.md`; i requisiti
aggiuntivi rispetto a upstream sono in `docs/REQUISITI.md`; il progetto del
ricalcolo in asta e le alternative misurate e scartate sono in
`docs/ARCHITETTURA_ASTA.md`; il motore di previsione e la sua verifica in
`docs/MOTORE.md`; dove va lo strumento dopo l'asta — formazione di
giornata, scambi, prestiti, asta di riparazione — è in `docs/VISIONE.md`.

## Forma del progetto

- `advisor/` — API Python 3.10+, gestione profilo, pipeline di proiezione,
  simulazione Monte Carlo. `advisor.server` è l'entrypoint HTTP locale.
- `web/` — client Vite/React. Consuma JSON generato dall'API locale e
  scopato per profilo; il browser non esegue la pipeline Python.
- In `web/src`: `main.jsx` shell e routing, `views/` un file per schermata
  (`auction`, `overview`, `players`, `simulation`, `teams`), `ui.jsx`
  primitive presentazionali condivise, `auction-advice.jsx` il vocabolario
  dei consigli usato sia dall'asta sia dal database giocatori, `styles/` il
  design system (`tokens.css` per primo, poi base, primitives, shell e fogli
  per schermata).
- Lo stato persistito nel browser ha un solo proprietario per concern.
  `auction-store.js` è l'unico modulo che legge o scrive l'asta salvata; le
  view leggono via `use-auction-store.js` e mutano tramite lo store, che
  ritorna `{ ok, message }` invece di sollevare eccezioni. `player-notes.js`
  e `player-filters.js` hanno le loro chiavi; `profile-storage.js` elenca
  tutte le chiavi scopate per profilo.
- Interfaccia mobile-first: layout telefono come default, media query
  `min-width` per schermi più grandi. Il colore porta significato: indaco per
  navigazione e focus, verde/ambra/rosso riservati ai verdetti d'asta.

## Setup e run

I comandi girano sul Mac dell'utente, **non** nella VM dell'agente.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd web && npm install
```

```bash
# terminale 1
.venv/bin/python -m advisor.server --host 127.0.0.1 --port 8000
# terminale 2
cd web && npm run dev
```

`VITE_LOCAL_API_BASE` sovrascrive l'URL API del client; default
`http://127.0.0.1:8000`.

## Workflow dati

La generazione precede sempre la simulazione:

```bash
.venv/bin/python -m advisor.pipeline \
  --profile config/profiles/lega-2026-27.json --raw-dir data/raw --output-dir data/processed

.venv/bin/python -m advisor.simulate \
  --profile config/profiles/lega-2026-27.json --raw-dir data/raw --output-dir data/processed \
  --iterations 1000 --seed 202627
```

Costruzione rosa e curva costo/target (legge l'output della pipeline):

```bash
.venv/bin/python -m advisor.optimize \
  --data data/processed/lega-2026-27/2026-27/auction_data.json \
  --profile config/profiles/lega-2026-27.json --k 1
# --owned "Nome:prezzo,..." i miei acquisti | --taken "Nome:prezzo,..." quelli altrui
# --curve per la curva costo/target
```

L'obiettivo del solver è **massimizzare i fantapunti dato il budget**: i
crediti non spesi valgono zero. Il target è un vincolo minimo da verificare,
mai una condizione di arresto — l'unica eccezione è `--curve`, la cui domanda
è appunto quanto costa un punto.

Motore di previsione (`docs/MOTORE.md`): si verifica stagione per stagione e
ogni cambiamento al modello si tiene solo se migliora la misura:

```bash
.venv/bin/python -m advisor.backtest --passi      # verifica, un passo alla volta
.venv/bin/python -m advisor.aggiorna --profile config/profiles/lega-2026-27.json \
  --importa <voti>.csv --giornata N               # una giornata nel modello
```

Il motore legge tutte le stagioni storiche di `data/raw`; dei listoni storici
usa solo `Qt.I` (sono di fine stagione: `Qt.A` e `FVM` conterrebbero il
risultato). I voti per giornata vanno in `data/updates/voti/<stagione>/` (in
`.gitignore`): nessuna fonte si legge in automatico.

In asta il client non esegue il solver: chiama due rotte stateless del server,
`POST /api/auction/plan` (~1.1 s) e `POST /api/auction/bid` (~1.3 s), passando
gli acquisti **per id**. `docs/ARCHITETTURA_ASTA.md` spiega perché non si
precalcola nulla e quali alternative sono state misurate e scartate.

Gli output vanno in `data/processed/<profile_id>/<stagione-con-trattino>/`;
la simulazione si aspetta `auction_data.json` in quella directory.
Le dichiarazioni di sorgente nel profilo attivo sono autoritative per file e
stagioni di input.

## Verifica

```bash
.venv/bin/python -m pytest          # test Python
cd web && npm test                  # solo web/tests/*.test.js
cd web && node --test src/profile-client.test.js   # se tocchi il profile client
cd web && npm run build             # build client
```

Non esistono script di lint o typecheck configurati: non inferirne dai
default del framework.

## Convenzioni del fork

**Nessun numero magico.** Ogni parametro di lega — crediti, slot, scoring,
modificatori, soglia gol virtuali, giornate — deriva dal profilo JSON.
Se un valore serve nel codice e non esiste nel profilo, si estende lo schema
del profilo (`advisor/league_profile.py`) e si aggiorna `MODEL.md`; non si
scrive la costante inline. Vale anche per il frontend: niente soglie
hard-coded nelle view.

**Il target è uno slider, non una costante.** Il target in fantapunti si
deriva da `virtual_goals.threshold + k * virtual_goals.step`. Non scrivere
mai 66, 72 o simili come letterali.

**Profilo di lega separato dal default.** `config/default_profile.json` è
l'unico profilo pubblico committato di upstream: si lascia com'è per non
divergere sui merge. Il profilo reale della lega vive in
`config/profiles/lega-2026-27.json`.

Quel percorso non è arbitrario: la UI elenca i profili leggendo
`config/profiles/`, e il server risolve un profilo come
`profiles_dir / f"{profile_id}.json"`. **Il nome del file deve coincidere con
`profile_id`**, trattini compresi, altrimenti il profilo esiste ma la UI non lo
vede. `config/profiles/` è in `.gitignore` — corretto, perché un profilo salvato
contiene i nomi delle squadre — quindi quel file **non è sotto controllo di
versione**: le regole di lega restano registrate nella tabella in
`docs/DECISIONI.md`, che invece è committata.

**Privacy dei dati.** `data/raw/calendario_lega.xlsx` e ogni altro input che
identifica la lega non vanno committati. I profili salvati, gli upload e i
dataset generati sono già in `.gitignore`.

**Allineamento con upstream.** Preferire modifiche additive (nuovi moduli,
nuovi campi di profilo con default retrocompatibili) alle riscritture dei
file esistenti, così `git merge upstream/main` resta praticabile.

**Documentare le assunzioni del modello.** Ogni cambiamento a pesi, prior o
formule va riflesso in `MODEL.md`, che è la specifica del modello, non un
commento.
