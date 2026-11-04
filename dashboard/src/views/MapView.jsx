// Map view (Section 3.4.7): facilities and mobile units by status, active incidents highlighted, the
// tracks of outreach teams; each unit shows how current its data is. OpenStreetMap tiles.
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useApi } from "../api.js";
import { Status, STATUS } from "../components/Status.jsx";
import { ago, EQUIPMENT, label, temp, time } from "../format.js";

const RANK = ["critical", "warning", "stale", "ok", "no_data"];
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
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

function markerElement(status, kind, onClick) {
  const s = STATUS[status] || STATUS.no_data;
  const el = document.createElement("button");
  el.className = `marker marker-${kind} marker-${status}`;
  el.style.setProperty("--c", s.color);
  el.innerHTML = `<span aria-hidden="true">${kind === "mobile" ? "■" : s.symbol}</span>`;
  el.setAttribute("aria-label", `${s.label}`);
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick();
  });
  return el;
}

export default function MapView() {
  const { data, error } = useApi("/map", ["device_status", "incident", "consignment", "custody_handoff"], 60000);
  const [criticalOnly, setCriticalOnly] = useState(false);
  const [selected, setSelected] = useState(null);     // {kind: "facility"|"mobile", id}
  const [, tick] = useState(0);
  const mapDiv = useRef(null);
  const map = useRef(null);
  const markers = useRef([]);
  const fitted = useRef(false);

  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30000);       // keep "x min ago" current
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    map.current = new maplibregl.Map({ container: mapDiv.current, style: STYLE, center: [8.52, 11.99], zoom: 11 });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.current.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    if (import.meta.env.DEV) window.__map = map.current;     // for debugging in the browser console
    map.current.on("load", () => {
      map.current.addSource("tracks", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.current.addLayer({ id: "tracks", type: "line", source: "tracks",
                             paint: { "line-color": "#0072B2", "line-width": 3, "line-dasharray": [2, 1] } });
      map.current.fire("tracks-ready");
    });
    return () => map.current.remove();
  }, []);

  const facilities = useMemo(() => {
    const list = [...(data?.facilities || [])].sort(
      (a, b) => RANK.indexOf(a.status) - RANK.indexOf(b.status) || a.name.localeCompare(b.name));
    return criticalOnly ? list.filter((f) => f.status === "critical") : list;
  }, [data, criticalOnly]);
  const mobile = data?.mobile || [];

  // markers and tracks
  useEffect(() => {
    if (!map.current || !data) return;
    markers.current.forEach((m) => m.remove());
    markers.current = [];
    for (const f of facilities) {
      const el = markerElement(f.status, f.tier === "health_facility" ? "facility" : "store",
                               () => setSelected({ kind: "facility", id: f.id }));
      markers.current.push(new maplibregl.Marker({ element: el }).setLngLat([f.lon, f.lat]).addTo(map.current));
    }
    for (const m of mobile) {
      if (m.lon == null) continue;
      const el = markerElement(m.status, "mobile", () => setSelected({ kind: "mobile", id: m.id }));
      if (m.stale) el.classList.add("greyed");
      markers.current.push(new maplibregl.Marker({ element: el }).setLngLat([m.lon, m.lat]).addTo(map.current));
    }
    const setTracks = () => map.current.getSource("tracks")?.setData({
      type: "FeatureCollection",
      features: mobile.filter((m) => m.track.length > 1).map((m) => ({
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
  }, [data, facilities]); // eslint-disable-line react-hooks/exhaustive-deps

  const focus = (lon, lat, sel) => {
    setSelected(sel);
    if (lon != null) map.current.flyTo({ center: [lon, lat], zoom: Math.max(map.current.getZoom(), 13) });
  };

  const counts = RANK.map((s) => [s, (data?.facilities || []).filter((f) => f.status === s).length]);
  const sel = selected?.kind === "facility" ? data?.facilities.find((f) => f.id === selected.id)
    : selected?.kind === "mobile" ? mobile.find((m) => m.id === selected.id) : null;

  return (
    <div className="mapview">
      <div className="map" ref={mapDiv} role="region" aria-label="Map of facilities and mobile units" />
      <aside className="side">
        {error && <p className="error">Could not load the map data: {error.message}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && (
          <>
            <div className="legend" aria-label="Facilities by status">
              {counts.map(([s, n]) => (
                <span key={s} className="legend-item"><Status status={s} /> <b>{n}</b></span>
              ))}
            </div>
            <label className="check">
              <input type="checkbox" checked={criticalOnly} onChange={(e) => setCriticalOnly(e.target.checked)} />
              Only facilities with an active critical incident
            </label>

            {sel && selected.kind === "facility" && <FacilityCard f={sel} onClose={() => setSelected(null)} />}
            {sel && selected.kind === "mobile" && <MobileCard m={sel} onClose={() => setSelected(null)} />}

            <h2 className="side-h">Facilities ({facilities.length})</h2>
            {facilities.length === 0 && <p className="muted">None{criticalOnly ? " with an active critical incident" : ""}.</p>}
            <ul className="list">
              {facilities.map((f) => (
                <li key={f.id}>
                  <button className={`row ${selected?.id === f.id ? "selected" : ""}`}
                          onClick={() => focus(f.lon, f.lat, { kind: "facility", id: f.id })}>
                    <Status status={f.status} compact />
                    <span className="row-main">
                      <span className="row-title">{f.name}</span>
                      <span className="row-sub">{f.jurisdiction_name} · {lastHeard(f.units)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>

            <h2 className="side-h">Mobile units ({mobile.length})</h2>
            {mobile.length === 0 && <p className="muted">No loggers out on consignments.</p>}
            <ul className="list">
              {mobile.map((m) => (
                <li key={m.id}>
                  <button className={`row ${selected?.id === m.id ? "selected" : ""}`}
                          onClick={() => focus(m.lon, m.lat, { kind: "mobile", id: m.id })}>
                    <Status status={m.status} compact />
                    <span className="row-main">
                      <span className="row-title">{m.serial_no} · consignment {m.consignment_no}</span>
                      <span className="row-sub">{m.holder_name || "—"} · reported {ago(m.last_received_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </aside>
    </div>
  );
}

function lastHeard(units) {
  const last = units.map((u) => u.last_received_at).filter(Boolean).sort().at(-1);
  return units.length === 0 ? "no logger" : last ? `last reading ${ago(last)}` : "no readings yet";
}

function FacilityCard({ f, onClose }) {
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{f.name}</div>
          <div className="muted">{f.jurisdiction_name} · {label({ health_facility: "Health facility", lga_store: "LGA cold store", state_store: "State cold store" }, f.tier)} · power: {f.power_source.replace("_", " + ")}</div>
        </div>
        <button className="btn-quiet" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {f.units.length === 0 && <p className="muted">No logger at this facility.</p>}
      <table className="mini">
        <thead><tr><th>Logger</th><th>Status</th><th>Temp.</th><th>Last reading</th></tr></thead>
        <tbody>
          {f.units.map((u) => (
            <tr key={u.id}>
              <td>{u.serial_no}<div className="muted">{label(EQUIPMENT, u.equipment_type)}</div></td>
              <td><Status status={u.status} />{u.power_ok === false && <div className="flag flag-warn">Power off</div>}</td>
              <td>{temp(u.temperature)}</td>
              <td>{u.last_received_at ? <>{ago(u.last_received_at)}<div className="muted">{time(u.last_event_time)}</div></> : "never"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {f.units.some((u) => u.open_incidents > 0) && (
        <Link className="btn" to={`/incidents?facility=${f.id}`}>View active incidents</Link>
      )}
    </div>
  );
}

function MobileCard({ m, onClose }) {
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{m.serial_no} <span className="muted">({label(EQUIPMENT, m.equipment_type)})</span></div>
          <div className="muted">Consignment {m.consignment_no}: {m.product_name} → {m.destination}</div>
        </div>
        <button className="btn-quiet" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <dl className="facts">
        <dt>Status</dt><dd><Status status={m.status} /></dd>
        <dt>Held by</dt><dd>{m.holder_name || "—"}</dd>
        <dt>Last report</dt><dd>{ago(m.last_received_at)} <span className="muted">(received {time(m.last_received_at)})</span></dd>
        <dt>Last position</dt>
        <dd>{m.lat != null ? <>{m.lat.toFixed(4)}° N, {m.lon.toFixed(4)}° E <span className="muted">at {time(m.position_time)}</span></> : "unknown"}</dd>
        <dt>Temperature</dt><dd>{temp(m.temperature)}</dd>
      </dl>
      {m.stale && <p className="flag flag-warn">Offline: shown at its last known position.</p>}
      <Link className="btn" to={`/consignments/${m.consignment_id}`}>Consignment details</Link>
    </div>
  );
}
