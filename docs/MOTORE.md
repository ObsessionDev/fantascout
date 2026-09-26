# Motore di previsione

Revisione del 26/09/2026. Qui c'è come è fatto il motore, come è stato
misurato, cosa è cambiato rispetto a prima e cosa resta. La specifica delle
formule sta in `MODEL.md`; questo documento racconta le scelte e i numeri.

## In una frase

Il motore guarda le stagioni passate di ogni giocatore, le avvicina alla media
del suo ruolo quanto più sono poche, e poi corregge con quello che le stagioni
passate hanno insegnato su prezzo iniziale e squadra; durante la stagione ogni
giornata giocata aggiorna la previsione con una regola bayesiana esatta.

## Dati: cosa c'è e cosa no

- `data/raw/statistiche_*.xlsx` (2015/16 → 2025/26): **solo totali di
  stagione** per giocatore (`Pv`, `Mv`, `Fm`, gol, assist, cartellini, gol
  subiti, rigori). Niente voti per giornata, minuti o titolari storici.
- `data/raw/listone_*.xlsx`: scaricati **a fine stagione**. `Qt.A` e `FVM`
  sono valori finali e conterrebbero il risultato; `FVM` esiste solo dal
  2022/23. L'unico segnale di mercato disponibile a inizio stagione è `Qt.I`,
  ed è l'unico che il motore usa.
- Una stagione è `Tutti` più `Ceduti` del suo listone: chi è stato venduto a
  gennaio c'era ad agosto. La squadra è quella di fine stagione: piccola fuga
  di informazione per chi ha cambiato squadra a gennaio, uguale per tutti i
  modelli confrontati.
- `titolari.csv`, `piazzati.csv` e `squadre.csv` esistono solo per il 2026/27:
  lo status editoriale (titolare/ballottaggio/riserva), il bonus rigorista e lo
  sconto per le coppe europee **non si possono verificare** e restano come
  prima, dichiarati non misurati.

## Verifica (`python -m advisor.backtest`)

Stagione per stagione: per prevedere la stagione T il modello vede solo le
statistiche delle stagioni prima di T e il listone di T con `Qt.I`, cioè
quello che si sa all'asta. Il risultato è la stagione T.

- **Stagioni di taratura:** 2018/19, 2019/20, 2020/21. Gli iperparametri si
  sono scelti solo su queste.
- **Stagioni di test:** 2021/22 → 2025/26, mai usate per scegliere nulla.
- **Gruppi:** tutti, *nuovi* (nessuna presenza a voto in Serie A nelle
  stagioni caricate), *con storico*.

Metriche:

| Metrica | Cosa misura |
|---|---|
| logloss / Brier presenza | probabilità di prendere voto in una giornata, contro `Pv/38` (binomiale) |
| calibrazione presenza | scarto medio fra probabilità prevista e frequenza osservata, per decili |
| MAE / RMSE / bias fv | fantavoto per presenza (`Fm`), pesato per le presenze |
| MAE punti | punti di stagione, `38 · p · fv` contro `Pv · Fm` |
| Spearman | ordine per ruolo dei giocatori che un'asta prezza (i primi `2 · 8 · slot` per `Qt.I`) |
| top-K | punti veri dei K scelti dal modello sui punti dei K migliori, K = `8 · slot` |
| vantaggio | Spearman fra i residui sul prezzo previsti e veri: quanto il modello vede **oltre** il listino |
| copertura 80/50 | quota di stagioni vere dentro l'intervallo previsto all'80% e al 50% |

`python -m advisor.backtest --passi` rifà la tabella qui sotto; `--json`
scrive le metriche per stagione.

Il fantavoto verificato è `Fm` dei file, calcolato con il punteggio standard
(rigori ±3 compresi, niente porta inviolata): la verifica usa quella
definizione, qualunque sia il regolamento della lega.

## Prima e dopo (stagioni di test, tutti i giocatori)

| | logloss presenza | calibrazione | MAE fantavoto | MAE punti | Spearman | top-K | vantaggio |
|---|---|---|---|---|---|---|---|
| prima (base) | 0.661 | 0.096 | 0.379 | 57.3 | 0.340 | 0.799 | 0.167 |
| dopo (motore) | **0.585** | **0.039** | **0.304** | **48.7** | **0.380** | **0.821** | **0.196** |

Sui **giocatori nuovi** (il problema 1): logloss presenza 0.606 → 0.519,
MAE punti 58.4 → 42.5, Spearman 0.333 → 0.558, vantaggio −0.027 → 0.127.
Prima, per i nuovi, il modello faceva peggio del listino; adesso vede qualcosa
oltre il listino.

Il miglioramento vale in **ognuna** delle otto stagioni misurate, non solo in
media (tabella per stagione in fondo).

Incertezza: l'intervallo all'80% del fantavoto di stagione contiene il valore
vero l'84% delle volte, quello al 50% il 54%; per le presenze l'80% contiene
l'80%. È calibrata, leggermente prudente sul fantavoto.

## Un cambiamento alla volta

Ogni passo aggiunge al precedente. Colonne: logloss presenza, MAE fantavoto,
MAE punti, Spearman, vantaggio, sulle stagioni di test.

| passo | logloss | MAE fv | MAE punti | Spearman | vantaggio | tenuto? |
|---|---|---|---|---|---|---|
| 0 base | 0.661 | 0.379 | 57.26 | 0.340 | 0.167 | — |
| 1 tassi per presenza a voto | 0.661 | 0.346 | 57.03 | 0.340 | 0.169 | sì |
| 2 rigori parati/sbagliati nel bonus | 0.661 | 0.345 | 57.00 | 0.340 | 0.167 | sì |
| 3 storico ristretto verso il ruolo | 0.624 | 0.319 | 58.26 | 0.340 | 0.189 | sì |
| 4 avvio a freddo appreso | 0.599 | 0.312 | 52.00 | 0.341 | 0.158 | sì |
| 5 prezzo iniziale per tutti | 0.587 | 0.306 | 49.11 | 0.370 | 0.179 | sì |
| 6 forza della squadra | 0.586 | 0.304 | 48.78 | 0.372 | 0.179 | sì |
| 7 presenza della stagione scorsa | 0.585 | 0.304 | 48.67 | 0.380 | 0.196 | sì |
| cambio di squadra | 0.585 | 0.304 | 48.66 | 0.381 | 0.197 | no |
| interazione prezzo × storico | 0.585 | 0.304 | 48.66 | 0.382 | 0.197 | no |
| alberi (gradient boosting) | 0.588 | 0.316 | 48.99 | 0.368 | 0.184 | no |

- **Passo 1 — un errore vero.** Il vecchio modello divideva i tassi di evento
  per 75/90, cioè li trattava come tassi "per 90 minuti", ma poi li usava come
  eventi *per presenza a voto*, che è come il fantavoto li conta. Risultato:
  bonus e malus gonfiati del 20%, attaccanti e portieri spostati in direzioni
  opposte. La sola correzione toglie 0.03 di errore sul fantavoto.
- **Passo 3** sostituisce i pesi fissi 60/30/10 sulle ultime tre stagioni con
  un decadimento per stagione (0.45) su fino a sei stagioni, più un
  restringimento verso la media del ruolo che pesa quanto 12 presenze (voto),
  20 presenze (eventi), una stagione (presenza). Da solo peggiora i punti dei
  nuovi (li mette tutti alla media del ruolo): il passo 4 lo risolve.
- **Passo 4 — avvio a freddo.** Per chi non ha storico il motore usa un
  modello per ruolo appreso sui *nuovi delle stagioni passate*: con quel
  prezzo iniziale, in quella squadra (o in una neopromossa), quanto hanno
  giocato e reso. Nessun numero è scritto a mano.
- **Passo 5** dà lo stesso modello a tutti: il prezzo iniziale contiene
  informazioni che lo storico non ha (un nuovo ruolo, un infortunio d'estate).
  È il passo che migliora di più i giocatori con storico.
- **Passi 6 e 7:** gol fatti e subiti per giornata della squadra la stagione
  prima (le neopromosse prendono la media delle neopromosse passate), e la
  presenza dell'ultima stagione separata dal resto dello storico.
- **Scartati:** cambio di squadra e interazione prezzo × storico non muovono
  nulla oltre il rumore; gli alberi (scikit-learn, stesse variabili, prova in
  un ambiente separato e non in `requirements.txt`) perdono su tutte le
  metriche. Con circa 5.000 righe e dieci variabili il modello statistico
  basta.

Taratura (sulle sole stagioni 2018-2021): decadimento 0.3-1.0, prior di voto
4-35 presenze, prior di eventi 8-50, prior di presenza 0.2-2 stagioni,
regolarizzazione 0.1-30. Le metriche variano di pochi millesimi: la scelta è
stabile e non dipende da un valore fortunato.

## Architettura

```
data/raw/statistiche_*.xlsx ─┐
data/raw/listone_*.xlsx ─────┼─ advisor.history ── advisor.engine ─── advisor.projection ── advisor.pipeline
                             │   (tutte le stagioni) (stima e modelli)   (stagione corrente)     (auction_data.json)
data/updates/voti/<stag>/ ───┴──────────────────── advisor.inseason ──┘
data/raw/statistiche_<stag corrente>.xlsx ────────  (aggiornamento bayesiano)
```

- `advisor/history.py` carica tutte le stagioni che trova in `data/raw`.
- `advisor/engine.py` il motore: riassunto dello storico con decadimento,
  restringimento, modelli per ruolo (logistico per la presenza, minimi
  quadrati pesati per il voto, Poisson con esposizione per ogni evento), e
  dispersione dei residui.
- `advisor/projection.py` addestra il motore sulle stagioni prima di quella
  del profilo e proietta il listone corrente; se ci sono giornate giocate le
  assorbe. Se lo storico ha meno di tre stagioni con listone e statistiche,
  la pipeline usa il calcolo di prima (`ModelConfig.engine = "legacy"` lo
  forza).
- `advisor/inseason.py` formato dei voti per giornata, importazione,
  somma, aggiornamento coniugato e verifica giornata per giornata.
- `advisor/aggiorna.py` il comando per importare una giornata e rigenerare.
- `advisor/backtest.py` la verifica, con la replica fedele del modello di prima
  (un test controlla che coincida con le funzioni della pipeline).

Cosa cambia nel dataset (`model_version` 2.0): stessi campi di prima, con
`proiezione.fonte_rate` che vale `storico` o `avvio_freddo`, e in più
`proiezione.incertezza`, `proiezione.giornate_osservate` e
`proiezione.presenze_osservate`; `event_rates` aggiunge `rigori_parati` e
`rigori_sbagliati`. Asta, dashboard, formazione (`advisor.lineup`),
ottimizzatore ed esportazione della rosa leggono gli stessi campi di prima.

## Le giornate nuove entrano da sole

Il problema 2. Durante la stagione ogni giornata giocata aggiorna la
previsione:

- **presenza:** prior beta con la concentrazione misurata (circa 2 stagioni
  di peso) più le giornate in cui il giocatore ha preso voto su quelle giocate
  dalla sua squadra;
- **voto:** prior normale che pesa `sigma² / tau²` giornate (dispersione della
  singola giornata sulla dispersione del livello vero, stimate dai residui:
  circa 140 giornate per un portiere (il suo voto si muove pochissimo), 33 per
  un difensore, 9 per un
  centrocampista, 8 per un attaccante) più i voti presi;
- **eventi:** prior gamma con la varianza misurata più gli eventi registrati.

Due modi di far entrare i dati, basta uno:

1. **File per giornata** in `data/updates/voti/<stagione>/giornata_NN.csv`,
   formato canonico descritto in `advisor/inseason.py` e in
   `DATA_SOURCES.md`. `python -m advisor.aggiorna --profile ... --importa
   file --giornata N` li scrive e rigenera.
2. **Statistiche della stagione in corso**: lo stesso file dei totali storici,
   `data/raw/statistiche_2026_27.xlsx`, scaricato a stagione iniziata. I
   totali bastano all'aggiornamento; si perdono solo la verifica giornata per
   giornata e il peso alle giornate recenti.

L'impronta dei file di giornata entra nella freschezza del dataset: quando ne
arriva uno nuovo la dashboard dice "fonti cambiate" e si rigenera dal pulsante
di sempre. La pipeline li legge da sola a ogni generazione.

**Da decidere per Mattia: la fonte dei voti per giornata.** Le opzioni, con i
termini d'uso letti, sono in `DATA_SOURCES.md`.

## Simulazione

Il Monte Carlo trattava ogni giornata come indipendente: la stagione di un
giocatore non poteva andare davvero bene o davvero male, e la dispersione dei
punteggi di stagione era troppo stretta. Ora in ogni iterazione si estraggono
una volta il livello di voto, il livello di bonus e la quota di presenze del
giocatore, con le dispersioni misurate e verificate sopra; l'undici si sceglie
sapendo chi ha perso il posto. Rigori e porta inviolata entrano nel fantavoto
se il profilo li prevede. Un dataset senza `proiezione.incertezza` si comporta
esattamente come prima.

## Cosa resta

- **Verifica giornata per giornata.** `inseason.matchday_backtest` è pronto e
  testato su dati sintetici; gira con `python -m advisor.aggiorna --verifica`
  appena ci sono file per giornata. Solo allora si possono misurare la forma
  recente (`half_life`, oggi spenta), il peso dello status editoriale e il
  termine di calendario per avversario, che oggi restano come prima e non
  misurati.
- **Status editoriale, rigorista, coppe europee:** manca lo storico, restano
  come prima. Lo status continua a pesare il 65% della presenza, che è molto
  per un segnale non verificato: il primo candidato da misurare quando ci
  saranno `titolari.csv` di più stagioni.
- **Forza delle squadre partita per partita** (Dixon-Coles o Elo): servono i
  risultati delle partite storiche. openfootball li offre in pubblico dominio
  (`DATA_SOURCES.md`); non scaricati, perché è una fonte nuova da approvare.
- **Lega di provenienza, età:** non ci sono nei dati. Il prezzo iniziale ne
  assorbe una parte.
- **Porta inviolata:** modellata come `exp(-gol subiti per presenza)`, non
  verificabile con i totali di stagione.
- **Regolamento della lega:** il profilo privato non ha ancora i valori di
  rigore parato (+3), rigore sbagliato (−3) e porta inviolata (+1) che
  `REQUISITI.md` R6 attribuisce alla lega; finché valgono 0 non contano.

## Stato del lavoro

Completato il 26/09/2026: verifica, motore, integrazione nella pipeline,
aggiornamento in stagione, simulazione, documentazione. Resta aperta solo la
scelta della fonte dei voti per giornata.

## Per stagione (tutti i giocatori)

| stagione | logloss base | logloss motore | MAE fv base | MAE fv motore | MAE punti base | MAE punti motore | Spearman base | Spearman motore |
|---|---|---|---|---|---|---|---|---|
| 2018-19 | 0.648 | 0.567 | 0.428 | 0.354 | 55.9 | 46.7 | 0.453 | 0.446 |
| 2019-20 | 0.635 | 0.532 | 0.414 | 0.362 | 54.3 | 41.7 | 0.402 | 0.485 |
| 2020-21 | 0.615 | 0.552 | 0.398 | 0.327 | 52.0 | 44.6 | 0.482 | 0.565 |
| 2021-22 | 0.651 | 0.578 | 0.381 | 0.311 | 56.5 | 48.1 | 0.371 | 0.412 |
| 2022-23 | 0.645 | 0.571 | 0.414 | 0.323 | 56.1 | 46.1 | 0.342 | 0.385 |
| 2023-24 | 0.678 | 0.593 | 0.385 | 0.312 | 58.8 | 49.8 | 0.309 | 0.340 |
| 2024-25 | 0.665 | 0.587 | 0.345 | 0.283 | 57.7 | 49.3 | 0.327 | 0.393 |
| 2025-26 | 0.667 | 0.596 | 0.370 | 0.291 | 57.3 | 50.1 | 0.353 | 0.370 |

Unica eccezione: lo Spearman del 2018/19, di poco sotto (0.446 contro 0.453).
