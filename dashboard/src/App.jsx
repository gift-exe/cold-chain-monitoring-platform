import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";

import { useApi } from "./api.js";
import { logout } from "./auth.js";
import Notifications from "./components/Notifications.jsx";
import { label, ROLES } from "./format.js";
import { onConnection, startLive } from "./live.js";
import Admin from "./views/Admin.jsx";
import Consignments, { ConsignmentDetail } from "./views/Consignments.jsx";
import IncidentDetail from "./views/IncidentDetail.jsx";
import Incidents from "./views/Incidents.jsx";
import MapView from "./views/MapView.jsx";
import Performance from "./views/Performance.jsx";

const OFFICERS = ["lga_officer", "state_officer", "national_officer"];

// Views and actions by role (Table 3.21)
export function sections(role) {
  if (role === "admin") return ["admin"];          // system status is on the administration page
  const s = ["map", "incidents"];
  if (role !== "outreach_lead" && role !== "driver") s.push("performance");
  if (role !== "state_officer" && role !== "national_officer") s.push("consignments");
  if (role === "national_officer") s.push("admin");
  return s;
}

const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");

const TITLES = { map: "Map", incidents: "Incidents", performance: "Performance", consignments: "Consignments",
                 admin: "Administration" };

export default function App() {
  const { data: me, error } = useApi("/me", ["notification"]);
  const [live, setLive] = useState(false);
  useEffect(() => {
    startLive();
    return onConnection(setLive);
  }, []);

  if (error) return <p className="fatal">Could not load your account: {error.message}</p>;
  if (!me) return <p className="loading">Loading…</p>;
  const nav = sections(me.role);
  const where = me.facility?.name || me.jurisdiction?.name;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">❄</span> Cold Chain Monitor
        </div>
        <nav className="nav" aria-label="Main">
          {nav.map((s) => (
            <NavLink key={s} to={`/${s}`}>{TITLES[s]}</NavLink>
          ))}
        </nav>
        <div className="topbar-right">
          <span className={`live ${live ? "on" : "off"}`}
                title={live ? "Updates arrive as they happen" : "Live updates disconnected; refreshing every minute"}>
            <span className="live-dot" aria-hidden="true" /> {live ? "Live" : "Reconnecting"}
          </span>
          <Notifications unread={me.unread} />
          <div className="user">
            <span className="avatar" aria-hidden="true">{initials(me.full_name)}</span>
            <span className="user-text">
              <span className="user-name">{me.full_name}</span>
              <span className="user-role">{label(ROLES, me.role)}{where ? `, ${where}` : ""}</span>
            </span>
            <button className="signout" onClick={logout}>Sign out</button>
          </div>
        </div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to={`/${nav[0]}`} replace />} />
          <Route path="/map" element={<MapView me={me} />} />
          <Route path="/incidents" element={<Incidents me={me} />} />
          <Route path="/incidents/:ref" element={<IncidentDetail me={me} />} />
          <Route path="/performance" element={<Performance me={me} />} />
          <Route path="/consignments" element={<Consignments me={me} />} />
          <Route path="/consignments/:id" element={<ConsignmentDetail me={me} />} />
          <Route path="/admin" element={<Admin me={me} />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}

export { OFFICERS };
