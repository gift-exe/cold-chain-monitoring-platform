// What is wrong, in plain words (E9, P13): the first thing an officer reads on an incident. Built from the
// incident's type, equipment, place, peak and times, and the limits that applied.
import { ago, clock, duration, time } from "./format.js";

const EQUIPMENT = {
  ice_lined_refrigerator: "ice-lined refrigerator", solar_direct_drive: "solar fridge", walk_in_cold_room: "cold room",
  vaccine_carrier: "vaccine carrier", cold_box: "cold box", freezer: "freezer", test_fridge: "fridge",
};

const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const deg = (v) => `${(Number.isInteger(Number(v)) ? String(Number(v)) : Number(v).toFixed(1)).replace("-", "−")} °C`;

// "the ice-lined refrigerator at Gwale PHC 2", "the vaccine carrier with consignment 110 (BCG, to Kofar Waika)"
function subject(inc) {
  const eq = EQUIPMENT[inc.equipment_type] || (inc.consignment_no ? "vaccine carrier" : "equipment");
  if (inc.facility_name) return `the ${eq} at ${inc.facility_name}`;
  if (inc.consignment_no) {
    const what = [inc.product_name, inc.destination && `to ${inc.destination}`].filter(Boolean).join(", ");
    return `the ${eq} with consignment ${inc.consignment_no}${what ? ` (${what})` : ""}`;
  }
  return `the ${eq} in ${inc.jurisdiction_name}`;
}

export function whatsWrong(inc, limits, now = Date.now()) {
  const on = !inc.event_end;
  const start = inc.event_start && new Date(inc.event_start);
  const mins = start ? ((inc.event_end ? new Date(inc.event_end) : now) - start) / 60000 : null;
  const dur = duration(mins);
  const since = start ? `since ${clock(start)}${mins > 20 * 60 ? ` on ${time(start).split(",")[0]}` : ""}` : "";
  const until = inc.event_end ? `until ${time(inc.event_end)}` : "";
  const back = !on && inc.condition_cleared ? " It is back to normal now." : "";
  const s = subject(inc);
  const peak = inc.peak_value != null ? deg(inc.peak_value) : null;
  const lim = limits || {};

  switch (inc.type) {
    case "heat_excursion":
      return {
        headline: `${cap(s)} ${on ? "is" : "was"} too warm.`,
        detail: `${peak ? `It reached ${peak}` : "It went"}${lim.upper_limit != null ? `, above the safe limit of ${deg(lim.upper_limit)}` : " above the safe limit"}, ${on ? `and has been too warm for ${dur} (${since}).` : `for ${dur}, ${until}.`}${back}`,
        advice: on ? "Check the fridge and its power now. If it does not cool down, move the vaccines to a working fridge or cold box."
          : "Check the vaccine vial monitors before using these vaccines, and record what was done.",
      };
    case "freeze":
      return {
        headline: `${cap(s)} ${on ? "is" : "was"} too cold: vaccines may freeze.`,
        detail: `${peak ? `It fell to ${peak}` : "It fell"}${lim.freeze_limit != null ? `, below the freezing limit of ${deg(lim.freeze_limit)}` : " below the freezing limit"}, ${on ? `and has been too cold for ${dur} (${since}).` : `for ${dur}, ${until}.`}${back}`,
        advice: on ? "Move freeze-sensitive vaccines away from the cold now and check the thermostat."
          : "Freezing can damage vaccines without any visible sign: do a shake test on freeze-sensitive vaccines before use.",
      };
    case "power_loss":
      return {
        headline: `Mains power to ${s} ${on ? "is off" : "was off"}.`,
        detail: on ? `It has been off for ${dur} (${since}).` : `It was off for ${dur}, ${until}.${back}`,
        advice: on ? `Check the power supply or generator, and watch the temperature${inc.equipment_type === "ice_lined_refrigerator" && mins < 6 * 60 ? "; an ice-lined refrigerator keeps vaccines cold for some hours without power" : " closely until power returns"}.`
          : "Check that the equipment cooled back down after the power returned.",
      };
    case "door_open":
      return {
        headline: `The door of ${s} ${on ? "is open" : "was left open"}.`,
        detail: on ? `It has been open for ${dur} (${since}).` : `It was open for ${dur}, ${until}.${back}`,
        advice: on ? "Close the door and check the temperature." : "Check the temperature stayed within limits.",
      };
    case "device_silence":
      return {
        headline: `The logger on ${s} ${on ? "has stopped reporting" : "stopped reporting for a while"}.`,
        detail: on ? `No reading has arrived ${since} (${ago(start, now)}). The equipment may be fine, but nobody can see its temperature.`
          : `No readings arrived for ${dur}; it started reporting again ${until.replace("until", "at")}.`,
        advice: on ? "Check the logger, its battery and its mobile signal." : "Check the readings from the gap once they arrive.",
      };
    case "custody_gap":
      return {
        headline: `Nobody has confirmed holding consignment ${inc.consignment_no}.`,
        detail: `${[inc.quantity && `${inc.quantity} doses of`, inc.product_name].filter(Boolean).join(" ") || "The consignment"}${inc.destination ? `, going to ${inc.destination},` : ""} ${on ? `has had no confirmed holder for ${dur} (${since}).` : `had no confirmed holder for ${dur}, ${until}.`}`,
        advice: on ? "Find out who has the consignment and ask them to confirm they received it." : "Record who held the consignment during the gap.",
      };
    case "local_alarm":
      return {
        headline: `The alarm on ${s} went off.`,
        detail: `The logger's own alarm was raised ${since.replace("since", "at")}${on ? "" : ` and cleared ${until.replace("until ", "at ")}`}.`,
        advice: "Check the equipment and its temperature.",
      };
    case "anomaly":
      return {
        headline: `${cap(s)} is behaving unusually.`,
        detail: "Its temperature pattern differs from its normal pattern, though it may still be within limits. This can be an early sign of a fault.",
        advice: "Check the equipment, its thermostat and its door seal.",
      };
    default:
      return { headline: `There is a problem with ${s}.`, detail: since ? `It began ${since}.` : "", advice: "" };
  }
}
