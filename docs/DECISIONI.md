# Decisioni

Registro delle decisioni prese e del perché. Serve a non rifare l'istruttoria.

## Contesto

- Asta Fantacalcio Classic, 8 squadre, 500 crediti, rosa 3P / 8D / 8C / 6A.
- L'utente è uno sviluppatore, non segue la Serie A e non conosce i giocatori:
  l'approccio è moneyball puro, ogni decisione deve reggersi sui numeri.
- Data: 3 settembre 2026. La Serie A 2026/27 ha già giocato 2 giornate, ma la
  lega parte dalla terza. Le proiezioni sono quindi **storico + prior di
  titolarità**, non forma corrente: nessun segnale delle prime 2 giornate
  entra nel modello.

## Base di codice: fork di `Zannael/fishertiger`

**Scelto.** Clonato in `~/Dev/fantascout`, `upstream` configurato.

Motivi:

- UI React/Vite già pronta, 5 viste (`auction`, `overview`, `players`,
  `simulation`, `teams`).
- 11 stagioni di listone + statistiche committate in `data/raw`
  (2015/16 → 2026/27): zero scraping, zero credenziali, zero login.
- Monte Carlo con seed riproducibile.
- `MODEL.md` documenta il modello in modo esplicito.
- Licenze compatibili: MIT sul codice, CC BY 4.0 sui dati.
- `config/default_profile.json` è già 8 squadre e 3/8/8/6 esatti. Le uniche
  modifiche necessarie sono i crediti (750 → 500), il modificatore difesa,
  i cambi e il passo dei gol virtuali.

## Base di codice scartata: `SilvioBaratto/fantabot`

**Scartato come base, tenuto come miniera.**

Ottima ingegneria: 348 commit, 4 layer con vincolo di dipendenza testato.
Ma non serve a questo scopo:

- È un agente autonomo che fa rilanci in autonomia — noi vogliamo un advisor
  che informi una decisione umana in tempo reale.
- Zero UI.
- Richiede conda + Docker + Postgres + Alembic + Playwright, login alla lega e
  scraping di 4 stagioni, perché `data/` è in `.gitignore`. Costo di setup
  incompatibile con un'asta la stessa sera.

Da riprendere in futuro per il suo DB di scraping e la griglia Mantra.

## Modello ereditato (da `MODEL.md`)

Assunzioni che accettiamo finché non abbiamo motivo di cambiarle:

- Pesi storici 60% / 30% / 10% dalla stagione più recente alla più vecchia.
- Prior di titolarità: `TITOLARE` 85%, `BALLOTTAGGIO` 55%, `RISERVA` 15%,
  mixati 65% prior / 35% storico quando lo storico esiste.
- Rate degli eventi normalizzati a una presenza "piena" di 75 minuti.
- Rigoristi principali: +0.12 xG/90.
- Le coppe europee applicano uno sconto di rotazione ai giocatori di movimento.
- Il FVM sorgente è conservato come `fvm_original` — è quello che rende
  possibile il surplus per credito (vedi `REQUISITI.md`, R1).
- Solo un segnale esplicito `confirmed_inactive` rende un giocatore
  ineleggibile; `RISERVA` e simili riducono l'utilità, non escludono.

## Regolamento della lega

Confermato con l'utente il 3 settembre 2026:

| Parametro | Valore | Effetto |
|---|---|---|
| Crediti | 500 (default repo: 750) | tutta la scala prezzi va riscalata |
| Squadre | 8 | come il default |
| Rosa | 3P / 8D / 8C / 6A = 25 | come il default |
| Modificatore difesa | **disattivato** | i difensori perdono gran parte del loro valore rispetto al default del repo, che lo ha attivo |
| Gol virtuali | soglia 66, passo **6** (default repo: 5) | target = 66 + k·6; ~72 FP ≈ 2 gol |
| Prima giornata | 3ª di Serie A | orizzonte lega 36 giornate su 38 |
| Scoring | **da confermare** | vedi sotto |
| Cambi / panchina | **da confermare** (diverso dai 3 di default) | incide sul valore della titolarità |

Due voci restano aperte e vanno chiuse prima di fidarsi delle proiezioni.

### Conseguenza della disattivazione del modificatore difesa

Non è un dettaglio di configurazione: il modificatore difesa è la ragione
principale per cui in molte leghe si spende sui difensori di squadre forti.
Senza, un difensore vale solo per i suoi bonus/malus diretti, che sono pochi.
La ripartizione di budget per ruolo di default del repo (P 7%, D 18%, C 25%,
A 50%) è tarata su leghe **con** il modificatore, quindi va rivista al ribasso
sulla D e al rialzo su C/A. Non va però riscritta a mano con numeri decisi a
occhio: è un output del solver di R2, non un input.

## Principi tenuti fermi

- **Nessun numero magico nel codice.** Tutto deriva dal profilo JSON. Se un
  numero serve e non è nel profilo, si estende lo schema del profilo.
- **Il target è uno slider**, derivato da `threshold + k·step`, mai una
  costante.
- **Modifiche additive** rispetto a upstream, per mantenere i merge possibili.
- Il server API e Vite girano sul Mac dell'utente, non nella VM dell'agente.
