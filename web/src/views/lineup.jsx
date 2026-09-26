import { useEffect, useMemo, useState } from "react";
import { buildLineup, getMatchdayStatus, LineupClientError, parseRosterExport } from "../lineup-client.js";
import { useAuctionBoard } from "../use-auction-store.js";
import { Empty, RoleChip } from "../ui.jsx";

const ROLE_ORDER = ["P", "D", "C", "A"];
const byRoleThenName = (a, b) =>
  ROLE_ORDER.indexOf(a.ruolo) - ROLE_ORDER.indexOf(b.ruolo) || a.nome.localeCompare(b.nome);

const playersById = (data) => {
  const map = new Map();
  (data?.players || []).forEach((player) => map.set(Number(player.id), player));
  return map;
};

/** One row of the roster editor: indisponibile / dubbio, before the formation is computed. */
function RosterEditorRow({ player, unavailable, doubtValue, onToggleUnavailable, onDoubtChange }) {
  return (
    <div className={`row lineup-editor-row${unavailable ? " is-unavailable" : ""}`}>
      <RoleChip role={player.ruolo} />
      <span className="row-main">
        <span className="row-title">{player.nome}</span>
        <span className="row-sub">{player.squadra}</span>
      </span>
      <span className="lineup-editor-controls">
        <label className="lineup-check">
          <input type="checkbox" checked={unavailable} onChange={onToggleUnavailable} />
          Indisponibile
        </label>
        {!unavailable && (
          <label className="lineup-check">
            Dubbio
            <input
              type="number"
              className="lineup-doubt-input"
              min={0}
              max={100}
              placeholder="%"
              value={doubtValue}
              onChange={(event) => onDoubtChange(event.target.value)}
            />
          </label>
        )}
      </span>
    </div>
  );
}

/** One row of the computed report: why the model chose (or benched) this player. */
function ReportRow({ row, selectable, selected, onToggle }) {
  const reasons = [];
  if (row.avversario) reasons.push(`${row.trasferta ? "trasferta" : "casa"} vs ${row.avversario}`);
  if (row.rigorista_priorita === 1) reasons.push("rigorista designato");
  else if (row.rigorista_priorita) reasons.push(`rigorista di riserva (${row.rigorista_priorita}°)`);
  if (row.dubbio) reasons.push("in dubbio");
  const content = (
    <>
      <RoleChip role={row.ruolo} />
      <span className="row-main">
        <span className="row-title">{row.nome}</span>
        <span className="row-sub">
          {row.squadra}
          {reasons.length ? ` · ${reasons.join(" · ")}` : ""}
        </span>
      </span>
      <span className="player-metric">
        <b>{row.fantavoto_atteso?.toFixed(2)}</b>
        <small>
          {Math.round((row.p_gioca || 0) * 100)}%
          {row.incertezza != null ? ` · ±${row.incertezza.toFixed(2)}` : ""}
        </small>
      </span>
    </>
  );
  if (!selectable) return <div className="row">{content}</div>;
  return (
    <button type="button" className={`row lineup-selectable${selected ? " is-selected" : ""}`} onClick={onToggle}>
      {content}
    </button>
  );
}

export default function LineupView({ data, profile, profileId, rules, apiBase = "" }) {
  const board = useAuctionBoard(profileId, data?.players || [], rules, true);
  const myTeam = board?.teams?.[board?.userTeamIndex ?? 0];
  const byId = useMemo(() => playersById(data), [data]);

  const [source, setSource] = useState("auction");
  const [uploadedIds, setUploadedIds] = useState(null);
  const [uploadError, setUploadError] = useState("");
  const [matchdayHint, setMatchdayHint] = useState(null);
  const [giornata, setGiornata] = useState(1);
  const [unavailable, setUnavailable] = useState(() => new Set());
  const [doubtInputs, setDoubtInputs] = useState({});
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [compare, setCompare] = useState(false);
  const [chosen, setChosen] = useState(() => new Set());

  useEffect(() => {
    let active = true;
    getMatchdayStatus(profile, { apiBase })
      .then((status) => {
        if (!active) return;
        setMatchdayHint(status);
        setGiornata((current) => (current !== 1 ? current : status.giornate_mancanti?.[0] || status.prima_giornata || 1));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [apiBase, profile?.profile_id]);

  const autoIds = (myTeam?.roster || []).map((player) => Number(player.id));
  const rosterIds = source === "file" && uploadedIds ? uploadedIds : autoIds;
  const roster = rosterIds.map((id) => byId.get(id)).filter(Boolean).sort(byRoleThenName);
  const missingIds = rosterIds.filter((id) => !byId.has(id));

  const toggleUnavailable = (id) => {
    setUnavailable((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setReport(null);
  };

  const setDoubt = (id, value) => {
    setDoubtInputs((current) => ({ ...current, [id]: value }));
    setReport(null);
  };

  const handleUpload = async (file) => {
    if (!file) return;
    setUploadError("");
    try {
      const ids = parseRosterExport(await file.text());
      setUploadedIds(ids);
      setUnavailable(new Set());
      setDoubtInputs({});
      setReport(null);
    } catch (err) {
      setUploadError(err instanceof LineupClientError ? err.message : "File non valido.");
    }
  };

  const compute = async () => {
    setBusy(true);
    setError("");
    setCompare(false);
    setChosen(new Set());
    try {
      const dubbi = {};
      Object.entries(doubtInputs).forEach(([id, value]) => {
        if (unavailable.has(Number(id)) || value === "") return;
        const probability = Number(value);
        if (Number.isFinite(probability)) dubbi[id] = Math.max(0, Math.min(1, probability / 100));
      });
      const result = await buildLineup(
        profile,
        { roster: rosterIds, giornata: Number(giornata), indisponibili: [...unavailable], dubbi },
        { apiBase },
      );
      setReport(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Formazione non calcolata.");
    } finally {
      setBusy(false);
    }
  };

  const selectableRows = report ? [...report.titolari, ...report.panchina] : [];
  const chosenValue = selectableRows
    .filter((row) => chosen.has(row.id))
    .reduce((sum, row) => sum + row.p_gioca * row.fantavoto_atteso, 0);
  const toggleChosen = (id) => {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="stack">
      <div className="section-head">
        <h2>Formazione di giornata</h2>
      </div>

      <div className="card stack">
        <div className="btn-row">
          <button
            type="button"
            className={`btn btn--sm${source === "auction" ? " btn--primary" : ""}`}
            onClick={() => setSource("auction")}
          >
            La mia rosa d'asta ({autoIds.length})
          </button>
          <label className={`btn btn--sm${source === "file" ? " btn--primary" : ""}`}>
            Carica rosa esportata
            <input
              type="file"
              accept=".json,application/json"
              style={{ display: "none" }}
              onChange={(event) => {
                setSource("file");
                handleUpload(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
          </label>
        </div>
        {source === "auction" && !autoIds.length && (
          <p className="notice notice--warn">
            Nessuna rosa d'asta salvata per questo profilo in questo browser. Esporta la rosa dalla dashboard, o caricala qui.
          </p>
        )}
        {source === "file" && uploadError && <p className="notice notice--stop">{uploadError}</p>}
        {missingIds.length > 0 && (
          <p className="notice notice--warn">Id non trovati nel dataset attuale: {missingIds.join(", ")}.</p>
        )}

        <label className="field">
          <span className="field-label">Giornata</span>
          <input
            type="number"
            className="input"
            min={1}
            max={38}
            value={giornata}
            onChange={(event) => {
              setGiornata(event.target.value);
              setReport(null);
            }}
          />
          {matchdayHint && (
            <span className="field-help">
              La lega gioca dalla {matchdayHint.prima_giornata}ª alla {matchdayHint.ultima_giornata}ª giornata di Serie A.
            </span>
          )}
        </label>

        {roster.length > 0 && (
          <div className="rows lineup-editor">
            {roster.map((player) => (
              <RosterEditorRow
                key={player.id}
                player={player}
                unavailable={unavailable.has(player.id)}
                doubtValue={doubtInputs[player.id] ?? ""}
                onToggleUnavailable={() => toggleUnavailable(player.id)}
                onDoubtChange={(value) => setDoubt(player.id, value)}
              />
            ))}
          </div>
        )}

        <button type="button" className="btn btn--primary btn--block" onClick={compute} disabled={busy || !roster.length}>
          {busy ? "Calcolo in corso..." : "Calcola formazione"}
        </button>
        {error && <p className="notice notice--stop" role="alert">{error}</p>}
      </div>

      {report && (
        <div className="card stack">
          <div className="section-head">
            <h2>Giornata {report.giornata} · modulo {report.formazione}</h2>
            <span className="count">{report.valore_atteso.toFixed(1)} FP attesi</span>
          </div>

          <div>
            <p className="micro">TITOLARI</p>
            <div className="rows">
              {report.titolari.map((row) => (
                <ReportRow
                  key={row.id}
                  row={row}
                  selectable={compare}
                  selected={chosen.has(row.id)}
                  onToggle={() => toggleChosen(row.id)}
                />
              ))}
            </div>
          </div>

          {report.panchina.length > 0 && (
            <div>
              <p className="micro">PANCHINA (in ordine)</p>
              <div className="rows">
                {report.panchina.map((row) => (
                  <ReportRow
                    key={row.id}
                    row={row}
                    selectable={compare}
                    selected={chosen.has(row.id)}
                    onToggle={() => toggleChosen(row.id)}
                  />
                ))}
              </div>
            </div>
          )}

          {report.fuori_lista.length > 0 && (
            <p className="micro">
              Fuori lista per questa giornata: {report.fuori_lista.map((p) => p.nome).join(", ")}
            </p>
          )}
          {report.indisponibili.length > 0 && (
            <p className="micro">
              Indisponibili: {report.indisponibili.map((p) => p.nome).join(", ")}
            </p>
          )}

          <div className="lineup-compare">
            <button type="button" className="btn btn--sm" onClick={() => setCompare((value) => !value)}>
              {compare ? "Annulla confronto" : "Confronta con la formazione che avresti messo"}
            </button>
            {compare && (
              <p className="micro">
                Tocca i giocatori sopra per costruire la tua formazione ({chosen.size} scelti). Valore atteso
                della tua scelta: <b>{chosenValue.toFixed(1)}</b> FP contro <b>{report.valore_atteso.toFixed(1)}</b>{" "}
                del consiglio ({chosenValue - report.valore_atteso >= 0 ? "+" : ""}
                {(chosenValue - report.valore_atteso).toFixed(1)}). Il confronto è libero: non verifica che la tua
                scelta rispetti modulo e panchina.
              </p>
            )}
          </div>
        </div>
      )}

      {!report && !roster.length && (
        <Empty title="Nessuna rosa disponibile">
          Esporta la rosa dalla dashboard dopo l'asta, o caricala da file, per calcolare la formazione di una giornata.
        </Empty>
      )}
    </div>
  );
}
