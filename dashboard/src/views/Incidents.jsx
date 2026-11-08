// Incident view (Section 3.4.7): incidents grouped by what needs doing first, with the event time and the
// detection time both shown so delayed data does not mislead. Selecting an incident opens its details
// beside the list (E9, P12), so an officer can work down the list without losing their place; the address
// is still /incidents/<number>, so links in SMS and notifications open the incident directly.
import { useEffect, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useApi } from "../api.js";
import { Flag, Status } from "../components/Status.jsx";
import { ago, label, place, STATES, TIERS, time, TYPES } from "../format.js";
import IncidentDetail from "./IncidentDetail.jsx";

const TABS = [
  ["active", "Active"],
  ["review", "Awaiting review"],
  ["closed", "Closed"],
  ["all", "All"],
];

// Sections for each tab, most urgent first. Escalated: nobody responded in time, so it has moved up a tier.
const SECTIONS = {
  active: [
    ["escalated", "Escalated, no response yet", (i) => !i.acknowledged_at && i.state === "escalated"],
    ["awaiting", "Awaiting response", (i) => !i.acknowledged_at && i.state !== "escalated"],
    ["handled", "Acknowledged, being handled", (i) => !!i.acknowledged_at],
  ],
  review: [["review", "Resolved, awaiting review", () => true]],
  closed: [["closed", "Closed", () => true]],
  all: [
    ["active", "Active", (i) => !["resolved", "closed"].includes(i.state)],
    ["review", "Resolved, awaiting review", (i) => i.state === "resolved"],
    ["closed", "Closed", (i) => i.state === "closed"],
  ],
};
const EMPTY = { active: "No active incidents. Everything in your area is within limits.",
                review: "Nothing is waiting for review.", closed: "No closed incidents yet.", all: "No incidents yet." };

export default function Incidents() {
  const { ref } = useParams();
  const [params] = useSearchParams();
  const state = params.get("state") || "active";
  const facility = params.get("facility");
  const severity = params.get("severity");
  const area = params.get("area");
  const owner = params.get("owner");
  const name = params.get("name");   // what an area, owner or facility filter is called, from the performance page
  const q = new URLSearchParams({ state, ...(facility ? { facility } : {}), ...(severity ? { severity } : {}),
                                  ...(area ? { area } : {}), ...(owner ? { owner } : {}) });
  const { data, error } = useApi(`/incidents?${q}`, ["incident"]);
  const navigate = useNavigate();
  const qs = params.toString() ? `?${params}` : "";

  const set = (changes) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    navigate(`/incidents${ref ? `/${ref}` : ""}${p.toString() ? `?${p}` : ""}`);
  };
  const open = (no) => navigate(`/incidents/${no}${qs}`);
  const close = () => navigate(`/incidents${qs}`);

  const groups = data ? SECTIONS[state].map(([key, title, test]) => [key, title, data.filter(test)]) : [];

  return (
    <div className={`incidents ${ref ? "has-detail" : ""}`}>
      <div className="inc-pane">
        <div className="page-head">
          <h1>Incidents</h1>
          <div className="tabs" role="tablist">
            {TABS.map(([k, t]) => (
              <button key={k} role="tab" aria-selected={state === k} className={state === k ? "on" : ""}
                      onClick={() => set({ state: k === "active" ? null : k })}>{t}</button>
            ))}
          </div>
        </div>
        {(facility || severity || area || owner) && (
          <p className="filter-note">
            {(facility || area || owner) && (name ? `Showing ${owner ? "incidents owned by " : ""}${name} only. `
                                                   : facility ? "Showing one facility. " : "Showing part of your area. ")}
            {severity && `Showing ${severity} incidents only. `}
            <button className="link" onClick={() => set({ facility: null, severity: null, area: null, owner: null, name: null })}>
              Show all
            </button>
          </p>
        )}
        {error && <p className="error">Could not load incidents: {error.message}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && data.length === 0 && <p className="empty">{EMPTY[state]}</p>}
        {groups.filter(([, , list]) => list.length > 0).map(([key, title, list]) => (
          <section key={key} className={`inc-group inc-group-${key}`}>
            <h2 className="inc-group-h">{title} <span className="count">{list.length}</span></h2>
            <ul className="inc-rows">
              {list.map((i) => <Row key={i.id} i={i} selected={String(i.incident_no) === ref} onOpen={() => open(i.incident_no)} />)}
            </ul>
          </section>
        ))}
      </div>
      {ref && (
        <div className="detail-pane">
          <IncidentDetail key={ref} incidentRef={ref} onClose={close} />
        </div>
      )}
    </div>
  );
}

function Row({ i, selected, onOpen }) {
  const el = useRef(null);
  useEffect(() => {
    if (selected) el.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const temp = i.peak_value != null && ["heat_excursion", "freeze"].includes(i.type);
  return (
    <li ref={el}>
      <button className={`inc-row ${selected ? "selected" : ""}`} onClick={onOpen} aria-current={selected || undefined}>
        <span className="ir-sev"><Status status={i.severity === "critical" ? "critical" : "warning"} compact /></span>
        <span className="ir-main">
          <span className="ir-title">{label(TYPES, i.type)} <span className="ir-no">#{i.incident_no}</span></span>
          <span className="ir-where">{place(i)}, {i.jurisdiction_name}</span>
        </span>
        <span className="ir-began">
          <span>{ago(i.event_start)}</span>
          <span className="muted">began {time(i.event_start)}</span>
        </span>
        <span className="ir-col ir-state">{label(STATES, i.state)}{temp && <span className="muted">peak {Number(i.peak_value).toFixed(1)} °C</span>}</span>
        <span className="ir-col ir-with">Now with {label(TIERS, i.current_tier) === "Owner" ? "the owner" : label(TIERS, i.current_tier)}<span className="muted">Owner: {i.owner_name}</span></span>
        <span className="ir-flags">
          {i.notification_failed && <Flag tone="bad" title="An SMS about this incident could not be delivered">SMS failed</Flag>}
          {i.overdue && <Flag tone="bad">Overdue</Flag>}
          {i.mode === "retrospective" && <Flag title="The event had ended before it was detected">Retrospective</Flag>}
          {i.reopen_count > 0 && <Flag tone="warn">Reopened</Flag>}
        </span>
      </button>
    </li>
  );
}

