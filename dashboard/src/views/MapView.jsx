// Map view (Section 3.4.7): the incident situation in the user's area at a glance. A summary strip on top;
// on the map, facilities as pins that look like what they are (clinic, cold store, field team), problem
// facilities large, coloured and drawn on top; beside it, one short row per facility, those needing
// attention first. A row expands to show the facility's details, and clicking a pin expands its row.
// Each unit shows how current its data is. OpenStreetMap tiles.
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useApi } from "../api.js";
import { Status, STATUS } from "../components/Status.jsx";
import { ago, EQUIPMENT, label, temp, time, TYPES } from "../format.js";

const RANK = ["critical", "warning", "stale", "ok", "no_data"];
const PROBLEM = new Set(["critical", "warning"]);
const ROUTE = "#0F766E";
const STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.55, "raster-opacity": 0.9 } }],
};

// Pin glyphs, drawn in a 20 x 20 box inside the pin's white disc.
const GLYPH = {
  facility: (c) => `<path d="M3 18V8l7-5 7 5v10z" fill="${c}"/>
                    <path d="M9 8.5h2V11h2.5v2H11v2.5H9V13H6.5v-2H9z" fill="#fff"/>`,
  store: (c) => `<path d="M2 18V7.5L10 3l8 4.5V18h-2.5v-7h-11v7z" fill="${c}"/>
                 <path d="M6 13h8M6 15.5h8" stroke="${c}" stroke-width="1.5"/>`,
  mobile: (c) => `<path d="M1 6h11v8.5H1zM12 9h3.8l3.2 3.2v2.3h-7z" fill="${c}"/>
                  <circle cx="5" cy="15.5" r="2.1" fill="${c}" stroke="#fff" stroke-width="1"/>
                  <circle cx="15" cy="15.5" r="2.1" fill="${c}" stroke="#fff" stroke-width="1"/>`,
};
const SIZE = { critical: 46, warning: 40, stale: 26, ok: 26, no_data: 24 };
const KIND_LABEL = { facility: "Health facility", store: "Cold store", mobile: "Field team" };
const TIER = { health_facility: "Health facility", lga_store: "LGA cold store", state_store: "State cold store" };

function pinSvg(status, kind, size) {
  const s = STATUS[status] || STATUS.no_data;
  const badge = PROBLEM.has(status)
    ? `<circle cx="27" cy="6" r="6" fill="#fff" stroke="${s.ink}" stroke-width="1.5"/>
       <text x="27" y="9.3" text-anchor="middle" font-size="9" font-weight="700" fill="${s.ink}">${s.symbol}</text>`
    : "";
  return `<svg width="${size}" height="${(size * 42) / 32}" viewBox="-1 -1 34 44" aria-hidden="true">
    <path d="M16 1C7.7 1 1 7.6 1 15.8c0 10.6 13.2 24 14.1 24.9a1.3 1.3 0 0 0 1.8 0C17.8 39.8 31 26.4 31 15.8 31 7.6 24.3 1 16 1z"
          fill="${s.fill}" stroke="#fff" stroke-width="2"/>
    <circle cx="16" cy="15.5" r="10.5" fill="#fff"/>
    <g transform="translate(6 5.5)">${GLYPH[kind](s.ink)}</g>${badge}
  </svg>`;
}

function markerElement(status, kind, name, onClick) {
  const s = STATUS[status] || STATUS.no_data;
  const el = document.createElement("button");
  el.className = `pin pin-${status}`;
  el.style.zIndex = String(10 - RANK.indexOf(status));         // problems drawn on top
  el.innerHTML = pinSvg(status, kind, SIZE[status] || 24);
  el.setAttribute("aria-label", `${name}: ${KIND_LABEL[kind]}, ${s.label}`);
  el.title = `${name}: ${s.label}`;
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick();
  });
  return el;
}

const kindOf = (f) => (f.tier === "health_facility" ? "facility" : "store");
const byRank = (a, b) => RANK.indexOf(a.status) - RANK.indexOf(b.status);
const where = (f) => [f.ward && `${f.ward} ward`, f.lga_name && `${f.lga_name} LGA`, f.state_name && `${f.state_name} State`]
  .filter(Boolean).join(", ")
  || f.jurisdiction_name;

export default function MapView() {
  const { data, error } = useApi("/map", ["device_status", "incident", "consignment", "custody_handoff"], 60000);
  const { data: active } = useApi("/incidents?state=active", ["incident"], 60000);
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [allTeams, setAllTeams] = useState(false);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(null);             // expanded row: "f:<id>" or "m:<id>"
  const [showOthers, setShowOthers] = useState(false);
  const [showTeams, setShowTeams] = useState(false);
  const [, tick] = useState(0);
  const mapDiv = useRef(null);
  const map = useRef(null);
  const markers = useRef([]);
  const fitted = useRef(false);
  const scrollTo = useRef(null);

  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30000);       // keep "x min ago" current
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    map.current = new maplibregl.Map({ container: mapDiv.current, style: STYLE, center: [8.52, 11.99], zoom: 11 });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    if (import.meta.env.DEV) window.__map = map.current;     // for debugging in the browser console
    map.current.on("load", () => {
      map.current.addSource("tracks", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.current.addLayer({ id: "tracks", type: "line", source: "tracks",
                             paint: { "line-color": ROUTE, "line-width": 3, "line-dasharray": [2, 1] } });
      map.current.fire("tracks-ready");
    });
    return () => map.current.remove();
  }, []);

  const facilities = useMemo(() => [...(data?.facilities || [])].sort(
    (a, b) => byRank(a, b) || a.name.localeCompare(b.name)), [data]);
  const mobile = useMemo(() => [...(data?.mobile || [])].sort(byRank), [data]);
  const shownFacilities = problemsOnly ? facilities.filter((f) => PROBLEM.has(f.status)) : facilities;
  const shownTeams = allTeams ? mobile : mobile.filter((m) => PROBLEM.has(m.status));

  const incidentsAt = useMemo(() => {
    const by = new Map();
    for (const i of active || []) {
      const key = i.facility_id ? `f:${i.facility_id}` : i.consignment_id ? `c:${i.consignment_id}` : null;
      if (key) by.set(key, [...(by.get(key) || []), i]);
    }
    return by;
  }, [active]);

  // Clicking a pin expands its row (opening its section if need be) and scrolls the row into view
  const expandFromMap = (key, section) => {
    setOpen(key);
    if (section === "others") setShowOthers(true);
    if (section === "teams") setShowTeams(true);
    scrollTo.current = key;
  };
  useEffect(() => {
    if (!scrollTo.current) return;
    document.getElementById(`row-${scrollTo.current}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    scrollTo.current = null;
  });

  // markers and tracks: normal first, problems last so they sit on top
  useEffect(() => {
    if (!map.current || !data) return;
    markers.current.forEach((m) => m.remove());
    markers.current = [];
    const add = (el, lon, lat) =>
      markers.current.push(new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat([lon, lat]).addTo(map.current));
    for (const f of [...shownFacilities].reverse()) {
      add(markerElement(f.status, kindOf(f), f.name,
                        () => expandFromMap(`f:${f.id}`, PROBLEM.has(f.status) ? "attention" : "others")), f.lon, f.lat);
    }
    for (const m of [...shownTeams].reverse()) {
      if (m.lon == null) continue;
      const el = markerElement(m.status, "mobile", `Field team, consignment ${m.consignment_no}`,
                               () => expandFromMap(`m:${m.id}`, PROBLEM.has(m.status) ? "attention" : "teams"));
      if (m.stale) el.classList.add("greyed");
      add(el, m.lon, m.lat);
    }
    const setTracks = () => map.current.getSource("tracks")?.setData({
      type: "FeatureCollection",
      features: shownTeams.filter((m) => m.track.length > 1).map((m) => ({
        type: "Feature", properties: { serial: m.serial_no }, geometry: { type: "LineString", coordinates: m.track },
      })),
    });
    if (map.current.getSource("tracks")) setTracks();
    else map.current.once("tracks-ready", setTracks);
    if (!fitted.current && (data.facilities.length || mobile.length)) {
      const pts = [...data.facilities.map((f) => [f.lon, f.lat]), ...mobile.filter((m) => m.lon != null).map((m) => [m.lon, m.lat])];
      const b = pts.reduce((bb, p) => bb.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
      map.current.fitBounds(b, { padding: 60, maxZoom: 14, duration: 0 });
      fitted.current = true;
    }
  }, [data, problemsOnly, allTeams]); // eslint-disable-line react-hooks/exhaustive-deps

  // Clicking a row toggles it, and moves the map to the place when it opens
  const toggle = (key, lon, lat) => {
    const opening = open !== key;
    setOpen(opening ? key : null);
    if (opening && lon != null) map.current.flyTo({ center: [lon, lat], zoom: Math.max(map.current.getZoom(), 13) });
  };

  const attentionFacilities = facilities.filter((f) => PROBLEM.has(f.status));
  const attentionTeams = mobile.filter((m) => PROBLEM.has(m.status));
  const q = search.trim().toLowerCase();
  const others = facilities.filter((f) => !PROBLEM.has(f.status))
    .filter((f) => !q || `${f.name} ${where(f)}`.toLowerCase().includes(q));
  const quietTeams = mobile.filter((m) => !PROBLEM.has(m.status));
  const nAttention = attentionFacilities.length + attentionTeams.length;

  const facilityRow = (f) => (
    <FacilityRow key={f.id} f={f} incidents={incidentsAt.get(`f:${f.id}`) || []} open={open === `f:${f.id}`}
                 onToggle={() => toggle(`f:${f.id}`, f.lon, f.lat)} />
  );
  const teamRow = (m) => (
    <TeamRow key={m.id} m={m} incidents={incidentsAt.get(`c:${m.consignment_id}`) || []} open={open === `m:${m.id}`}
             onToggle={() => toggle(`m:${m.id}`, m.lon, m.lat)} />
  );

  return (
    <div className="mapview">
      <SummaryStrip s={data?.summary} />
      <div className="map-wrap">
        <div className="map" ref={mapDiv} role="region" aria-label="Map of facilities and field teams" />
        <Legend problemsOnly={problemsOnly} setProblemsOnly={setProblemsOnly}
                allTeams={allTeams} setAllTeams={setAllTeams} teams={mobile.length} />
      </div>
      <aside className="side">
        {error && <p className="error">Could not load the map data: {error.message}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && (
          <>
            <h2 className="side-h">Needs attention <span className="count">{nAttention}</span></h2>
            {nAttention === 0 && (
              <p className="all-clear"><Status status="ok" compact /> All clear: no active incidents in your area.</p>
            )}
            <ul className="places">
              {attentionFacilities.map(facilityRow)}
              {attentionTeams.map(teamRow)}
            </ul>

            <details className="section" open={showOthers} onToggle={(e) => setShowOthers(e.currentTarget.open)}>
              <summary><h2 className="side-h">Other facilities <span className="count">{facilities.length - attentionFacilities.length}</span></h2></summary>
              <input className="search" type="search" placeholder="Search by facility, ward or LGA" value={search}
                     onChange={(e) => setSearch(e.target.value)} aria-label="Search facilities" />
              {others.length === 0 && <p className="muted">{q ? "No facility matches." : "None."}</p>}
              <ul className="places">{others.map(facilityRow)}</ul>
            </details>

            <details className="section" open={showTeams} onToggle={(e) => setShowTeams(e.currentTarget.open)}>
              <summary><h2 className="side-h">Other field teams <span className="count">{quietTeams.length}</span></h2></summary>
              {quietTeams.length === 0 && <p className="muted">No other field teams are out with vaccines.</p>}
              <ul className="places">{quietTeams.map(teamRow)}</ul>
            </details>
          </>
        )}
      </aside>
    </div>
  );
}

function SummaryStrip({ s }) {
  const v = (k) => (s ? s[k] : "–");
  const pct = s && s.raised ? ` (${Math.round((100 * s.responded) / s.raised)}%)` : "";
  const days = s?.days ?? 7;
  return (
    <section className="summary" aria-label="Incident summary for your area">
      <Stat to="/incidents" value={v("active")} label="Active incidents" />
      <Stat to="/incidents?severity=critical" value={v("critical")} label="Critical" status="critical"
            lit={s?.critical > 0} />
      <Stat to="/incidents?severity=warning" value={v("warning")} label="Warning" status="warning" lit={s?.warning > 0} />
      <Stat to="/incidents" value={v("awaiting")} label="Awaiting response" tone={s?.awaiting > 0 ? "attention" : ""} />
      <Stat to="/incidents?state=all" value={s ? `${s.responded}/${s.raised}` : "–"}
            label={`Responded to in the last ${days} days${pct}`} />
      <Stat to="/incidents?state=all" value={v("resolved")} label={`Resolved in the last ${days} days`} />
    </section>
  );
}

function Stat({ to, value, label: text, status, lit, tone = "" }) {
  const s = status && STATUS[status];
  return (
    <Link to={to} className={`stat ${lit ? `stat-${status}` : ""} ${tone ? `stat-${tone}` : ""}`}>
      <span className="stat-v">
        {s && <span className="stat-sym" aria-hidden="true">{s.symbol}</span>}{value}
      </span>
      <span className="stat-l">{text}</span>
    </Link>
  );
}

function Legend({ problemsOnly, setProblemsOnly, allTeams, setAllTeams, teams }) {
  return (
    <div className="map-legend" aria-label="Map legend">
      <div className="lg-row">
        {["critical", "warning", "ok", "stale"].map((s) => (
          <span key={s} className="lg-item"><span className="lg-dot" style={{ background: STATUS[s].fill }} />
            {STATUS[s].label}</span>
        ))}
      </div>
      <div className="lg-row">
        {["facility", "store", "mobile"].map((k) => (
          <span key={k} className="lg-item"><span className="lg-glyph" dangerouslySetInnerHTML={{
            __html: `<svg width="16" height="16" viewBox="0 0 20 20">${GLYPH[k]("#3d4a43")}</svg>` }} />
            {KIND_LABEL[k]}</span>
        ))}
        <span className="lg-item"><svg width="22" height="8" aria-hidden="true">
          <line x1="1" y1="4" x2="21" y2="4" stroke={ROUTE} strokeWidth="3" strokeDasharray="5 3" /></svg>
          Field team's route</span>
      </div>
      <label className="lg-check">
        <input type="checkbox" checked={problemsOnly} onChange={(e) => setProblemsOnly(e.target.checked)} />
        Only facilities with incidents
      </label>
      {teams > 0 && (
        <label className="lg-check">
          <input type="checkbox" checked={allTeams} onChange={(e) => setAllTeams(e.target.checked)} />
          Show all field teams ({teams})
        </label>
      )}
    </div>
  );
}

function IncidentTag({ incidents, status }) {
  if (incidents.length === 0) {
    return <span className={`tag tag-${status === "stale" || status === "no_data" ? "quiet" : "none"}`}>
      {status === "stale" ? "No recent data" : status === "no_data" ? "No data yet" : "No incidents"}</span>;
  }
  const worst = incidents.some((i) => i.severity === "critical") ? "critical" : "warning";
  return <span className={`tag tag-${worst}`}>{incidents.length} {incidents.length === 1 ? "incident" : "incidents"}</span>;
}

function IncidentList({ incidents, more }) {
  if (incidents.length === 0) return null;
  return (
    <ul className="inc-list">
      {incidents.map((i) => (
        <li key={i.id}>
          <Status status={i.severity === "critical" ? "critical" : "warning"} compact />
          <Link to={`/incidents/${i.incident_no}`}>{label(TYPES, i.type)}, #{i.incident_no}</Link>
          <span className="muted">started {ago(i.event_start)}</span>
          <span className={i.acknowledged_at ? "ack yes" : "ack no"}>
            {i.acknowledged_at ? `Acknowledged by ${i.acknowledged_by_name || "someone"}` : "Awaiting response"}
          </span>
        </li>
      ))}
      {more}
    </ul>
  );
}

function FacilityRow({ f, incidents, open, onToggle }) {
  return (
    <li id={`row-f:${f.id}`} className={`place ${open ? "open" : ""} place-${f.status}`}>
      <button className="place-head" onClick={onToggle} aria-expanded={open}>
        <Status status={f.status} compact />
        <span className="place-main">
          <span className="place-name">{f.name}</span>
          <span className="place-where">{where(f)}</span>
        </span>
        <IncidentTag incidents={incidents} status={f.status} />
      </button>
      {open && (
        <div className="place-body">
          <IncidentList incidents={incidents} more={incidents.length > 0 && (
            <li className="inc-more"><Link to={`/incidents?facility=${f.id}`}>Open these in the incidents list</Link></li>
          )} />
          <dl className="facts">
            <dt>Type</dt><dd>{label(TIER, f.tier)}</dd>
            <dt>Power</dt><dd>{f.power_source.replace("_", " and ")}</dd>
          </dl>
          {f.units.length === 0 ? <p className="muted">No logger at this facility.</p> : (
            <table className="mini">
              <thead><tr><th>Equipment</th><th>Status</th><th>Temp.</th><th>Last reading</th></tr></thead>
              <tbody>
                {f.units.map((u) => (
                  <tr key={u.id}>
                    <td>{label(EQUIPMENT, u.equipment_type)}<div className="muted">{u.serial_no}</div></td>
                    <td><Status status={u.status} />{u.power_ok === false && <div className="flag flag-warn">Power off</div>}</td>
                    <td>{temp(u.temperature)}</td>
                    <td>{u.last_received_at ? <>{ago(u.last_received_at)}<div className="muted">{time(u.last_event_time)}</div></> : "never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </li>
  );
}

function TeamRow({ m, incidents, open, onToggle }) {
  return (
    <li id={`row-m:${m.id}`} className={`place ${open ? "open" : ""} place-${m.status}`}>
      <button className="place-head" onClick={onToggle} aria-expanded={open}>
        <Status status={m.status} compact />
        <span className="place-main">
          <span className="place-name">Field team: {m.holder_name || "unassigned"}</span>
          <span className="place-where">Consignment {m.consignment_no} to {m.destination}</span>
        </span>
        <IncidentTag incidents={incidents} status={m.status} />
      </button>
      {open && (
        <div className="place-body">
          <IncidentList incidents={incidents} />
          <dl className="facts">
            <dt>Carrying</dt><dd>{m.product_name}</dd>
            <dt>Logger</dt><dd>{m.serial_no} ({label(EQUIPMENT, m.equipment_type)})</dd>
            <dt>Last report</dt><dd>{ago(m.last_received_at)} <span className="muted">(received {time(m.last_received_at)})</span></dd>
            <dt>Last position</dt>
            <dd>{m.lat != null ? <>{m.lat.toFixed(4)}° N, {m.lon.toFixed(4)}° E <span className="muted">at {time(m.position_time)}</span></> : "unknown"}</dd>
            <dt>Temperature</dt><dd>{temp(m.temperature)}</dd>
            <dt>Route</dt><dd>Dashed line on the map, from where the team set out</dd>
          </dl>
          {m.stale && <p className="flag flag-warn">Offline: shown at its last known position.</p>}
          <Link className="btn" to={`/consignments/${m.consignment_id}`}>Open the consignment</Link>
        </div>
      )}
    </li>
  );
}
