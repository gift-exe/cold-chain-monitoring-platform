# E9 fix record

How each problem in `problem-log.csv` was fixed. Round 1 was iterative: problems were logged one at a time,
then discussed with the evaluator (GA) and fixed before the next was looked at, so later problems were found
on an already partly improved dashboard.

| Fix | Problems | Status |
|---|---|---|
| F01 Map view redesign and new theme | P01, P02, P03, P04, P05 | Accepted by GA (revised by F02); 91eddf4 |
| F02 Calmer summary, expandable facility rows, tidier top bar, new typeface | P06, P07, P08, P09, P10, P11 | Accepted by GA; 91eddf4 |
| F03 Incidents: list grouped by urgency, details beside the list | P12 | Applied; awaiting GA's review |
| F04 Incident details say what is wrong in plain words | P13, P14 | Applied; awaiting GA's review |

## F01: Map view redesign and new theme

**Problems:** P01 (markers do not look like facilities), P02 (no overview of the incident situation),
P03 (problem facilities do not stand out), P04 (one long mixed list), P05 (every field team listed).

**What changed**

- **Summary cards (P02).** Six cards above the map, counting only the user's area (LGA, state or nation):
  *Active incidents*, *Critical*, *Warning*, *Awaiting response* (active, not yet acknowledged),
  *Responded to* ("x of y" incidents raised in the last 7 days that were acknowledged or resolved, with the
  percentage) and *Resolved* (last 7 days). Each card opens the incidents list; *Critical* and *Warning*
  open it filtered to that severity. Same cards at every level (GA's decision). API: `summary` added to
  `GET /map` (`views.summary`), with a test.
- **Map pins (P01, P03).** Facilities are map pins with a glyph for what they are: a clinic for a health
  facility, a warehouse for an LGA or state cold store, a van for a field team. Colour shows status: red
  critical, yellow warning, green normal, grey no recent data. Critical and warning pins are larger, carry
  a status badge (▲ / ◆), are drawn on top, and critical pins pulse; normal pins are small. Status is
  never shown by colour alone (FR10.2): the badge symbol, size and the text label (on hover and for screen
  readers) carry it too. The base map is desaturated so the pins stand out.
- **Legend on the map (P01).** What each colour and each glyph means, plus two toggles: *Only facilities
  with incidents* and *Show all field teams*.
- **Side panel (P04, P05).** *Needs attention* first: only facilities and field teams with an active
  incident, critical first, each with its incidents (type, number, how long ago it started, and whether it
  is acknowledged or awaiting response; at most three, then "+ n more"). "All clear" when there are none.
  *All facilities* (with a search box) and *Field teams* follow, collapsed.
- **Field teams (P05).** Only teams with an incident are drawn on the map by default; the toggle shows them
  all, and the *Field teams* section lists them all, so T6 can still be done.
- **Facility card.** Opens on clicking a pin or a row; if the facility has active incidents, a red or
  yellow banner at the top says so, with a link to each incident.
- **Theme (GA's request).** Light, green-tinted theme in place of the dark blue top bar: white top bar with
  a green brand mark and pill-shaped navigation, rounded corners (14 px panels, 10 px controls), soft
  shadows, Inter typeface. Applies to every page.
- Incidents list accepts `?severity=critical|warning` (used by the cards) and says when it is filtered.

**Files:** `dashboard/src/views/MapView.jsx`, `dashboard/src/components/Status.jsx`,
`dashboard/src/styles.css`, `dashboard/src/views/Incidents.jsx`, `dashboard/index.html`,
`src/coldchain/api/views.py`, `tests/test_dashboard_views.py`.

**Checked:** `tests/test_dashboard_views.py` 11 passed; dashboard built and viewed as the LGA officer at
1440 × 900.

## F02: Calmer summary, expandable facility rows, tidier top bar, new typeface

**Problems:** P06 (templated summary cards), P07 (too much in each side-panel entry), P08 (pin and list
entry not linked), P09 (untidy top bar, emoji bell), P10 (plain typeface), P11 (route lines unexplained).

**What changed**

- **Summary (P06).** The six separate cards with coloured top borders and all-caps labels became one plain
  strip of six figures with sentence-case labels. Colour is used only where it carries meaning: the critical
  and warning figures turn red and yellow when above zero, and *Awaiting response* turns orange. Each figure
  still opens the incidents list.
- **Side panel rows (P07).** One short row per facility: status symbol, facility name, where it is
  ("Gwale LGA, Kano State"), and a tag saying how many incidents it has (red or yellow) or "No incidents".
  Clicking a row expands it to show the incidents (type, number, when it started, acknowledged or awaiting
  response), the facility type and power source, and each piece of equipment with its status, temperature
  and last reading; it also moves the map to the facility. Sections: *Needs attention* (facilities and
  field teams with incidents), then *Other facilities* (with search) and *Other field teams*, collapsed.
- **Pin opens its row (P08).** Clicking a pin on the map expands that facility's row, opening its section
  if collapsed, and scrolls it into view. The separate facility card was removed.
- **Top bar (P09).** Live status as a small dot and the word "Live" (or "Reconnecting"); the emoji bell
  replaced by a line icon in a rounded button matching the interface, unread count capped at "99+"; the
  user's initials, name and role grouped with *Sign out*, set apart by a divider.
- **Typeface (P10).** Figtree, a soft, open sans-serif, in place of Inter. Table headings changed from
  all caps to sentence case on every page.
- **Route lines (P11).** The legend now shows the dashed line as "Field team's route", and an expanded field
  team row says so too. The lines are the GPS track of an outreach team's logger since it set out (in the
  development stack, the demo feeder's scripted outreach trip; not the GTS data).
- API: `GET /map` facilities now carry `lga_name` and `state_name`.

- **Ward (P07, added at GA's request).** Facilities gained a `ward` (migration 0008, nullable). The Kano
  simulation network takes each facility's ward from GRID3 (an LGA store takes its host facility's ward);
  the invented demo facilities take the real ward of the nearest GRID3 facility in the same LGA. Rows read
  "Dorayi ward, Gwale LGA, Kano State", and the side-panel search matches wards. Administrators can set and
  edit the ward (administration, Facilities). Tests: ward, LGA and state on the map
  (`test_facility_officer_sees_own_facility_only`), ward edited by the administrator (`test_admin.py`).

**Not done:** facility vaccine stock is not shown because the prototype does not track stock (only
consignment quantities).

**Files:** `dashboard/src/views/MapView.jsx`, `dashboard/src/App.jsx`,
`dashboard/src/components/Notifications.jsx`, `dashboard/src/views/Admin.jsx`, `dashboard/src/styles.css`,
`dashboard/index.html`, `src/coldchain/api/views.py`, `src/coldchain/api/app.py`, `src/coldchain/admin.py`,
`src/coldchain/sim/network.py`, `src/coldchain/sim/register.py`, `migrations/versions/0008_facility_ward.py`,
`seeds/dev_z_demo_network.sql`, `tests/test_dashboard_views.py`, `tests/test_admin.py`.

**Report impact:** the ERD (Figure 3.9) and the facility entity in Chapter 3/4 should list `ward`.

**Checked:** dashboard, admin and simulator tests 57 passed, ruff clean; viewed as the LGA officer at 1440 × 900; clicking a
pin expanded its row.

## F03: Incidents grouped by urgency, details beside the list

**Problem:** P12 (opening an incident left the list; the flat list did not show what to do first).

**What changed**

- **Sections by urgency.** The *Active* tab lists incidents in three sections, most urgent first:
  *Escalated, no response yet* (red heading), *Awaiting response* (orange) and *Acknowledged, being
  handled*; within each, critical first, then oldest. *All* groups into active, awaiting review and closed.
  Each section shows its count. Empty states say what an empty list means ("Everything in your area is
  within limits").
- **Details beside the list.** Selecting an incident opens its details in a panel on the right; the list
  narrows to compact rows (severity, type and number, place, how long ago) and stays scrollable, with the
  selected row highlighted. A close button returns to the full list. The address is still
  `/incidents/<number>`, so links in SMS and notifications open the incident with the list beside it. On a
  narrow screen the details take the whole width.
- **Detail panel order.** What the user can do comes first (*Acknowledge*, *Record action*, *Resolve*; or
  *Close incident* and *Reopen* for a reviewer), then *What happened*, *Who is handling it*, *Readings* and
  *Timeline*. The place now includes the ward. Keyboard focus moves to the opened incident's title.
- With no incident open, rows show the full information (began, state, who it is with and the owner);
  without table headings, those cells are labelled in words ("Now with LGA", "Owner: ...").
- **Location map (added at GA's request).** A small map beside *What happened* shows where the incident
  is: the facility's pin (clinic or cold store, in the incident's severity colour), or for a field team the
  van at its logger's last known position with its route since setting out, and when that position was
  reported. If the logger has never reported a position, the panel says so. A map of all incidents beside
  the list was considered and not built: the Map page already shows it, and a third panel would crowd the
  list and the details.
- API: incidents carry `facility_ward`; incident details carry `location` (`views.location`), with a test.

**Files:** `dashboard/src/views/Incidents.jsx`, `dashboard/src/views/IncidentDetail.jsx`,
`dashboard/src/components/pins.js` (pins and base map shared with the map view),
`dashboard/src/views/MapView.jsx`, `dashboard/src/App.jsx`, `dashboard/src/styles.css`,
`src/coldchain/api/views.py`, `tests/test_dashboard_views.py`.

**Checked:** dashboard and incident end-to-end tests passed; viewed as the LGA officer at 1440 × 900;
selecting an incident opened its details beside the list; the location map checked on a facility incident
(#1554) and a field-team incident (#1361).

## F04: Incident details say what is wrong in plain words

**Problems:** P13 (the details listed times, condition and logger, but never said what the problem was);
P14 (decorative edge stripes on the new box and on the selected row).

**What changed**

- A box at the top of the incident details, in the incident's severity colour (grey once resolved or
  closed), states the problem in one sentence, gives the facts behind it, and says what to do. Written for
  each incident type from the incident's equipment, place, peak, times and the limits that applied, e.g.:
  - *The ice-lined refrigerator at Gwale Demo PHC 2 is too warm.* It reached 11.2 °C, above the safe limit
    of 8 °C, and has been too warm for 2 h 10 min (since 10:50). *What to do:* check the fridge and its
    power now; if it does not cool down, move the vaccines to a working fridge or cold box.
  - *Nobody has confirmed holding consignment 111.* 40 doses of BCG, going to Goron Dutse, has had no
    confirmed holder for 4 days. *What to do:* find out who has the consignment and ask them to confirm
    they received it.
  - Freezing, power off, door open, logger silent, local alarm and unusual behaviour (anomaly) have their
    own wording; ended events say how long they lasted and whether readings are back to normal, with the
    follow-up check (vaccine vial monitors after heat, shake test after freezing). Retrospective incidents
    say they were found after they had ended.
- **No edge stripes (P14).** The box has no coloured side stripe: a round severity icon (▲ critical,
  ◆ warning, ✓ resolved or closed) beside the sentence and a soft tinted background carry the colour. The
  selected incident row likewise lost its side bar; it is shown by a tinted background and a green title.
- The "ice-lined refrigerator keeps vaccines cold for some hours" hint appears only while the power has been
  off for less than 6 hours.
- API: incidents carry the consignment's product, destination and quantity.

**Files:** `dashboard/src/summary.js`, `dashboard/src/views/IncidentDetail.jsx`, `dashboard/src/styles.css`,
`src/coldchain/api/views.py`.

**Checked:** wording printed for every incident type, ongoing and ended; viewed on incident #1554 (power
off) as the LGA officer.
