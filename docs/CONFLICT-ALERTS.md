# Estimated conflict display

This is a ground-display estimate from received broadcasts, not airborne TCAS, a confirmed collision course, or a manoeuvre instruction. Missing or stale reception can hide a conflict. It must not be used for navigation or separation decisions.

## Primary references checked 26 November 2026

- [FAA AC 90-120, 20 November 2024](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_90-120.pdf), section 2.7 and Table 2-3, printed page 2-5 (PDF page 10).
- [EUROCONTROL ACAS Guide, edition 5.0, April 2025](https://www.eurocontrol.int/sites/default/files/2025-09/eurocontrol-safety-acas-guide-5-0.pdf), sections 11.1–11.3 and Table 16, printed page 110 (PDF page 110).

TCAS II time thresholds depend on altitude: traffic advisories use 20–48 seconds, resolution advisories 15–35 seconds. Range and altitude tests both matter; low closure rates also use fixed distance thresholds. TCAS also has radio-altimeter-based inhibitions, tracking, filtering, coordination and manoeuvre-selection logic which this application does not implement. ACAS Xa uses different threat logic. A red estimate here does not mean an aircraft has received a TCAS RA.

| TCAS reference altitude | TA / RA seconds | TA / RA distance NM | TA / RA vertical ft |
| --- | --- | --- | --- |
| Below 1,000 ft AGL | 20 / no RA | 0.30 / no RA | 850 / no RA |
| 1,000–2,350 ft AGL | 25 / 15 | 0.33 / 0.20 | 850 / 600 |
| 2,350 ft AGL to FL50 | 30 / 20 | 0.48 / 0.35 | 850 / 600 |
| FL50–FL100 | 40 / 25 | 0.75 / 0.55 | 850 / 600 |
| FL100–FL200 | 45 / 30 | 1.00 / 0.80 | 850 / 600 |
| FL200–FL420 | 48 / 35 | 1.30 / 1.10 | 850 / 700 |
| Above FL420 | 48 / 35 | 1.30 / 1.10 | 1,200 / 800 |

The FAA table additionally shows no alerts below 500 ft AGL; EUROCONTROL's table groups the low-altitude TA threshold, with separate inhibition discussion. Neither is treated as a pressure-altitude or terrain model here.

## Application algorithm and limits

- Require airborne status, position, barometric altitude, ground speed, true ground track and barometric vertical rate, each timestamped no more than five seconds ago. Do not assume level flight when vertical rate is missing. Do not mix GNSS and pressure altitude. Unknown, invalid, stale and ground traffic is counted as unassessed, never as clear.
- Align positions and altitudes to the evaluation time using at most five seconds of constant-velocity extrapolation. Compare relative horizontal motion in a common local tangent plane and vertical motion in feet/second.
- Position extrapolation follows a great circle and transports its track direction across the poles; pressure altitude uses its own timestamp. Inputs outside the estimator's supported bounds (ground speed 0–1,500 kt, altitude −2,000–100,000 ft, vertical rate ±20,000 ft/min) are unassessed. An Earth-centred spatial grid rejects distant pairs before local projection, including opposite sides of the world.
- Predict simultaneous entry into horizontal and vertical proximity limits over the published time windows. This is a simplified protected-volume calculation; it does not reproduce TCAS tau, modified tau, filters, radio-altimeter inhibitions or RA guidance. Parallel traffic outside the limits and crossing routes at different times/heights do not trigger.
- Use the higher pressure-altitude band of the pair. Because height above ground is unavailable, below 5,000 ft pressure altitude use a documented fallback of 25/15 seconds, 0.33/0.20 NM and 850/600 ft. This is an application choice, not a TCAS low-altitude mode. Above that, use the table's pressure-altitude bands, with boundaries at 5,000, 10,000, 20,000 and above 42,000 ft.
- Yellow means the estimated pair meets the yellow window; red means it meets the shorter/tighter red window. Already-close pairs can enter red immediately. Both aircraft are marked. Multiple encounters use the highest current level for each aircraft. Received emergency states retain red priority. Estimates clear when geometry or data freshness no longer qualifies.
- Yellow and red windows are solved independently. The displayed time is estimated entry into that level's simultaneous proximity limits; displayed horizontal/vertical separations are at that entry time, not a guaranteed closest approach or collision time.
- Evaluate all received aircraft before display filtering. A hidden partner can still cause a visible aircraft warning. No received values, external feeds, existing alert rules, or history records are modified by these estimates.
- Default on, with independent enable and flashing preferences. Disabled flashing produces steady colour. Labels and hover/details text identify the other aircraft, estimated time and predicted separation. No absence-of-conflict safety claim is shown.
