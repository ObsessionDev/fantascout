# Visione

Dove va lo strumento dopo l'asta. Non sono requisiti: sono le domande che
vorremo saper rispondere, annotate finché sono fresche, con cosa serve e cosa
abbiamo già.

L'asta è un evento, la stagione è trentasei. Tutto quello che segue vale più
dell'asta in termini di punti, e ha molto più tempo per essere fatto bene.

## 1. Formazione di ogni giornata

**La domanda:** chi schierare questa giornata, dati i miei venticinque.

È la funzione più usata di tutte — trentasei volte, contro una — ed è anche la
più vicina a essere già pronta. Il dataset porta già gli array per giornata
(`p_gioca_per_giornata`, `voto_puro_mean_per_giornata`,
`bonus_atteso_per_giornata`, `venue_per_giornata`), e `optimize.lineup_value`
già sceglie l'undici migliore fra le formazioni ammesse tenendo conto della
copertura panchina e del cap sui cambi. Scegliere la formazione di *una*
giornata è quella funzione ristretta a un solo indice.

**Cosa manca davvero:** la disponibilità reale vicino alla scadenza —
infortuni, squalifiche, probabili formazioni. Il modello oggi conosce solo un
prior di titolarità fissato a inizio stagione. Senza quel dato la funzione dice
cose ragionevoli ma cieche, e schierare un infortunato costa più di ogni
raffinatezza del modello.

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
