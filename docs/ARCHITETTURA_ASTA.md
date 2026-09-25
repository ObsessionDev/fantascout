# Architettura del ricalcolo in asta

Analisi fatta prima di scrivere la UI, il 3 settembre 2026. Ogni affermazione
qui è stata verificata sui dati veri, non dedotta.

## Il vincolo reale

L'asta procede **per ruoli**: P, poi D, poi C, poi A. Si passa al ruolo
successivo solo quando **tutte** le squadre hanno riempito quel ruolo. I
partecipanti a turno chiamano un giocatore del ruolo corrente e parte l'asta
verbale.

Ne segue il requisito di latenza: scrivere il nome può costare qualche secondo,
ma **una volta selezionato il giocatore le informazioni devono essere già
pronte.** Non c'è tempo per un solver.

## Conclusione: non serve precalcolare nulla

Tre misure, prese sullo stato reale a metà asta, decidono l'architettura:

| Operazione | Tempo |
|---|---|
| Ricalcolo del piano dopo un acquisto registrato | **0.85 s** |
| Prezzo massimo **esatto di un singolo giocatore** | **1.05 s** |
| Prezzi massimi di una shortlist da 25 | 24.2 s |

Il calcolo a runtime non è il collo di bottiglia che sembrava. Il costo sta nel
**batch**, non nella singola risposta. Quando un giocatore viene chiamato e se
ne scrive il nome, il suo prezzo esatto esce in circa un secondo: meno del
tempo di scriverlo.

Quindi il flusso è banale e non ha stato da invalidare:

1. Acquisto registrato (prezzo + squadra) → si ricalcola il piano, 0.85 s.
2. Giocatore chiamato → si calcola **il prezzo massimo di lui solo**, 1.05 s.

Peggior caso, se la chiamata arriva mentre il piano sta ancora ricalcolando:
1.9 s. Il requisito era sotto i 5 s.

Nel frattempo la scheda del giocatore può mostrare subito, a costo zero, i
valori che la pipeline ha già calcolato: `fp_per_giornata`, `surplus`,
`prezzo_atteso`, confidenza. Nessuno di questi richiede una risoluzione.

Tutto ciò che segue — predizione dei prossimi chiamati, batch per ruolo, cache
di ruolo con invalidazione — risolveva un problema che la misura ha dissolto.
Resta qui perché le alternative valutate e scartate valgono quanto quella
scelta.

## Le due rotte

Stateless entrambe: `auction-store.js` resta l'unico proprietario dell'asta e
la manda al server per farla valutare. I giocatori viaggiano **per id**.

### `POST /api/auction/plan`

```json
{"profile_id": "lega-2026-27", "k": 1,
 "owned": [{"playerId": 2170, "price": 12}],
 "taken": [{"playerId": 5841, "price": 90}]}
```

Ritorna punti attesi, se il target è raggiunto, crediti impegnati e non
allocati, crediti per fantapunto al margine, slot ancora aperti nella lega per
ruolo, e la rosa pianificata. **~1.1 s.**

### `POST /api/auction/bid`

Come sopra più `"playerId"`: il giocatore appena chiamato. Ritorna prezzo
atteso, **prezzo massimo**, margine, FP/giornata, surplus e se il giocatore è
informativo. **~1.2-1.4 s** via HTTP.

Due memo tengono quel numero basso, ed entrambe sono necessarie: il dataset si
rilegge solo se cambia sul disco, e `ensure_lineup_cache` svuota la memo delle
formazioni solo quando cambia il profilo, non a ogni richiesta. Senza, la stessa
chiamata costa 2.2 s. Il dataset condiviso viene però copiato per richiesta,
perché la riprezzatura muta i prezzi e due stati d'asta non devono contaminarsi.

## La UI

Innestata, non riscritta. Lo strato di stato esisteva già e non è stato toccato:
`auction-store.js` resta l'unico proprietario dell'asta, `auction-nomination.js`
governa già le fasi P→D→C→A.

Tre file nuovi, tutti additivi:

- `auction-advisor-client.js` — parla con le due rotte. Divide la cronologia
  degli acquisti in *miei* e *degli altri*: entrambe le metà servono, perché i
  giocatori altrui escono dal listone **e** i loro crediti escono dal mercato.
- `use-auction-advisor.js` — l'hook. Ogni richiesta porta con sé lo stato per
  cui è stata fatta e la risposta viene scartata se quello stato non è più
  attuale. Un tetto vecchio mostrato come attuale è peggio di nessun tetto,
  perché l'utente ci rilancia sopra.
- `auction-ceiling.jsx` — il riquadro del prezzo massimo, con i tre colori già
  in uso per i verdetti d'asta. Durante un ricalcolo il numero precedente resta
  leggibile ma smorzato: svuotarlo si leggerebbe come "nessuna opinione", che è
  l'unica cosa che non deve dire.

Nel listone (`players.jsx`) prezzo atteso e surplus arrivano dal dataset, quindi
la lista non costa nulla da mostrare: il solver serve solo quando un giocatore è
sotto il martello. Un giocatore con i rate imputati dal FVM lo dichiara.

## Alternativa scartata 1: predire i prossimi chiamati

L'idea di stimare i prossimi giocatori chiamati e precalcolarli risolve un
problema che la struttura dell'asta ha già risolto.

In ogni istante è in vendita **un solo ruolo**. L'insieme dei possibili
prossimi chiamati non è "i giocatori probabili": è **tutti i giocatori ancora
disponibili di quel ruolo**, e sono pochi — 65 portieri, poi ~190 difensori,
~190 centrocampisti, ~86 attaccanti, in calo a ogni acquisto.

Quindi: **si prezza l'intero ruolo corrente, una volta, dopo ogni asta
conclusa.** Nessuna predizione, nessun rischio di sbagliare stima, zero cache
miss. La finestra di tempo utile è quella tra la chiusura di un'asta e la
chiamata successiva, che è ampiamente sufficiente.

Il costo del batch è l'unico problema aperto, e si risolve cambiando algoritmo,
non indovinando.

## Il costo: la traccia innesca la bisezione — **misurato, non assunto**

Il piano iniziale era sostituire del tutto la bisezione con la curva marginale.
Validato contro la bisezione fine, **non regge**: la traccia del greedy dà un
*limite inferiore*, non il valore esatto. Troncare il piano a una spesa minore
è **un** modo di permettersi il candidato, non il migliore, perché con meno
budget il greedy avrebbe comprato upgrade diversi e più economici.

Errori misurati con la sola traccia: esatta per 4 candidati su 6, ma
Calhanoglu 56.7 contro 78.6 reali e Dimarco 51.5 contro 69.9. Sottostimare di
22 crediti significa smettere di rilanciare troppo presto.

La traccia resta utile come **innesco**: è un limite inferiore valido e gratuito,
e la bisezione parte da lì invece che da zero.

| Metodo | Errore massimo | Costo |
|---|---|---|
| Bisezione da zero, 6 sonde | ~7 cr (risoluzione) | 7 risoluzioni |
| Sola traccia | 21.9 cr | 1 risoluzione |
| **Traccia + bisezione, 6 sonde** | **4.5 cr** | 7 risoluzioni |

L'errore residuo è **sempre conservativo**: il prezzo massimo calcolato non
supera mai quello vero, quindi lo strumento non fa mai rilanciare troppo.

Costo effettivo: ~1.1 s per candidato. Non serve prezzare tutti i giocatori del
ruolo: `_shortlist` scarta subito chi non migliorerebbe il piano, e per loro la
risposta è comunque "non rilanciare sopra la base". Restano ~15-30 candidati
veri per fase, cioè 15-35 secondi, che stanno nella finestra tra un'asta e la
chiamata successiva.

## Alternativa scartata 2: prezzo per sostituzione, con prezzo ombra

Formulazione proposta: il massimo per X è il prezzo oltre il quale esiste
un'alternativa Z dello stesso ruolo, con prezzo atteso non superiore, che rende
almeno quanto X.

È l'idea giusta a metà. La sostituzione da sola non dice nulla quando X è il
migliore ancora disponibile: nessuna alternativa lo eguaglia e il tetto sarebbe
illimitato. Il vincolo mancante è il budget, che entra tramite il valore
marginale del credito λ:

    max_bid(X) = prezzo(Z) + (valore(X) − valore(Z)) / λ

Costo: **1 ms per 83 candidati**, contro ~1 s ciascuno del metodo esatto. Tre
ordini di grandezza.

Ma la precisione non regge, e in modo pericoloso. Misurato:

| Giocatore | Formula | Verità | Scarto |
|---|---|---|---|
| Malen | 15.5 | 102.3 | −86.8 |
| Martinez L. | 15.5 | 84.1 | −68.6 |
| Pinsoglio, Terracciano, … | 10.7 | 0.0 | +10.7 |

Due cause. λ è la pendenza a fine curva, ma la funzione valore è **concava**:
estrapolarla linearmente su un salto grande sottostima molto. E il "sostituito"
finisce per essere l'ultimo panchinaro invece del titolare marginale, quindi il
guadagno calcolato non è quello che entra davvero nell'undici.

L'effetto netto è il peggiore possibile: **sottostima i giocatori forti e
sopravvaluta gli scarti**, quindi sbaglia perfino l'ordinamento — non è
recuperabile nemmeno come stima grezza da mostrare in attesa. Scartata.

Sopravvive `marginal_credit_value`, che resta un buon indicatore da mostrare
("al margine 1 FP costa 65 crediti"), ma non è una base per rilanciare.

## Alternativa scartata 3: la sola curva marginale

Oggi `maximum_bids` bisezione per candidato, 6 sonde, una risoluzione completa
del solver a sonda. Prezzare 60 portieri costerebbe ~75 secondi.

Il greedy però produce già ciò che serve: una **sequenza ordinata di upgrade**,
ognuno con (crediti spesi, punti guadagnati). Il cumulato è la funzione valore
`V(B)`, concava, e si ottiene **gratis** registrando la traccia del greedy che
si sta già eseguendo.

Per un candidato X basta allora una sola risoluzione, quella con X forzato in
rosa, che produce la traccia `V_X`. Il prezzo massimo è il più grande `p` tale
che `V_X(B − p) ≥ V(B)`, letto per interpolazione sulle due tracce.

Una risoluzione per candidato invece di sette: **7× più veloce, e per di più
esatto**, senza la risoluzione di ~7 crediti che la bisezione si porta dietro.

In più, uno screening preliminare elimina la maggior parte dei candidati senza
risolvere nulla: chi ha fantavoto inferiore al peggiore titolare del suo ruolo
non può avere un prezzo massimo sopra la base d'asta.

## Invalidazione

La chiave di cache è lo stato dell'asta, non il tempo:

```
hash(profilo, dataset, acquisti di tutte le squadre, crediti residui per squadra)
```

Ogni acquisto registrato cambia la chiave e fa partire il ricalcolo in
background. La UI legge sempre dalla cache: se durante il ricalcolo arriva una
lettura, mostra il valore precedente **marcato come in aggiornamento**, invece
di bloccare. Un numero leggermente stantio è utilizzabile; una schermata che si
blocca durante un rilancio no.

---

# Triage

## Stato: tutti i bloccanti chiusi il 3 settembre 2026

| | Bloccante | Esito |
|---|---|---|
| B1 | Dove gira il solver | endpoint Python (deciso) |
| B2 | `nomination_policy` sbagliata | corretto nel profilo |
| B3 | Riprezzatura cieca alle fasi | da +37.5% a **+0.0%** |
| B4 | Cache non chiavata | chiavata sull'hash profilo e limitata |
| B5 | Match per nome | id, e i nomi ambigui vengono rifiutati |

## Bloccanti — chiusi

### B1. Il solver è in Python, la UI no — decisione architetturale

`advisor/optimize.py` è l'unica implementazione del modello. Il client, per
convenzione del progetto, **non esegue la pipeline**: consuma JSON generato.
Ma il ricalcolo in asta dipende dallo stato dell'asta, che vive nel browser.

Due strade, e vanno decise adesso perché la UI dipende dalla scelta:

- **Endpoint in `advisor.server`** (raccomandato). Stateless: il browser manda
  lo stato dell'asta, riceve la tabella dei prezzi massimi del ruolo corrente.
  Riusa il codice testato, non duplica il modello, e non tocca la proprietà
  dello stato: `auction-store.js` resta l'unico proprietario. Costo: il server
  deve girare durante l'asta, e già gira.
- **Portare il solver in JS.** Nessuna dipendenza dal server, ma duplica il
  modello in due linguaggi. Due implementazioni divergono sempre, e qui
  divergere significa consigli diversi dalla simulazione. Sconsigliato.

### B2. `nomination_policy` era `call` invece di `call_by_role` — **corretto**

Il profilo dichiarava un'asta libera. `isRoleNomination` accetta solo
`call_by_role`, `random_by_role`, `alphabetical_by_role`: con `call`,
`activeNominationRole` ritorna `null` e **tutta la logica delle fasi per ruolo
resta spenta**. La UI sarebbe stata costruita su un presupposto falso.

**Deciso: endpoint in `advisor.server`.** Uso solo in locale, quindi la
dipendenza dal server non costa nulla e il modello resta in un solo linguaggio.

### B3. La riprezzatura ignora le fasi per ruolo — **corretto**

`reprice_remaining_market` distribuisce gli slot residui sulle **proporzioni
originali** dei ruoli. In un'asta per fasi questo è strutturalmente sbagliato a
ogni passaggio di fase, e peggiora fino a diventare grave alla fine.

Misurato: chiuse le fasi P, D e C, restano 48 slot di attaccanti e 800 crediti.
La formula usa **12 attaccanti invece di 48** per fissare la scala, e prezza i
48 attaccanti residui a **1100 crediti quando ce ne sono 800: +37%**.

È l'errore peggiore possibile, perché colpisce nella fase in cui non c'è più
margine per rimediare.

**Correzione.** La struttura per fasi rende noti con certezza gli slot ancora
aperti per ruolo: si contano, non si stimano. Ogni ruolo ancora aperto reclama
una quota dei crediti residui proporzionale al valore di mercato che deve
ancora assorbire. L'identità si conserva — i prezzi attesi sui giocatori ancora
da comprare sommano ai crediti ancora da spendere — e il caso pre-asta ricade
esattamente nella formula originale, con zero slot riempiti.

Verificato sullo stesso scenario: i 48 attaccanti residui ora sommano a **800
crediti esatti, scarto +0.0%**.

### B4. `_LINEUP_CACHE` non è chiavata sul dataset

La cache è globale, illimitata, e la chiave sono i soli id dei giocatori. Ma il
valore dipende da fantavoto e probabilità, che cambiano con scoring e profilo.
In un processo server che serve più di un profilo **restituisce il valore di
un'altra lega**, silenziosamente. Va chiavata anche su
`dataset_configuration_hash` e limitata in dimensione.

### B5. I giocatori vanno identificati per id, non per nome

`_resolve_owned` fa match sul nome minuscolo. Nel listone non ci sono duplicati
esatti, ma **18 cognomi sono condivisi**: `Martinez Jo.` / `Martinez L.`,
`Terracciano` / `Terracciano F.`, `Pessina` / `Pessina Mas.`, `Stankovic F.` /
`Stankovic A.`. In asta assegnare il giocatore sbagliato è irreversibile.
La UI deve passare id; la CLI può tenere i nomi come comodità.

## Non bloccanti — verificati e a posto

- **Il vincolo di rosa completa regge.** Testato pagando 450 crediti per tre
  portieri: con 50 crediti residui il solver completa comunque 25 slot e
  segnala `SOTTO IL MINIMO` (56.27 FP). Il prezzo ombra sale correttamente a
  0.27 FP/credito. Non serve un vincolo di riserva esplicito.
- **`auction-store.js` non va toccato.** Modella già tutte le squadre, con
  `owner`, `price`, `playerId`, cronologia, undo e migrazione di versione. E
  `auction-nomination.js` implementa già le fasi P→D→C→A. Lo strato di stato
  che serve **esiste già**: manca solo lo strato di calcolo.
- **La risoluzione dei prezzi massimi** (~7 crediti) sparisce da sola passando
  alla curva marginale, quindi non va gestita separatamente.

## Debito noto, non bloccante

- Il greedy non fa scambi multi-slot ("vendo due medi per un top"). In un'asta
  reale non si rivende, quindi il limite morde poco.
- `p_gioca` non tiene conto delle prime due giornate di Serie A già giocate.
- `ROSTER_SLOTS` e i default di `LeagueConfig` in `config.py` contengono ancora
  costanti di lega (750 crediti, soglia 66, passo 5). Sono solo default,
  sovrascritti da `from_profile`, ma violano la convenzione del fork e prima o
  poi ingannano qualcuno.

## Ordine consigliato

B2 è chiuso. Poi B1 (la decisione), B3 e B5 — che sono correttezza — e infine
B4, che morde solo quando il server serve più profili. La curva marginale
sostituisce la bisezione insieme a B3, perché toccano lo stesso codice.
Solo dopo, la UI.
