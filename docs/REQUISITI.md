# Requisiti

Cosa manca a upstream per servire il caso d'uso di questa lega, in ordine di
valore. Il contesto e le decisioni stanno in `DECISIONI.md`.

## Il modello mentale

Upstream risponde a: *"data questa rosa, quanto vale?"*
A noi serve l'inverso: *"dato un target di fantapunti, qual è la rosa più
economica che lo raggiunge?"* — e, in asta, *"questo giocatore a questo prezzo
mi avvicina o mi allontana dal target?"*

L'unità di misura è il **fantapunto atteso per giornata della formazione
schierata** (`E[FP/giornata]`), non la somma delle proiezioni dei 25 in rosa.
Upstream valuta già la miglior formazione fixture-aware più la copertura dei
voti mancanti: è la base giusta, va solo invertita.

## Il target

Non è una costante. Deriva dal profilo:

```
target(k) = virtual_goals.threshold + k * virtual_goals.step
```

Con `threshold = 66` e `step = 6`: `k=0 → 66` (1 gol), `k=1 → 72` (2 gol),
`k=2 → 78` (3 gol). Il valore `72` non deve comparire da nessuna parte nel
codice: `k` è lo slider, il resto viene dal JSON.

---

## R0 — Il bonus zero sui giocatori senza storico

**Bug di correttezza, non feature. Da fare per primo.**

123 giocatori su 531 (23% del listone) non hanno righe storiche nelle tre
stagioni caricate: nuovi acquisti dall'estero, giovani, neopromossi.
Per loro `pipeline.py` calcola `bonus = 0`, perché tutti i rate per 90
(`Gf`, `Ass`, `Amm`, `Esp`, `Au`, `Gs`) sono vuoti.

Zero non è un valore neutro, perché il bonus atteso ha segno diverso per ruolo:

| Ruolo | bonus tipico con storico | bonus senza storico | effetto |
|---|---|---|---|
| P | da −0.9 a −1.7 (gol subiti) | 0.00 | **sopravvalutato** |
| D | leggermente negativo (cartellini) | 0.00 | sopravvalutato |
| C | da +1.0 a +1.7 | 0.00 | sottovalutato |
| A | da +0.8 a +2.9 | 0.00 | sottovalutato |

Il risultato è visibile nella classifica dei portieri prodotta oggi: i primi
due per fantavoto atteso sono Vicario e Perri, e sono primi **solo perché il
modello non sa niente di loro**. Ogni portiere di cui abbiamo dati viene
scavalcato da uno di cui non ne abbiamo. La distorsione è direzionale e
sistematica, non rumore.

**Risolto il 3 settembre 2026.** Sono state valutate tre opzioni:

1. *Estrarre i dati esteri mancanti* — corretto ma fuori tempo massimo a poche
   ore dall'asta, e richiede fonti non presenti nel repo.
2. *Mediana per ruolo* — cheap, ma dà lo stesso bonus a Ramos G. (FVM 237) e a
   Varela G. (FVM 40), quindi produce surplus fasulli: enormi e positivi sui
   giocatori senza storico che costano poco, enormi e negativi su quelli che
   costano molto. Sostituisce una distorsione con un'altra.
3. *Regressione del rate su `log(1 + FVM)` per ruolo* — **scelta.**

La verifica empirica sui giocatori **con** storico dice che il FVM predice il
bonus proprio dove serve:

| Ruolo | n | R² | guadagno RMSE vs mediana |
|---|---|---|---|
| P | 47 | 0.06 | 7% |
| D | 144 | 0.25 | 16% |
| C | 153 | 0.48 | 29% |
| A | 64 | 0.59 | 36% |

Su P e D il fit aggiunge poco, ma lì la dispersione del bonus è comunque
piccola e il fit degrada da solo verso la media di ruolo. Su C e A, dove il
bonus **è** il valore del giocatore, il segnale è forte.

I coefficienti sono stimati a runtime dalle stagioni caricate
(`fit_rate_priors` in `pipeline.py`), mai scritti nel codice. I giocatori
imputati portano `proiezione.fonte_rate = "prior_fvm"`.

**Il limite, dichiarato:** per questi giocatori la proiezione è derivata dal
prezzo, quindi il loro surplus (R1) sarà ≈ 0 per costruzione. È il risultato
epistemicamente corretto — su di loro non abbiamo informazione indipendente dal
mercato, e non dobbiamo fingere di averla. Vanno mostrati a bassa confidenza
(R5) e, in asta, non sono né occasioni né trappole: sono ignoti.

## R1 — Surplus per credito — **fatto**

Implementato in `pipeline.py` (`market_price_expectations`, `market_value_curve`,
`annotate_surplus`); ogni giocatore porta un blocco `mercato` in
`auction_data.json`.

Il prezzo atteso non usa più il fattore `0.75` di upstream, che non sopravvive
a un cambio di crediti. Due identità lo fissano senza parametri liberi: il
mercato piazza esattamente `8 × 25 = 200` giocatori e spende esattamente
`8 × 500 = 4000` crediti, al minimo di 1 ciascuno. Verifica: i prezzi attesi
sommano a 4000 esatti, e la ripartizione per ruolo **emerge** invece di essere
imposta — P 7.1%, D 18.9%, C 34.8%, A 39.2%. Da notare che il mercato mette in
C molto più del 25% previsto dal profilo di default.

Esempi di surplus reali (giocatori con storico):

| Giocatore | Prezzo atteso | FP/g | Atteso dal prezzo | Surplus |
|---|---|---|---|---|
| Mandragora (C, Torino) | 9.0 | 5.65 | 3.64 | +2.01 |
| Busio (C, Venezia) | 5.9 | 5.38 | 3.20 | +2.18 |
| Colombo (A, Genoa) | 19.1 | 6.08 | 4.08 | +2.00 |

**Avvertenza sul surplus per credito:** è instabile sotto i ~3 crediti, dove il
denominatore domina. `Forson O.` guida quella classifica con un surplus di
+0.73 solo perché costa 1.3. Per l'asta si usa il surplus, e il solver per le
decisioni di rosa.

## R1-bis — Il vecchio testo di R1

**Probabile quick win della serata.**

`fvm_original` è conservato dalla pipeline. Il surplus è la differenza tra la
proiezione del giocatore e il prezzo che il mercato gli attribuisce, riscalata
sui crediti effettivi della lega:

- prezzo atteso = `fvm_original` normalizzato sul monte crediti della lega
  (8 squadre × 500 crediti), non sul monte del profilo di default;
- surplus = `E[FP/giornata]` del giocatore − il contributo che ci si
  aspetterebbe da un giocatore di quel prezzo;
- ordinamento per surplus e per surplus/credito, filtrabile per ruolo.

È ciò che rende utilizzabile lo strumento in asta: dice se il prezzo corrente
è sopra o sotto il valore, non solo chi è il giocatore migliore in assoluto.
Da esporre nella vista `players` e nella vista `auction`.

**Attenzione:** con il modificatore difesa disattivato, il FVM di mercato dei
difensori è sistematicamente gonfio rispetto al loro valore in questa lega.
Il surplus sui difensori sarà negativo quasi ovunque — è il risultato corretto,
non un bug, e va reso leggibile nella UI.

## R2 e R3 — Inversione moneyball e curva costo/target — **fatte**

`advisor/optimize.py`.

### L'obiettivo, corretto il 3 settembre

La prima formulazione era "rosa più economica che raggiunge il target". È
sbagliata: **i crediti non spesi valgono zero**, quindi risparmiare non è un
risultato. L'obiettivo è *massimizzare i fantapunti dato il budget*, con il
target come **vincolo minimo da verificare**, non come condizione di arresto.

L'unica domanda in cui minimizzare la spesa ha senso è la curva costo/target,
che serve proprio a sapere quanto costa un punto. Lì il flag interno
`stop_at_target` resta attivo; ovunque altro no.

Ne segue la vera modalità d'uso: **pre-asta** per etichettare i giocatori
obiettivo, poi **durante l'asta** si registrano gli acquisti di *tutte* le
squadre e si rilegge il piano aggiornato.

```bash
# pre-asta: lista obiettivo e prezzi massimi
.venv/bin/python -m advisor.optimize --data data/processed/lega-2026-27/2026-27/auction_data.json \
  --profile config/profiles/lega-2026-27.json --k 1

# in asta: i miei acquisti e quelli degli altri
.venv/bin/python -m advisor.optimize ... \
  --owned "Berardi:60,Mandragora:12" \
  --taken "Martinez L.:180,Malen:170,Hojlund:150"

# curva costo/target
.venv/bin/python -m advisor.optimize ... --curve
```

### Prezzo massimo di rilancio

Il numero che serve davvero in asta non è chi comprare ma **fino a quanto
rilanciare adesso**. Definito contro il piano, non contro un singolo scambio:
è il prezzo più alto al quale possedere quel giocatore, e poi spendere al
meglio ciò che resta, batte ancora il piano che non lo include. Un credito in
più peggiora la rosa, per quanto forte sia il giocatore.

Trovato per bisezione, perché il valore del piano scende in modo monotono al
calare del budget. Costa una risoluzione del solver per sonda, quindi gira su
una rosa ristretta di candidati. Risoluzione ~7 crediti con 6 sonde: i prezzi
massimi sono indicativi al credito, non esatti.

Esempio a metà asta (io: Berardi 60, Mandragora 12; la lega ha già bruciato
1000 crediti sui big):

| Giocatore | Prezzo atteso | Max | Margine | |
|---|---|---|---|---|
| Kolo Muani (A) | 51.5 | 60.2 | +8.7 | occasione |
| Bremer (D) | 19.4 | 20.1 | +0.7 | occasione |
| Da Cunha (C) | 27.6 | 20.1 | −7.5 | lascia andare |
| Paz N. (C) | 76.0 | 60.2 | −15.8 | lascia andare |

### Mercato residuo

Registrare gli acquisti altrui non serve solo a togliere i giocatori dal pool.
Consuma anche i **crediti degli avversari**, e le due cose non calano allo
stesso ritmo. `reprice_remaining_market` ricalcola i prezzi attesi sui crediti
e sugli slot che restano alla lega: una stanza che ha speso presto ha meno
crediti per slot residuo, e tutto ciò che è ancora sul tabellone costa meno.
Nell'esempio sopra Laurientè passa da 29.9 a 26.4 attesi.

È il motivo per cui vale la pena inserire anche gli acquisti degli altri, non
solo i propri: ignorarlo è il modo in cui ci si ritrova con crediti che non si
riescono più a convertire in giocatori.

### Prestazioni

`lineup_value` è memoizzata sull'insieme dei giocatori — il prezzo non entra
nel valore della rosa — e il pool dei candidati è ristretto ai migliori 45 e ai
più economici 25 per ruolo, perché tutti gli altri sono dominati. Il giro
completo con prezzi massimi passa da 50 a 10 secondi.

### La correzione che ha cambiato tutto: la copertura panchina

La prima versione valutava la rosa sommando `p_gioca × fantavoto` degli undici,
e dava il target **irraggiungibile a ogni k** (tetto 64.45 FP). Era il modello
a sbagliare, non la lega: con `p_gioca` mediana 0.846 le assenze attese in un
undici sono **1.69**, contro **4 cambi** disponibili. Quasi ogni voto mancante
è coperto dalla panchina, e addebitare al titolare le giornate che salta
sottostima la rosa di circa un settimo.

`lineup_value` ora vale ogni slot come il titolare quando ha il voto più la
panchina dietro di lui quando non ce l'ha, con il cap dei cambi dal profilo e
`incomplete_lineup.score` per le assenze scoperte. È la ragione per cui
`mercato.fp_per_giornata` (che è la misura giusta per una *lista*) e il valore
di *rosa* sono due numeri diversi e devono restare separati.

### Panchina a dimensione libera

La lega non usa una panchina composta per ruolo: sono **8 giocatori qualsiasi**.
Aggiunto `bench_switch.composition = "any_role"` con `bench_size`, entrambi con
default retrocompatibile (`by_role`), quindi il profilo di upstream non cambia.

Non è un dettaglio di configurazione. Con rosa 25 e undici titolari restano 14
giocatori liberi, ma solo 8 si siedono: la copertura diventa **scarsa e
condivisa fra i ruoli**, e un quarto difensore di riserva viaggia solo se vale
più di un secondo attaccante di riserva. Prima il modello considerava
disponibili tutti e 14.

Effetto misurato sulla curva:

| target | prima (copertura illimitata) | con panchina 8 |
|---|---|---|
| 66 | 107 cr | 106 cr |
| 72 | 190 cr | **242 cr** |
| 75 | 352 cr | 376 cr |
| tetto | 77.63 | **76.51** |

Il nucleo da 72 FP costa **52 crediti in più**, il 27%. Con meno panchina i
riempitivi da 1 credito coprono meno, quindi servono titolari migliori.

Fatto solo nel motore d'asta (`optimize.py`): `simulation.py` legge ancora la
panchina per ruolo, e il pannello impostazioni non espone il campo. Entrambi
sono lavoro post-asta, tracciato.

### La curva

| k | target | punti | spesa | costo del punto marginale |
|---|---|---|---|---|
| 0 | 66 | 66.18 | 81 | — |
| 0.5 | 69 | 69.10 | 116 | 11.6 cr |
| 1 | 72 | 72.23 | 193 | 25.7 cr |
| 1.5 | 75 | 75.02 | 333 | 46.7 cr |
| 2 | 78 | 77.44 | 499 | 69 cr — fuori portata |

Il punto marginale costa **sei volte** di più a 75 che a 69. Il tetto con tutti
i 500 crediti è ~77.4: **78 FP, cioè 3 gol virtuali, non è raggiungibile.**

Conseguenza per l'asta: il nucleo da 72 FP costa 193 crediti. I crediti non
spesi non valgono nulla, quindi si spendono comunque — ma sapendo che gli
ultimi 3 punti costano più dei primi 9, e che ogni avversario che paga un
difensore a prezzo di listino sta comprando sulla parte piatta della curva.

## R2-bis — Il vecchio testo di R2

Il problema:

```
minimizza   Σ prezzo(giocatore)
soggetto a  E[FP/giornata](rosa) ≥ target(k)
            slot esattamente 3P / 8D / 8C / 6A
            Σ prezzo ≤ 500
            ≥ 1 credito residuo per ogni slot ancora aperto
```

Note di implementazione:

- Non è separabile per ruolo: `E[FP/giornata]` dipende dalla formazione
  schierabile e dalla copertura panchina, quindi il contributo di un giocatore
  dipende da chi altro c'è in rosa. Un greedy per surplus dà una buona
  soluzione iniziale, non l'ottimo.
- La ripartizione di budget per ruolo (P/D/C/A) deve essere **output** del
  solver, non input. Le percentuali del profilo restano come vincolo morbido
  opzionale, non come assunzione.
- Serve una modalità incrementale: data la rosa parziale già acquistata in
  asta, ricalcolare la rosa ottima sui crediti e sugli slot rimasti. È questa
  la modalità che si usa realmente durante la serata.

## R3 — Curva costo ↔ target

Quanto costa il fantapunto marginale. Si risolve R2 per una griglia di `k`
(es. target da 66 a 84) e si traccia costo minimo contro target.

La curva serve a due domande: dove sta il ginocchio (fin dove comprare punti
è economico) e quanto margine di crediti resta a un dato target. Un target
raggiungibile a 430 crediti su 500 lascia 70 crediti di potere di fuoco per
i rilanci; uno che ne costa 495 no.

## R4 — Varianza: da stagione a giornata

Il Monte Carlo di upstream simula la stagione. Serve la distribuzione del
punteggio di **una giornata** per la rosa candidata, quindi
`P(FP_giornata ≥ target)`.

Una rosa che centra il target in media ma lo manca il 45% delle giornate è
peggiore, in un campionato a scontri diretti tra 8 squadre, di una che ha una
media leggermente più bassa e una varianza minore. Il criterio di scelta
finale dovrebbe essere la probabilità di superare il target, non la media.

Da fare dopo R2: senza rosa candidata non c'è niente da simulare.

## R5 — Onestà sul contesto

Le proiezioni sono storico + prior di titolarità a zero giornate osservate
nella lega. Le prime 2 giornate di Serie A già giocate **non** sono nel
modello. La UI deve dirlo dove mostra i numeri, e la confidenza deve essere
visibilmente più bassa per neopromossi, nuovi acquisti e giocatori con poco
storico in Serie A — sono i casi in cui il prior fa tutto il lavoro.

---

## R6 — Eventi di scoring mancanti nello schema

Il regolamento della lega prevede quattro eventi che il modello non conosce:

| Evento | Valore | Nello schema? | Nei dati? |
|---|---|---|---|
| Rigore segnato | +3 | non serve: uguale al gol, già coperto da `goal` | `R+` |
| Rigore sbagliato | −3 | **manca** | `R-` |
| Rigore parato | +3 | **manca** | `Rp` |
| Porta inviolata | +1 | **manca** | **non nei dati** |

I primi due sono meccanici: si aggiungono a `ScoringEventValues` con default 0
(retrocompatibile con `default_profile.json`) e si leggono le colonne `R-` e
`Rp`, che esistono già nei file `statistiche_*.xlsx`.

La porta inviolata è l'unico vero problema di modello. I file statistiche
danno solo `Gs` (gol subiti totali) e `Pv` (presenze), mai il dettaglio per
partita: il numero di clean sheet non è ricavabile per aggregazione. Va
modellato, per esempio come Poisson sui gol subiti per partita —
`λ = Gs/Pv`, `P(clean sheet) = e^(−λ)` — meglio ancora reso fixture-aware
usando la forza dell'avversario, che la pipeline già calcola.

Ordine di grandezza, sui portieri titolari con storico: `λ` va da 0.76
(Mandas) a 1.21 (De Gea), quindi `P(CS)` da 0.47 a 0.30. La porta inviolata
sposta i portieri di circa 0.17 FP/giornata tra il migliore e il peggiore,
mentre i gol subiti li separano già di 0.57. Sommati: circa 0.74 FP/giornata
tra il portiere migliore e il peggiore, cioè ~27 FP sulle 36 giornate della
lega — meno di mezzo gol virtuale.

**Conclusione operativa: il portiere non è dove si vince questa lega.**
Con il modificatore difesa disattivato la differenza tra il portiere più caro
e uno da 1 credito è piccola in fantapunti e grande in crediti.

## R7 — Incroci fixture tra portieri: valutato e non implementato

**Proposta:** una sezione dedicata all'accoppiamento fixture-aware dei
portieri, perché una volta comprato il portiere X il valore marginale di Y
dipende da quanto i loro calendari si complementano, e il surplus dovrebbe
tenerne conto.

**Già in upstream, più di quanto sembri.** `_sample_goalkeeper_utility`
(`simulation.py:88`) è indicizzata per giornata, valuta ogni club come unità
separata e sceglie l'opzione migliore giornata per giornata: la rotazione
fixture-aware è già modellata. La riga 194 valuta esattamente il valore
marginale di un candidato *dato* l'insieme dei portieri già posseduti. Quindi
il consiglio d'asta lo considera già.

**Misurato il tetto massimo, e non vale la pena estenderlo.** Guadagno della
rotazione ottimale su coppia rispetto al miglior portiere singolo, giornate
3-38:

| Coppia | FVM tot | guadagno |
|---|---|---|
| Svilar + Carnesecchi | 140 | +0.149 FP/g |
| Meret + Okoye | 76 | +0.148 |
| Skorupski + Meret | 80 | +0.142 |
| Falcone + Maignan | 78 | +0.135 |
| Stankovic F. + Butez | 61 | +0.133 |
| Martinez Jo. + Bijlow | 83 | +0.129 |
| Butez + Bijlow | 65 | +0.125 |

Il guadagno è compreso tra +0.125 e +0.149 per **ogni** coppia praticabile.
È praticamente una costante: non discrimina tra le scelte, quindi non è un
vantaggio competitivo. Una vista dedicata mostrerebbe una tabella in cui ogni
cella dice `+0.14`.

La ragione sta a monte. L'oscillazione fixture-aware di un giocatore tra le
giornate ha sd mediana **0.13 in tutti e quattro i ruoli** (P 0.122, D 0.132,
C 0.132, A 0.131): il termine di calendario di questo modello è piccolo e
uniforme per costruzione, perché — come dichiara `MODEL.md` — le proiezioni
per giornata variano con l'avversario *preservando la media stagionale del
giocatore*. Il massimo di due estrazioni con sd 0.13 vale circa 0.10, che è
esattamente ciò che si osserva.

Per confronto, il dislivello di **livello** tra portieri titolari è 0.57
FP/giornata (Svilar 4.589, Caprile 4.023): quattro volte il guadagno da
rotazione. Chi è il portiere conta molto più di come lo si accoppia.

**Limite dichiarato:** questa è un'affermazione sul modello, non sul calcio.
Se l'effetto calendario reale è più grande di ±0.13, il modello lo sta
sottostimando e la rotazione varrebbe di più. Rivalutare R7 solo se si decide
di rendere più aggressivo il termine fixture — a quel punto la misura va
rifatta, non riusata.

## Ordine di lavoro

R0 va per primo: è una correzione di correttezza e senza di essa ogni
classifica è distorta in modo direzionale.
Poi R1 → R2 → R3, che sono la catena che serve stasera.
R6 è cheap per le due voci sui rigori, più costoso per la porta inviolata, e
comunque di impatto contenuto.
R4 e R5 migliorano la qualità della decisione ma non sono prerequisiti per
prenderla.
