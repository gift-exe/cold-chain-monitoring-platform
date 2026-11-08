# E9 fix record

How each problem in `problem-log.csv` was fixed. Round 1 was iterative: problems were logged one at a time,
then discussed with the evaluator (GA) and fixed before the next was looked at, so later problems were found
on an already partly improved dashboard.

| Fix | Problems | Status |
|---|---|---|
| F01 Map view redesign and new theme | P01, P02, P03, P04, P05 | Applied; revised by F02 after GA's review |
| F02 Calmer summary, expandable facility rows, tidier top bar, new typeface | P06, P07, P08, P09, P10, P11 | Applied; awaiting GA's review |

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
