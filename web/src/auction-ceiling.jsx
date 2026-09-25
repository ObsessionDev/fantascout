/** The ceiling: the most this player is worth right now, in credits.
 *
 *  Defined against the whole plan rather than a single swap — the highest price
 *  at which owning him, and then spending what is left as well as possible,
 *  still beats the plan that does not include him. One credit more makes the
 *  roster worse however good the player is, which is the question a live auction
 *  actually asks.
 *
 *  It is deliberately louder than the market price beside it. The expected price
 *  says what the room will probably pay; only the ceiling says when to stop.
 */

const credits = (value) =>
  Number.isFinite(Number(value)) ? Math.round(Number(value)) : null;

export function Ceiling({ state, price }) {
  const bid = state?.bid;
  const ceiling = credits(bid?.prezzo_massimo);
  const expected = credits(bid?.prezzo_atteso);
  const current = Number(price);
  const stale = state?.status === "loading" && bid;

  if (state?.status === "error") {
    return (
      <div className="ceiling ceiling--muted">
        <span className="ceiling-label">Prezzo massimo non disponibile</span>
        <span className="micro">
          {state.error?.code === "network_error"
            ? "Il server locale non risponde: avvialo per avere il tetto di rilancio."
            : state.error?.message}
        </span>
      </div>
    );
  }

  if (!bid) {
    return (
      <div className="ceiling ceiling--muted">
        <span className="ceiling-label">Calcolo del prezzo massimo…</span>
        <span className="micro">Circa un secondo.</span>
      </div>
    );
  }

  // A player who cannot improve the plan at any price is not a matter of degree.
  if (ceiling === 0) {
    return (
      <div className={`ceiling ceiling--never${stale ? " is-stale" : ""}`}>
        <span className="ceiling-label">Non rilanciare</span>
        <span className="ceiling-note">
          Non migliora la rosa a nessun prezzo. Prezzo atteso {expected} cr.
        </span>
      </div>
    );
  }

  const over = Number.isFinite(current) && current > ceiling;
  const margin = ceiling != null && expected != null ? ceiling - expected : null;
  const gain = Number(bid.gain ?? bid.guadagno);

  return (
    <div className={`ceiling ceiling--${over ? "over" : "under"}${stale ? " is-stale" : ""}`}>
      <div className="ceiling-main">
        <span className="ceiling-label">Prezzo massimo</span>
        <strong className="ceiling-value">{ceiling}</strong>
        <span className="ceiling-unit">crediti</span>
      </div>
      <span className="ceiling-note">
        {over
          ? `Sei sopra di ${Math.round(current - ceiling)}: i crediti rendono di più altrove.`
          : `Mercato ${expected} cr${
              margin > 0 ? ` · margine +${Math.round(margin)}` : margin < 0 ? ` · sopra il tuo tetto di ${Math.round(-margin)}` : ""
            }`}
      </span>
      {/* The gain is what he is actually worth; the ceiling is that divided by
          what a credit buys at the margin. While the plan still cannot spend the
          budget, that divisor is nearly zero and the ceiling swings wildly on a
          hair's difference in points, so the gain is shown next to it and the
          ceiling is marked as the soft number it is. */}
      {Number.isFinite(gain) ? (
        <span className="ceiling-note">
          Aggiunge <b>{gain.toFixed(2)}</b> FP/giornata al piano
          {bid.piano_saturo === false
            ? " · hai ancora crediti non allocati, quindi il tetto è indicativo e generoso"
            : ""}
        </span>
      ) : null}
      {bid.informativo === false ? (
        <span className="micro ceiling-warn">
          Proiezione derivata dal prezzo: su questo giocatore non abbiamo dati propri.
        </span>
      ) : null}
      {stale ? <span className="micro">Aggiornamento in corso…</span> : null}
    </div>
  );
}
