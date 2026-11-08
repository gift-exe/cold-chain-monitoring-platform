# E9 fix record

How each problem in `problem-log.csv` was fixed. Round 1 was iterative: problems were logged one at a time,
then discussed with the evaluator (GA) and fixed before the next was looked at, so later problems were found
on an already partly improved dashboard.

| Fix | Problems | Status |
|---|---|---|
| F01 Map view redesign and new theme | P01, P02, P03, P04, P05 | Accepted by GA (revised by F02); 91eddf4 |
| F02 Calmer summary, expandable facility rows, tidier top bar, new typeface | P06, P07, P08, P09, P10, P11 | Accepted by GA; 91eddf4 |
| F03 Incidents: list grouped by urgency, details beside the list | P12 | Accepted by GA; 7f0ab4c |
| F04 Incident details say what is wrong in plain words | P13, P14 | Accepted by GA; 7f0ab4c |
| F05 Performance page: plain headline figures, trend, ranking, view by officer | P15, P16 | Accepted by GA; 7aa6e6f |
| F06 Performance page: clearer waiting figures, time limit, comparison, rows open incidents | P17 | Accepted by GA; 7aa6e6f |
| F07 Vaccines in transit: plain names, list and details, journey, temperature, map, dialog | P18, P19, P20, P21 | Accepted by GA |
| F08 Vaccines in transit: hand over to health facilities, figures and breakdown by area | (GA's requests on F07) | Accepted by GA |
| F09 Incident and shipment lists shown 15 rows at a time | P22 | Accepted by GA |

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

## F05: Performance page: plain headline figures, trend, ranking, view by officer

**Problems:** P15 (one dense table with MTTA/MTTR jargon and no overview or charts); P16 (figures only by
place or tier, not by the people responsible).

**What changed**

- **Headline figures.** One strip, in the same style as the map summary, for the user's area and period:
  *incidents raised* (with how many critical), *average time to respond* and *average time to resolve*
  (each with "from alert to acknowledgement (MTTA)" / "(MTTR)" in small print, so the terms are explained
  where they appear and the long explanatory paragraph could go), *responded to before escalation* (the
  share acknowledged by the person responsible before the incident moved up a level) and *still waiting
  for a response* (with the longest wait, in orange when above zero).
- **Trend chart.** Average time to respond (solid green) and to resolve (dashed violet) for incidents
  raised each day (7-day period), week (30 and 90 days) or month (12 months), so a supervisor can see
  whether response is improving. Days with no incidents are gaps, not joined; hovering shows the incident
  count and both averages. The two colours pass the colour-blindness check, and the line style tells them
  apart without colour.
- **Ranking, slowest first.** Horizontal bars by LGA, facility, responsible officer or level, switchable
  between time to respond and time to resolve; each row says how many were acknowledged (resolved) and
  how many are waiting. A group that has not acknowledged any incident has no average, which would have
  ranked it best: such groups go to the top, marked *No response yet*. T5 is answered by reading the top
  of the list (state officer, by LGA). Incidents held at state or national level read "Kano State
  (state-level stores)" rather than appearing as if Kano were an LGA.
- **Responsible officer (P16).** A new grouping by the incident's owner: the person responsible when the
  incident was created, with their role and the facilities they cover. Grouped by owner, not by whoever
  acknowledged, so an officer who steps in after an escalation is not charged with the owner's delay.
  Offered to LGA, state and national officers only (the API refuses it for others), so a facility officer
  does not see colleagues' figures.
- **Full table kept, folded away.** *All figures by ...* opens the table, with plain column names and
  "none acknowledged" in place of a dash.
- **Controls.** Period as pill buttons (7 days by default, matching the map summary's "last 7 days");
  the incident mode in plain words: *Caught while happening*, *Found after they ended*, *All incidents*.
  "Tier at acknowledgement" renamed *Level that responded*.
- API: `GET /performance` returns `overall`, `trend` and `bucket`, each group's `before_escalation`, and
  accepts `group=officer` (with `role` and `covers`). Test:
  `test_response_metrics_overall_trend_and_by_responsible_officer`.

**Report impact:** FR10.3 asks for figures by tier and facility; those remain, and the officer view goes
beyond the requirement. Chapter 4 should mention the officer grouping and why it uses the owner.

**Files:** `dashboard/src/views/Performance.jsx`, `dashboard/src/styles.css`, `src/coldchain/api/views.py`,
`src/coldchain/api/app.py`, `tests/test_dashboard_views.py`.

**Checked:** dashboard view tests 13 passed, ruff clean; viewed at 1440 × 900 as the LGA officer (by
facility and by responsible officer) and as the state officer (by LGA, T5: Dala, Nassarawa and the state
stores have not responded; Gwale is the slowest of those that have, 17 min).

## F06: Performance page: clearer waiting figures, time limit, comparison, rows open incidents

**Problem:** P17 (the full table's *Waiting for a response* cell, "18, longest 4 days", packed two
figures together without saying what either was). The other changes were suggested by the facilitator
and accepted by GA while discussing P17.

**What changed**

- **Waiting figures split (P17).** The table has two columns, *Not yet acknowledged* (18) and *Oldest
  waiting* (4 days). The ranking rows and the headline say it in words: "20 not yet acknowledged, the
  oldest waiting 4 days"; "the oldest has waited 4 days".
- **Time allowed, on the trend chart.** Critical and warning incidents have very different limits
  before they move up from the person responsible (15 min and 24 h, Table 3.15), so one limit line over a
  mixed average would mislead. The chart now shows one severity at a time (*Critical*, the default,
  *Warning* or *All*) with that severity's limit as a dotted line, and a sentence under it: "A critical
  incident not acknowledged within 15 min moves up to the LGA officer. Points above the dotted line mean
  responses were too slow on average." *All* has no line and says why. The limits are read from the
  escalation policy, so they follow any change an administrator makes.
- **Compared with the period before.** Under each headline figure: "5 min faster than the previous
  7 days" (green when better, orange when worse), "12 more than the previous 7 days", "None in the
  previous 7 days" or "No figures for the previous 7 days".
- **Ranking rows open their incidents.** Selecting a row (an LGA, state, facility or officer) opens the
  incidents list filtered to it, e.g. "Showing Dala only", with *Show all* to return. Levels and
  consignments in transit are not one place or person, so their rows are not links.
- API: `GET /performance` returns `previous` (the same figures for the period before), `limits` (time
  allowed to acknowledge, by severity) and per-severity averages in `trend`; `GET /incidents` accepts
  `area` (an LGA, or a state with its LGAs) and `owner`, which narrow the user's scope and never widen it.
  Tests: `test_response_metrics_overall_trend_and_by_responsible_officer` (extended),
  `test_incident_list_narrowed_to_an_area_or_owner`.

**Files:** `dashboard/src/views/Performance.jsx`, `dashboard/src/views/Incidents.jsx`,
`dashboard/src/styles.css`, `src/coldchain/api/views.py`, `src/coldchain/api/app.py`,
`tests/test_dashboard_views.py`.

**Checked:** dashboard and admin tests 32 passed, ruff clean; viewed at 1440 × 900 as the state officer:
critical and warning trend with their limits; selecting Dala opened its 20 waiting incidents.

## F07: Vaccines in transit: plain names, list and details, journey, temperature, map, dialog

**Problems:** P18 ("consignment" is jargon), P19 (separate detail page; flat list that does not show what
needs doing), P20 (details show little), P21 (inline creation form; travel time in minutes).

**What changed**

- **Names (P18).** The page is *Vaccines in transit* (GA's choice), with a line under the title: "Vaccines
  on the move: who has them, where they are going, and how cold they are." Each consignment is a
  *shipment* ("Shipment 110: 120 doses of Pentavalent"); "batch" was avoided because on vaccines it means
  the manufacturer's lot. The word is the same everywhere a user meets it: the SMS sent to a receiver
  ("Shipment 110 from ...: reply REC 110 ..."), the SMS replies ("OK shipment 110 received"), the help
  text, incident summaries ("Nobody has confirmed holding shipment 111"), the map and the performance page.
  "Release" became *hand over* and "reject" *refuse*. The code, API and database keep "consignment", the
  term used in the report (Section 3.4.3).
- **List and details (P19).** As on the incidents page: shipments grouped by what needs doing (*Needs you*:
  handed to you to confirm, or refused and back with you; *Handed over, not yet confirmed*, with the
  confirmation deadline; *With you*; *With others*), with *In transit* and *Completed* tabs. Each row: the
  logger's status symbol, doses and vaccine, from and to, who has it, the latest temperature and how old
  it is, and tags (*Confirm receipt*, *Not confirmed*, incidents). Selecting one opens its details beside
  the list; the address is still `/consignments/<id>`.
- **Details (P20).** A plain-words box at the top in the status colour: who has it and since when (or who
  it was handed to, by when they must confirm, and who stays responsible until then; or that it was
  refused), the latest temperature against the vaccine's safe range (or the range over a completed trip),
  and open incidents. Then *What you can do* (hand over, with a note that the receiver gets an SMS code
  and the sender stays responsible until they confirm; confirm, refuse, withdraw, mark completed),
  *Where it is* (facts and a map of the last position and route, the incident map component), *Temperature
  since it set out* (chart with the too-warm and too-cold lines), *Journey* (each handover in words: "Test
  Facility OIC handed it to Test Outreach Lead (outreach team lead)") and *Incidents* (linked). T6 is
  answered in the details: the map caption gives when and where the logger last reported.
- **Dialog (P21).** *New shipment* opens a dialog over the page (keyboard focus stays in it; Escape or
  clicking outside closes it): *Leaving from*, *Vaccine*, *Number of doses*, *Going to*, *Expected travel
  time* in hours and minutes (with the 30-minute confirmation margin explained) and *Temperature logger*
  ("the device packed with the vaccines that reports their temperature and position").
- API: shipments carry the logger's latest temperature, status (as on the map), open incidents and
  severity, the holder's role and since when; details carry the location, readings over the trip and
  incidents. A completed shipment keeps its logger, readings and route.

**Not done (then done in F08):** the dashboard offered only outreach team leads and drivers as receivers,
so a health facility's officer could not receive a shipment on screen, although the custody rules allow it.

**Report impact:** say that the interface calls consignments "shipments" and the page "Vaccines in
transit", and that SMS wording changed to match (Section 3.4.6 SMS examples).

**Files:** `dashboard/src/views/Consignments.jsx`, `dashboard/src/views/IncidentDetail.jsx` (map
shared), `dashboard/src/App.jsx`, `dashboard/src/summary.js`, `dashboard/src/format.js`,
`dashboard/src/views/MapView.jsx`, `dashboard/src/views/Performance.jsx`, `dashboard/src/views/Admin.jsx`,
`dashboard/src/styles.css`, `src/coldchain/api/views.py`, `src/coldchain/notify/templates.py`,
`src/coldchain/notify/commands.py`, `tests/test_dashboard_views.py`, `tests/test_notify_e2e.py`.

**Checked:** dashboard, custody, SMS command, template, notification and admin tests and the SMS
end-to-end test (local SMS emulator) 100 passed, ruff clean; viewed at 1440 × 900 as the LGA officer:
shipment 111 (handed over, not confirmed in time), 347 (completed, refused then withdrawn) and 110 (with
the outreach lead: map with route, four days of temperature); the new shipment dialog opened and closed.

## F08: Vaccines in transit: hand over to health facilities, figures and breakdown by area

**Requests:** made by GA while reviewing F07, not separate problems: (1) a shipment must be able to go from
a cold store to a health facility and then to an outreach team; (2) the page needs its own figures, with a
breakdown by LGA for state officers and by state for national officers.

**What changed**

- **Hand over to health facilities.** The *Hand over to* list now offers health facility officers as well
  as outreach team leads and drivers, in two groups: *Health facilities* (listed by facility, e.g. "Gwale
  Test Health Facility: Test Facility OIC") and *Outreach teams and drivers*. A shipment stays one record
  however many times it changes hands: store -> facility (the officer confirms) -> outreach team (the team
  lead confirms), each handover a step in its journey. The custody rules already allowed facility officers
  to hold shipments (Section 3.4.3); only the dashboard's list left them out.
- **Figures above the list.** For the user's area: *in transit* (with how many completed in the last
  7 days), *waiting for you to confirm* (for roles that can hold shipments), *handed over, awaiting
  confirmation* (with how many were refused), *not confirmed in time* (red when above zero), *with open
  incidents* and *logger not reporting* ("temperature not being watched"). Hidden while a shipment's
  details are open, so the list keeps its width.
- **Breakdown by area.** A table of the same figures by where the shipments left from (LGA officer, folded
  by default), by LGA (state officer) or by state (national officer), with problem counts in colour and the
  areas with problems first.
- **State and national officers see the page.** It was hidden from them; they now have it for the
  overview. A national officer cannot hold shipments, so *New shipment* is not shown to them.
- API: `GET /consignments/summary` (`views.shipment_summary`); receivers include facility officers with
  their facility. Tests: `test_shipment_goes_store_to_facility_to_outreach_team_in_one_record`,
  `test_shipment_figures_and_breakdown_by_area`.

**Report impact:** Table 3.21 (views by role) should show the shipments page for state and national
officers (view only for national).

**Files:** `dashboard/src/views/Consignments.jsx`, `dashboard/src/App.jsx`, `dashboard/src/styles.css`,
`src/coldchain/api/views.py`, `src/coldchain/api/app.py`, `tests/test_dashboard_views.py`.

**Checked:** dashboard view tests 17 passed, ruff clean; viewed at 1440 × 900 as the LGA officer (figures,
breakdown by facility folded) and the state officer (figures, breakdown by LGA).

## F09: Incident and shipment lists shown 15 rows at a time

**Problem:** P22 (long lists in one scroll).

**What changed**

- Each section of the incidents list and the shipments list shows 15 rows at a time, with "16–23 of 23",
  *Previous*, *Next* and "Page 2 of 2" under it; sections with 15 or fewer rows have no controls. Sections
  page separately, so the most urgent section stays at the top.
- If the open incident or shipment is on a later page (for example, opened from an SMS link), that page is
  shown with the row highlighted. If the list gets shorter, the page moves back.
- One shared component for both pages.

**Files:** `dashboard/src/components/PagedList.jsx`, `dashboard/src/views/Incidents.jsx`,
`dashboard/src/views/Consignments.jsx`, `dashboard/src/styles.css`.

**Checked:** viewed at 1440 × 900 as the state officer: shipments page 2 (16–23 of 23); incident #763 opened
from its address, shown on page 2 of *Escalated, no response yet* with its row highlighted.
