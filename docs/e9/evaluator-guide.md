# Heuristic evaluation of the cold chain dashboard (test E9)

Thank you for taking part. This session takes about 60–90 minutes. You will work alone, using the
dashboard on the researcher's machine, and note every usability problem you find. There are no right or
wrong answers: the dashboard is being evaluated, not you.

## What the dashboard is for

The prototype monitors refrigerators and vaccine carriers in Kano State. When a fridge gets too warm or
too cold, the responsible officer is alerted by SMS and on this dashboard, and the incident is escalated
up the chain (LGA, state, national) if nobody responds. Officers use the dashboard to see what is wrong
and where, to act on incidents, to hand vaccine consignments to outreach teams, and to review how quickly
incidents are handled.

Users are cold chain officers at different levels. Each sees only their own area:

| Role | Sees |
|---|---|
| Facility officer-in-charge | Their own facility |
| LGA cold chain officer | Every facility and outreach team in the LGA |
| State cold chain officer | Every LGA in the state |
| National cold chain officer | Everything; also sets product limits |
| System administrator | System status and users' phone numbers |

## How to do the evaluation

1. Sign in as the user named for each task. The researcher will give you the sign-in details.
2. Carry out the tasks below in order. Take your time, and explore the screens around each task.
3. Whenever something makes the dashboard harder to use, record it in the problem log (one row per
   problem), even if you completed the task. Note where it happened, what the problem is, which heuristic
   it breaks, and how severe you think it is.
4. Rate severity on this scale (Nielsen Norman Group):

| Rating | Meaning |
|---|---|
| 0 | Not a usability problem |
| 1 | Cosmetic: fix only if there is time |
| 2 | Minor: low priority |
| 3 | Major: important to fix |
| 4 | Usability catastrophe: must be fixed before release |

When rating, consider how often the problem would occur, whether users can get round it, and whether it
would keep bothering them once they know about it.

## Tasks (Table 3.22)

| Task | Sign in as | What to do |
|---|---|---|
| T1 | LGA officer | Identify all facilities in the LGA with an active critical incident |
| T2 | Facility officer-in-charge | Acknowledge a new incident and record a resolution with a note |
| T3 | LGA officer | Review a resolved incident, examine its timeline and readings, and close it |
| T4 | LGA officer | Release a consignment to an outreach team |
| T5 | State officer | Find the LGA with the longest mean time to acknowledge in the past month |
| T6 | LGA officer | Determine when an outreach team's logger last reported and where |
| T7 | National officer | Change the heat tolerance duration for a product |
| T8 | Administrator | Deactivate a user's phone number |

## The ten heuristics (Nielsen Norman Group)

| Code | Heuristic | Ask yourself |
|---|---|---|
| H1 | Visibility of system status | Do I always know what is going on, and how current the data is? |
| H2 | Match between system and the real world | Does it use words and ideas a cold chain officer would use? |
| H3 | User control and freedom | Can I back out of a mistake easily? |
| H4 | Consistency and standards | Do the same things look and behave the same way throughout? |
| H5 | Error prevention | Does it stop me making mistakes before they happen? |
| H6 | Recognition rather than recall | Is what I need visible, or do I have to remember it? |
| H7 | Flexibility and efficiency of use | Can frequent tasks be done quickly? |
| H8 | Aesthetic and minimalist design | Is there irrelevant or competing information? |
| H9 | Help users recognise, diagnose and recover from errors | Are error messages plain, precise and constructive? |
| H10 | Help and documentation | Is help available where I would need it? |

## Problem log

Use the spreadsheet `problem-log.csv` (one row per problem):

| Column | What to write |
|---|---|
| evaluator | Your initials |
| task | T1–T8, or "general" |
| screen | Where it happened (e.g. "incident detail", "map") |
| problem | What the problem is, in a sentence or two |
| heuristic | H1–H10 (the main one) |
| severity | 0–4 |
| suggestion | Optional: how it might be fixed |

Please do not discuss your findings with the other evaluators until everyone has finished; the findings
are merged afterwards and each problem is given the mean of the evaluators' ratings.
