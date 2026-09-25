# Visione

Dove va lo strumento dopo l'asta. Non sono requisiti: sono le domande che
vorremo saper rispondere, annotate finché sono fresche, con cosa serve e cosa
abbiamo già.

L'asta è un evento, la stagione è trentasei. Tutto quello che segue vale più
dell'asta in termini di punti, e ha molto più tempo per essere fatto bene.

## 1. Formazione di ogni giornata — in corso

**La domanda:** chi schierare questa giornata, dati i miei venticinque.

È la funzione più usata di tutte — trentasei volte, contro una. `optimize.best_lineup`
(estratto da `optimize.lineup_value`, che ora tiene la formazione scelta invece
di scartarla) sceglie l'undici migliore fra le formazioni ammesse tenendo conto
della copertura panchina e del cap sui cambi. `advisor.lineup` lo applica a una
sola giornata: dati una rosa, una giornata e le indisponibilità, restituisce
l'undici, la panchina in ordine e le note (`README.md`, sezione "Matchday
Lineup"). L'esportazione della rosa dalla dashboard (fase 1 della proposta) è
il pulsante **Esporta la mia rosa** nella schermata asta.

**Cosa manca ancora:** la disponibilità vicino alla scadenza è oggi scritta a
mano (`--indisponibili`, `--dubbi`); leggerla dalle probabili formazioni dei
siti sportivi è la fase 3, non ancora costruita — prima vanno letti i termini
d'uso dei siti candidati. Manca anche il flusso su Telegram (fase 4: `/formazione`
o pianificato) e la varianza contro l'avversario, discussa sotto.

**Nota sulla varianza:** a differenza dell'asta, qui il criterio giusto non è
sempre la media. Contro un avversario più forte conviene alzare la varianza,
contro uno più debole abbassarla. È R4 (`P(rosa ≥ target)`) applicato allo
scontro diretto, e la simulazione Monte Carlo esiste già.

## 2. Consigli di scambio

**La domanda:** questo scambio mi conviene?

È la stessa domanda del prezzo massimo, con due giocatori che si muovono invece
di uno. La macchina c'è già: `maximum_bid_for` valuta un piano con e senza un
giocatore; uno scambio è la stessa valutazione con un ingresso e un'uscita, più
l'eventuale conguaglio in crediti.

**Cosa serve in più:**

- L'orizzonte residuo. A gennaio restano meno giornate, quindi un giocatore che
  rende di più ma rientra fra sei settimane vale meno che a settembre. Il
  valore va calcolato sulle giornate che restano, non sulle trentasei.
- Il valore per la controparte. Uno scambio si chiude se conviene a entrambi:
  saper stimare cosa ci guadagna l'altro dice quali proposte hanno senso fare,
  ed è esattamente il calcolo che facciamo per noi applicato alla sua rosa.
- La rosa avversaria, che l'asta già registra: `auction-store.js` tiene tutte
  le squadre, non solo la mia.

## 3. Prestiti

Meccanica nuova di Fantagazzetta. **Le regole vanno confermate prima di
modellarle** — è la stessa disciplina tenuta per scoring, modificatore difesa e
panchina: nessun numero e nessuna meccanica entra nel modello per sentito dire.

Le domande da chiudere quando ci arriviamo: quanti prestiti, per quanto tempo,
chi prende i punti del giocatore prestato, se c'è un costo in crediti, se il
prestito è reversibile, cosa succede alla rosa di partenza mentre il giocatore
è via.

Quasi certamente comporta un'estensione dello schema del profilo, come ogni
altra regola di lega: se un valore serve nel codice e non esiste nel profilo,
si estende il profilo.

## 4. Asta di riparazione

**La domanda:** stessa dell'asta, ma con tre differenze che cambiano i conti.

- **Rosa parziale, non vuota.** Già gestito: la modalità incrementale del
  solver esiste e si usa dal primo acquisto della sessione di settembre.
- **Orizzonte più corto.** Il target in fantapunti per giornata resta lo stesso
  — è per giornata, quindi non si riscala — ma il *valore* di un credito
  cambia: comprare un punto per diciotto giornate vale metà che per trentasei.
  La curva costo/target va riletta sull'orizzonte residuo.
- **Mercato diverso.** Meno crediti in circolazione, meno slot aperti, e
  giocatori il cui prezzo di listino non riflette più quello che hanno fatto
  vedere. `reprice_remaining_market` ragiona già su crediti e slot residui; il
  pezzo mancante è che a gennaio abbiamo mezza stagione di dati **veri** su
  questa Serie A, che oggi non esistono.

Ed è la nota più importante di tutte: a gennaio il modello smette di essere
storico più prior e diventa storico più stagione in corso. Sarà molto più
affidabile di stasera.

---

Ordine plausibile per valore: formazione di giornata, poi scambi, poi asta di
riparazione, e i prestiti quando le regole sono note. Nessuno di questi ha la
fretta che aveva l'asta.
