// Display formats. Times are shown in West Africa Time (WAT), where the officers are.
const TZ = "Africa/Lagos";

export function time(t) {
  if (!t) return "—";
  return new Date(t).toLocaleString("en-GB", { timeZone: TZ, day: "2-digit", month: "short", hour: "2-digit",
                                               minute: "2-digit" });
}

export function clock(t) {
  return t ? new Date(t).toLocaleTimeString("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }) : "—";
}

export function duration(minutes) {
  if (minutes == null) return "—";
  const m = Math.round(minutes);
  if (m < 60) return `${m} min`;
  if (m < 48 * 60) return `${Math.floor(m / 60)} h ${m % 60 ? `${m % 60} min` : ""}`.trim();
  return `${Math.round(m / 1440)} days`;
}

export function ago(t, now = Date.now()) {
  if (!t) return "never";
  const m = (now - new Date(t).getTime()) / 60000;
  return m < 1 ? "just now" : `${duration(m)} ago`;
}

export const TYPES = {
  heat_excursion: "Heat excursion", freeze: "Freezing", power_loss: "Power loss", door_open: "Door open",
  device_silence: "Logger silent", custody_gap: "Custody gap", local_alarm: "Local alarm", anomaly: "Anomaly",
};
export const TIERS = { owner: "Owner", lga: "LGA", state: "State", national: "National" };
export const ROLES = {
  facility_oic: "Facility officer-in-charge", outreach_lead: "Outreach team lead", driver: "Driver",
  lga_officer: "LGA cold chain officer", state_officer: "State cold chain officer",
  national_officer: "National cold chain officer", admin: "System administrator",
};
export const STATES = {
  open: "Open", notified: "Notified", acknowledged: "Acknowledged", escalated: "Escalated",
  resolved: "Resolved, awaiting review", closed: "Closed",
};
export const EQUIPMENT = {
  ice_lined_refrigerator: "ILR", solar_direct_drive: "SDD fridge", walk_in_cold_room: "Cold room",
  vaccine_carrier: "Vaccine carrier", cold_box: "Cold box", freezer: "Freezer", test_fridge: "Test fridge",
};
export const CUSTODY = { held: "Held", in_handoff: "Awaiting receipt", rejected: "Rejected by receiver",
                         completed: "Completed" };

export const label = (map, key) => map[key] || (key ? key.replaceAll("_", " ") : "—");

export function place(i) {
  if (i.facility_name) return i.facility_name;
  if (i.consignment_no) return `Shipment ${i.consignment_no}`;
  return i.jurisdiction_name || "—";
}

export const temp = (t) => (t == null ? "—" : `${Number(t).toFixed(1)} °C`);
