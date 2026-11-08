# E9 round 2: setting up for the peer evaluators

Round 2 asks two peers to work through the tasks in `evaluator-guide.md` and say where the system does
not make sense. The screens were reworked in round 1 (`fixes.md`); round 2 is mainly about whether the
workflow holds together: who is told what, when, and what they can do about it.

## Giving them access

**Recommended: share the source code** (a copy of the repository, or access to it) and let each evaluator
run the stack on their own machine. It needs Podman, `uv` and Node.js, and about 8 GB of free memory.

- Never share `coldchain/.env`: it holds your Africa's Talking key. It is not in the repository
  (`.gitignore`), and the stack makes its own from `.env.example` on first start.
- Never share the GTS tracks or anything under `runs/`.
- Use the local-SMS stack only, so that no text message can reach a real phone.

**Tunnelling from your machine** (for example with Cloudflare Tunnel or ngrok) is possible but not ready
yet. Sign-in goes through Keycloak, and the realm, the dashboard and the API all expect `localhost`
addresses. A tunnelled address would need the dashboard client's redirect URIs and web origins
(`infra/keycloak/coldchain-realm.json`), `KEYCLOAK_PUBLIC_URL` and the API's token issuer check changed to
the public address. Ask if you want to go this way.

**Simplest of all:** a screen-sharing session where they say what to click and you drive.

## Starting it (each evaluator, or you)

```bash
make reset COMPOSE="podman compose -f compose.yaml -f compose.local-sms.yaml"   # only if a stack ran before: deletes its data
make dev COMPOSE="podman compose -f compose.yaml -f compose.local-sms.yaml"
podman restart coldchain-dashboard-1
make e9-data
make demo                                          # leave it running in its own terminal
```

Open http://localhost:3000. Live incidents appear within a minute or two of starting `make demo`.

Do not run the test suite on this database. Some tests leave records behind, which would show up on the
screens.

## Accounts

One sign-in account per role. The passwords are the development test passwords in
`infra/keycloak/coldchain-realm.json`.

| Account | Role | Use it for |
|---|---|---|
| test.facility | Officer-in-charge, Gwale Test Health Facility | T2 (acknowledge and resolve); confirming a shipment |
| test.lga | LGA cold chain officer, Gwale | T1, T3, T4, T6 |
| test.state | State cold chain officer, Kano | T5 (which LGA is slowest) |
| test.national | National cold chain officer | T7 (product limits); shortening escalation times |
| test.admin | System administrator | T8 (phone numbers); the *Text messages* tab |
| test.outreach | Outreach team lead, Gwale | Receiving shipments |

The other people (officers-in-charge of the demo PHCs, the Dala, Nassarawa and Kaduna North LGA
officers, and others) have phone numbers but no sign-in. They "use SMS". An evaluator can act for them
from *Text messages* (below).

## What is in the data

`make e9-data` (`scripts/e9_data.py`) adds the following to the development seeds. Every person,
facility and number is invented; the places and wards are real.

- **Vaccines:** eight products (BCG, bOPV, Pentavalent, PCV, IPV, Measles-Rubella, HPV, Td), each
  marked damaged by freezing or not. The tolerances are the system's defaults, not checked against WHO
  guidance.
- **Places:** Kano (state store; Gwale, Dala and Nassarawa, each with an LGA store and health
  facilities), plus Kaduna (state store; Kaduna North with an LGA store and two health facilities). Every
  store and facility has a logger reporting each minute while `make demo` runs.
- **A month of incidents** (about 96), with readings and full timelines. Response times differ by LGA:
  Gwale is quick, Dala is slow and often escalated. All of them improve over the month, so the
  performance page has trends and a ranking. Two recent ones are *resolved, awaiting review* (T3).
- **Live incidents from the demo feeder:** a heat excursion at Dala Demo PHC 1, freezing at Nassarawa
  Demo PHC 2, mains power off at Gwale Demo PHC 2, and a logger that stops reporting at Gwale Demo PHC 3
  (about 20 minutes in).
- **Shipments in every state:**
  - completed journeys over the month (store to outreach team, and store to facility to outreach team,
    including one refused and then re-sent);
  - BCG at the Gwale store, ready to hand over (T4);
  - Pentavalent handed to the Gwale Test Health Facility, waiting for confirmation;
  - Pentavalent refused this morning, back with the LGA officer;
  - Measles-Rubella at the facility, ready to go on to an outreach team;
  - a Dala shipment never confirmed, so it has a custody gap incident;
  - an outreach team on the road with GPS and temperature (T6).

## Watching escalation

1. As **test.national**, go to *Administration, Escalation policy* and shorten the critical times. For
   example, *Critical incidents caught while happening*: 2 min, 5 min, 10 min. Change the longest one
   first, because each step must stay later than the one before it.
2. Start a fresh incident: `make e9-freeze`. The Gwale Test Health Facility's fridge freezes for
   40 minutes, and a critical incident appears about five minutes later, owned by **test.facility**.
   Run it again for another.
3. Leave it unacknowledged and watch it move up: it reaches test.lga, then test.state, then
   test.national. Each step shows in the incident's timeline and in each person's notifications.
   Or acknowledge it as test.facility to see escalation stop.
4. Afterwards, put the times back to Table 3.15: `make e9-restore-policy`.

## Text messages

As **test.admin**, *Administration, Text messages* shows every SMS the system sent and received. In
this test set-up, messages go to a local stand-in for the SMS gateway and never reach phones.

- *Reply as this phone* (on a message) or *Send as a phone* (at the top) sends a text to the system as if
  from that person's phone. Commands: `ACK 99`, `RES 99 KJ3X Moved the vaccines`, `REC 15 AB12`,
  `REJ 15 AB12 carrier warm`, `HELP`.
- The codes are shown in this set-up only. The database keeps them hidden, and messages sent before the
  stand-in last restarted show `****`.

## Sending them off

Give them `evaluator-guide.md` (tasks T1–T8, Nielsen's heuristics, the severity scale) and ask them to log
problems in the same format as `problem-log.csv`, or just to tell you what did not make sense. Their
findings come back to us for merging and the Chapter 5 write-up.
