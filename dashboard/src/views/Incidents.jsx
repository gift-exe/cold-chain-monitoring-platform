// Incident view (Section 3.4.7): incidents by severity and age, with the event time and the detection
// time both shown so delayed data does not mislead.
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useApi } from "../api.js";
import { Flag, Severity } from "../components/Status.jsx";
import { ago, label, place, STATES, TIERS, time, TYPES } from "../format.js";

const TABS = [
  ["active", "Active"],
  ["review", "Awaiting review"],
  ["closed", "Closed"],
  ["all", "All"],
];

export default function Incidents() {
  const [params, setParams] = useSearchParams();
  const state = params.get("state") || "active";
  const facility = params.get("facility");
  const q = new URLSearchParams({ state, ...(facility ? { facility } : {}) });
  const { data, error } = useApi(`/incidents?${q}`, ["incident"]);
  const navigate = useNavigate();

  const set = (k, v) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    setParams(p);
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Incidents</h1>
        <div className="tabs" role="tablist">
          {TABS.map(([k, t]) => (
            <button key={k} role="tab" aria-selected={state === k} className={state === k ? "on" : ""}
                    onClick={() => set("state", k === "active" ? null : k)}>{t}</button>
          ))}
        </div>
      </div>
      {facility && (
        <p className="filter-note">
          Showing one facility. <button className="link" onClick={() => set("facility", null)}>Show all</button>
        </p>
      )}
      {error && <p className="error">Could not load incidents: {error.message}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.length === 0 && <p className="empty">No incidents here.</p>}
      {data && data.length > 0 && (
        <div className="table-wrap"><table className="table">
          <thead>
            <tr>
              <th>Severity</th><th>No.</th><th>What</th><th>Where</th><th>Began</th><th>Detected</th>
              <th>State</th><th>With</th><th>Owner</th><th></th>
            </tr>
          </thead>
          <tbody>
            {data.map((i) => (
              <tr key={i.id} className="clickable" onClick={() => navigate(`/incidents/${i.incident_no}`)}>
                <td><Severity severity={i.severity} /></td>
                <td><Link to={`/incidents/${i.incident_no}`} onClick={(e) => e.stopPropagation()}>#{i.incident_no}</Link></td>
                <td>{label(TYPES, i.type)}{i.peak_value != null && ["heat_excursion", "freeze"].includes(i.type) && <span className="muted"> · peak {Number(i.peak_value).toFixed(1)} °C</span>}</td>
                <td>{place(i)}<div className="muted">{i.jurisdiction_name}</div></td>
                <td>{time(i.event_start)}<div className="muted">{ago(i.event_start)}</div></td>
                <td>{time(i.detected_at)}</td>
                <td>{label(STATES, i.state)}</td>
                <td>{label(TIERS, i.current_tier)}</td>
                <td>{i.owner_name}</td>
                <td className="flags">
                  {i.notification_failed && <Flag tone="bad" title="An SMS about this incident could not be delivered">SMS failed</Flag>}
                  {i.overdue && <Flag tone="bad">Overdue</Flag>}
                  {i.mode === "retrospective" && <Flag title="The event had ended before it was detected">Retrospective</Flag>}
                  {i.reopen_count > 0 && <Flag tone="warn">Reopened</Flag>}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
