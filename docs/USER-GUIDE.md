# FlightRadar24 user guide

Version 0.9.0 · Kieran O'Rourke

All example aircraft, coordinates and delivery addresses are dummy data. Practice never changes your live profile.

- [Your first reception](#start)
- [Supported USB devices and drivers](#hardware)
- [USB, network, serial and recording inputs](#inputs)
- [Gain, sample rate and receiver tuning](#tuning)
- [Antenna location, altitude and range](#location)
- [Map, rings and the aircraft list](#map)
- [Read the aircraft details panel](#details)
- [Create and save aircraft filters](#filters)
- [Watch an aircraft or an emergency code](#alerts)
- [Sound and desktop notifications](#local-alerts)
- [Send an email alert](#email)
- [Phone, ntfy, Discord and webhooks](#phone)
- [Quiet hours, cooldowns and delivery status](#quiet)
- [What an alert contains](#alert-content)
- [Understand the Statistics screen](#statistics)
- [Airports and proximity counts](#airports)
- [Limit history and log file growth](#storage)
- [Export aircraft and statistics](#export)
- [Register and upload to Flightradar24](#sharing)
- [MLAT status and verification](#mlat)
- [RAW, SBS and Beast data outputs](#outputs)
- [Startup, stale aircraft and reconnects](#operation)
- [Import and export INI settings](#import)
- [Recorded playback and dummy practice](#playback)
- [Practice with prefilled dummy data](#practice)
- [No aircraft, sparse reception or blank map](#reception)
- [Logs, diagnostics and requesting support](#logs)
- [Profiles, backups and upgrades](#backup)
- [Privacy, credentials and network access](#privacy)
- [Glossary and units](#glossary)
- [About, licence and project](#about)
- [Software updates from GitHub](#updates)
- [Aviation waypoints on the map](#waypoints)

<a id="start"></a>
## Your first reception

FlightRadar24 is an independent native desktop app for receiving nearby 1090 MHz ADS-B and Mode S traffic, inspecting broadcast data and optionally feeding Flightradar24. It runs in its own window.

### Start here

1. Connect a supported RTL-SDR USB receiver and a suitable 1090 MHz antenna. Keep the antenna clear of obstructions where practical.
2. On Windows, run the complete installer if the receiver needs its WinUSB driver. Open FlightRadar24.
3. Choose Receiver & settings → Receiver. Set Input type to USB RTL-SDR · 1090 MHz. Scan USB receivers and select the decoder index, normally 0.
4. Enter your own antenna coordinates and altitude if known, then Save receiver settings. The altitude field is in feet above mean sea level: metres × 3.28084.
5. Select Start receiver. Watch accepted messages and the aircraft table in Airspace. A map marker appears only after a position has been decoded.
6. Use Support → Practice with dummy data to explore without a receiver. Close the app normally to stop its receiver and feeder.

### What to expect

Radio range depends on antenna, placement, local terrain, aircraft and interference. A connected device with no messages does not establish a software failure. The program cannot supply worldwide aircraft from the internet; its map works worldwide and shows traffic from your selected source.

Uploading is optional. Your sharing key is unrelated to an API subscription. No route, registration or aircraft-photo service is required.

<a id="hardware"></a>
## Supported USB devices and drivers

The intended receiver family is RTL2832U RTL-SDR with a suitable tuner for 1090 MHz. Compatibility and observed hardware tests are different.

### Hardware support

| Setting | Example / value | Meaning |
| --- | --- | --- |
| RTL-SDR Blog V4 | Live reception tested on Windows x64 | This is the physically tested USB receiver. |
| RTL2832U with R820T / R820T2 | Supported family; other models unverified | RTL-SDR Blog V3 and generic dongles need their own device/tuner check. |
| Installer USB IDs | 0BDA:2832, 0BDA:2838; SDR interface 0 | Automatic WinUSB installation is restricted to these IDs. It leaves composite parents and secondary interfaces alone. |
| Other radios | Beast / AVR / decoded SBS input | A network or serial source can supply data even when its USB radio is unsupported. |

### Windows driver setup

1. Close other software using the dongle. Connect it directly to a reliable USB port.
2. Run the complete installer. It checks supported devices and preserves a working WinUSB binding; Windows may ask for administrator or driver confirmation.
3. The helper uses Windows' inbox WinUSB driver. Its device-specific catalogue is locally signed, not WHQL certified.
4. Open the app and Scan USB receivers again. The portable app does not install drivers; rerun setup when a supported device needs provisioning.

### Limits

A device appearing in a USB list is not proof of usable reception. Airspy, HackRF, SDRplay, 978 MHz UAT and Kinetic proprietary binary modes are not supported USB paths in this application.

Windows x64 is the tested platform. Linux x86-64 has source/build support but no hardware/runtime verification in this project. macOS is a future option.

<a id="inputs"></a>
## USB, network, serial and recording inputs

Stop reception before changing an input. Save the settings and start again. Only one configured input is active at a time.

### Choose a source

| Setting | Example / value | Meaning |
| --- | --- | --- |
| USB RTL-SDR | Decoder index 0 | First receiver enumerated by the decoder, not a Windows port number. |
| Beast TCP | 127.0.0.1 : 30005 | Timestamped binary stream. Use the actual source host/port. |
| AVR TCP | 127.0.0.1 : 30002 | Raw hexadecimal messages. Plain AVR without sample timestamps cannot provide MLAT timing. |
| SBS TCP | 127.0.0.1 : 30003 | Decoded BaseStation reports; supports local display and SBS output, not raw reconstruction or uploading. |
| Beast / AVR serial | COM3 on Windows; /dev/ttyUSB0 on Linux | Match the radio's baud rate: 57600, 115200, 921600, 1000000 or 3000000. |
| Recorded I/Q file | Unsigned 8-bit interleaved I/Q .uc8 | Choose the file and original sample rate. Recorded playback cannot be uploaded as live traffic. |

### Practical checks

Host fields take a hostname or IP address without http://. The input TCP port can be 1–65535. If you use a remote source, configure its host rather than leaving the example loopback address.

Windows accepts 2.0 or 2.4 MHz USB/IQ sample rates. Linux's decoder requires 2.4 MHz. Windows playback runs at decoder processing speed; Linux playback is paced. Playback statistics are kept separate from live statistics.

<a id="tuning"></a>
## Gain, sample rate and receiver tuning

Begin with defaults, change one setting at a time, and compare accepted traffic over similar periods. More gain does not always produce better reception.

### Tuning reference

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Tuner gain | −10 dB | Automatic gain sentinel; manual gain accepts 0–60 dB. |
| Frequency correction | 0 PPM | Offset correction from −1000 to 1000 PPM; tune only with evidence. |
| Digital automatic gain | Off | Digital AGC is separate from tuner automatic gain. |
| Bias tee power | Off | Enable only if the attached active antenna or amplifier requires it and the radio supports it. |
| DC filter | On | Decoder DC filtering. |
| Phase enhancement | Off | Optional decoder enhancement. |
| Bit error correction | 1 bit | Off, 1 bit, or up to 2 bits. Correction is not proof of message authenticity. |
| Sample rate | 2.4 MHz | Match the decoder/input. A recording must use the rate at which it was captured. |

### Apply changes

1. While receiving, keep a complete 60-second baseline on the Receiver tab. Change one setting at a time and compare similar traffic over several minutes. Accepted Mode S per second, app rejects, corrections and strong-frame share describe received messages, not all transmissions. Frame level in dBFS is not RF SNR or a clipping measurement. Missing measurements are unavailable, not zero.
2. Stop receiver, change the Receiver tab values and Save receiver settings.
3. Start receiver and compare messages, rejects and range. Check Logs & diagnostics if the decoder rejects an option.
4. Windows USB bandwidth: leave at 0 for driver default, or request 300–3200 kHz. A narrow filter can distort pulses. Phase enhancement applies at 2.0 MHz only. Windows 2.4 MHz provides frame strength and correction counts; Beast may supply strength, while standard AVR and SBS have no RF levels. Network and serial receivers must be tuned at the source.
5. Mode A/C replies have neither an aircraft address nor a position; they are counted but never assigned to a plane. GPS and altimeter readings are available only through transmitted ADS-B or Mode S fields. No filed route, 978 MHz UAT, 1030 MHz interrogations or direct sensor data are obtained from a 1090 MHz receiver.
6. For a private rotating archive set Local message archive above zero (MiB). The message-capture folder in the application data directory contains JSON Lines arrays: Unix reception time, input, message type, hex payload, receiver clock, signal byte and corrected bits. Null means not provided. Anonymous replies append possible Mode A code and, only for valid Gillham patterns, an alternative Mode C pressure altitude; neither is an identified flight. SBS stores original line bytes including the terminator. Capture precedes app validation but follows radio demodulation. Oldest segments expire; dropped entries and disk errors are reported. No archive is uploaded.
7. Try better antenna placement before assuming a tuning change can compensate for an unsuitable antenna.

<a id="location"></a>
## Antenna location, altitude and range

Your receiver location enables local range calculations and a useful map centre. Enter the antenna location, not an aircraft or an airport location.

### Example only

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Latitude | 0.000000 | Dummy point in the Atlantic. South is negative; valid range −90 to 90. |
| Longitude | −30.000000 | Dummy point. West is negative; valid range −180 to 180. |
| Antenna altitude AMSL | 100 feet | Dummy altitude above mean sea level, not height above ground. Metres × 3.28084 = feet. |
| Maximum position range | 300 NM | Local position plausibility/display range; 1–21600 NM. It does not improve radio sensitivity. |

### Use your own values

Enter both latitude and longitude or leave both blank. The map starts globally when no station is configured. Distance filters cannot match when a station or fresh aircraft position is missing.

Use the same accurate antenna location and altitude for the official feeder registration. MLAT also depends on timestamp quality, surrounding stations and vendor permission.

<a id="map"></a>
## Map, rings and the aircraft list

Airspace is a near-full-screen worldwide map. Reception and sharing status run across the very top bar, the aircraft list floats in a box along the bottom spanning the map, clicking a plane pops its live details up in a compact floating box at the left, and map attribution sits in the bottom bar at the right. Aircraft with no decoded position still appear in the list.

### Explore your airspace

1. Drag the map to pan. Double-click to zoom in one level around that point; use the +/− overlay buttons or wheel to adjust further.
2. Select an aircraft marker or list row and its live details pop up in a compact floating box at the left (covering the map there on narrow windows); close it with its × button. Double-click a row to centre that aircraft.
3. Use the antenna button (◎) to return to your receiver location, or the fit button (⛶) to include current received positions.
4. Collapse the bottom aircraft box to a slim bar with its ▾ button (or View → Aircraft list) whenever you want the map completely clear; it reopens the same way.
5. Open Filters to limit displayed traffic. Clear filters to restore all received aircraft.
6. Use Find (Ctrl+F) to jump to a callsign or ICAO address without changing your saved filters. Click a column heading to sort the list; aircraft missing that value stay at the bottom.
7. Distance, bearing and the dotted rings are measured from your antenna latitude/longitude; rings step every 50 nautical miles (or kilometres in metric display). Leave the station position unset and those stay blank rather than guessing.
8. An aircraft broadcasting an emergency squawk (7500, 7600 or 7700) or transponder emergency state flashes red on the map with a pulsing ring, and its list row is red. Display & units can switch the flashing off for a steady red marker. It is shown as received and is not independently verified.
9. Window size and layout are remembered for next time. Close the details panel to regain space.

### Identify aircraft silhouettes

Shapes and sizes follow the received ADS-B emitter category: light, small, large, high-vortex, heavy, high-performance, helicopter/rotorcraft, glider, lighter-than-air, parachutist, ultralight, uncrewed and space/transatmospheric craft. Surface vehicles and ground obstacles have distinct symbols too.

Open View → Aircraft symbol guide for the visual legend, or hover over a marker to see its category. Unknown, missing or reserved categories use a directional marker. A category is not an exact model, engine type or true-to-scale size. The receiver does not infer a helicopter from its callsign or speed.

Silhouettes turn with received ground track. Balloons, parachutists and obstacles stay upright. Selected aircraft stay amber; emergency and estimated-conflict colours and flashing apply to every silhouette.

### Use two monitors

1. Choose Detach in the Aircraft header, or View → Detach Aircraft table. The same live table opens in a separate resizable window, freeing the map's bottom edge.
2. Drag its title bar to another monitor, or choose Move to monitor and select the display. Maximise it normally, or use Full screen (F11). On the main map window, View → Full screen or F11 works independently, so both displays can run full screen. Esc exits full screen in the active window.
3. Find, filters, sorting and aircraft selection stay synchronised with the map. Selecting a row opens the same compact details popup on the map's left side. Ctrl+F focuses Find in the table window.
4. Use Dock back or close the Aircraft window to return the table to its floating box along the bottom. Closing the main application closes both windows and stops the receiver normally.
5. The separate window's position, size and full-screen state are remembered when the application closes. If its saved monitor is missing at startup, the table stays docked. Disconnecting a monitor while the app is open also docks the table so it remains accessible.

### Internet and freshness

OpenStreetMap background tiles require internet access and have a separate 64 MiB cache. Attribution stays visible in the bottom bar at the right. The tile provider sees map-area requests and your IP address, not your aircraft payload or sharing key.

Range rings, the compass and aircraft positions work without map tiles. Track lines are recent session observations, not a reconstructed full route. Stale or missing positions are not current aircraft locations.

### Choose aircraft labels and hover details

1. Open View → Map labels & conflict display, or Receiver & settings → Storage & alerts → Display & units.
2. Choose Custom map labels. Tick callsign, ICAO, altitude, ground speed, track, vertical rate, squawk, antenna distance and last-seen age individually. Save display settings applies only display preferences. Callsign/detailed/hidden presets remain available.
3. Hover over an aircraft or its label to see more available broadcast information, position, measurement source and age. Click opens the full details panel. Hover can be switched off independently. Missing registration, routes or other database information is not invented.

### Yellow and red estimated conflicts

Both aircraft flash yellow for a predicted close approach and red for the shorter, tighter proximity window. Emergency broadcasts retain red priority. Table rows and the selected details panel describe the estimate. The enable and flashing switches are independent; flashing off leaves a steady warning colour.

Estimates require airborne status, coordinates, barometric altitude, ground speed, true track and barometric vertical rate no more than five seconds old. Both horizontal and vertical proximity must occur at the same time. Missing, stale and ground observations count as unassessed, not safe. All received aircraft are checked, including partners hidden by filters.

TCAS II reference thresholds vary with altitude: caution timing is 20–48 seconds and resolution advisory timing is 15–35 seconds. This app uses a simplified proximity prediction, not TCAS tracking or advisory logic. Below 5,000 ft pressure altitude it uses a documented 25/15-second fallback because height above terrain is unavailable. These ground-display estimates must not be used for navigation or separation decisions and give no manoeuvre instructions. See docs/CONFLICT-ALERTS.md for the complete thresholds and FAA/EUROCONTROL sources.

### Display units, labels, rings and north reference

Receiver & settings → Storage & alerts → Display & units chooses aviation units (feet, knots, nautical miles) or metric conversions (metres, km/h, kilometres), map label detail, station range rings and keeping the selected aircraft centred. These choices affect presentation only; reception, uploading, alerts and history keep broadcast values.

Altitude at or above 10,000 ft is also shown as an approximate flight level, for example 35,000 ft · FL350. The receiver cannot confirm the pressure setting, so treat FL as shorthand. Metric values are converted from the broadcast feet and knots, not re-measured. Up/down arrows summarise the broadcast vertical rate; a level arrow covers rates within ±100 ft/min.

The compass and the N/E/S/W labels on the rings follow your chosen north reference. The background map itself is drawn with true north up. For magnetic bearings, enter the current magnetic variation for your antenna location from a chart (degrees, east positive); the compass, cardinals and table bearings then read in degrees magnetic (°M). The app does not calculate variation itself.

<a id="details"></a>
## Read the aircraft details panel

Select an aircraft to inspect what the transmission actually contains. The details panel updates while that aircraft is being received and shows measurement ages.

### Identity and motion

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Callsign / ICAO24 | DEMO101 / A00001 | Dummy identifiers. A callsign is transmitted text; ICAO24 is the aircraft's 24-bit address. |
| Altitude | 6500 ft | Barometric and GNSS values are separate when available. |
| Ground speed / track | 145 kt / 075° | Movement over the ground; not necessarily airspeed or heading. |
| Vertical rate | +320 ft/min | Positive is climbing; negative is descending. |
| Squawk | 1200 | Four octal digits. Special codes include 7500, 7600 and 7700. |
| Emitter category | Light | Broadcast category, not make or model. |

### More broadcast fields

Expand the detail groups for available barometric/GNSS measurements, indicated/true airspeed, headings, selected altitude/heading, emergency state, integrity and capability fields. Data source and ages help distinguish fresh measurements from old observations.

Recent altitude and speed charts use only received session history and show gaps. Unknown values stay unavailable; lost reception is marked.

These transmissions do not supply an airline database, registration, model, aircraft age, photo, route, scheduled times or confirmed departure/destination. None is invented. Not all aircraft transmit every supported field.

<a id="filters"></a>
## Create and save aircraft filters

Display filters affect the map, radar, table and current-aircraft CSV. Reception, uploading, statistics and alert evaluation still use all received traffic.

### Build a filter

1. Open Airspace → Filters. Set only the conditions you need; blank numeric values mean any.
2. For the dummy example, set Callsigns to DEMO* and Maximum altitude to 10000 feet. In Practice, only DEMO101 matches.
3. Optionally name the preset, such as Low demo aircraft, and Apply. Select saved presets from the Airspace list.
4. Use Clear filters to show all aircraft. The Filters dialog can remove a named preset.

### Matching rules

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Callsigns | DEMO*, SAMPLE3 | Case-insensitive exact values or a final * prefix wildcard; comma-separated alternatives. |
| ICAO24 addresses | A00001, A00002 | Exact six hexadecimal digits. No partial ICAO wildcard. |
| Squawks | 1200, 7700 | Exactly four octal digits each. |
| Altitude / ground speed | 0–10000 ft / any kt | Optional minimum and maximum, inclusive. Missing/expired values do not match. |
| Antenna distance | 50 NM | Requires receiver coordinates and a fresh aircraft position. |
| Category / source / flight state | Light / ADS-B / airborne | Category uses received text; source and state use their selected values. |
| Position / emergency | Fresh position; emergency only | Emergency matches special squawks or decoded emergency states. |

### Why nothing matches

All different fields must match together (AND). Values inside each code list are alternatives (OR). A narrow combination, unknown measurement or stale position can exclude every aircraft. Clear one condition at a time. Up to 20 presets can be saved.

<a id="alerts"></a>
## Watch an aircraft or an emergency code

Alerts run while the app and receiver are running. The master switch, individual rule and channel settings all matter.

### Create your first rule

1. Open Alerts → Add rule, or select an aircraft in Airspace and choose Alert for selected aircraft.
2. Name the rule. Enter an exact ICAO24 address for one aircraft, or use callsign and other filter fields for a group.
3. Choose In-app history, Sound and/or Desktop for local delivery. Email and Webhook also require configured destinations in Storage & alerts.
4. Set Minimum repeat interval (30–86400 seconds), save the enabled rule, then enable the Alerts master switch.
5. Use the emergency preset to watch decoded emergency states and squawks 7500, 7600 or 7700. Review the event history and delivery status.

### When rules fire

A rule fires on first match or re-entry after a non-match/reception gap, subject to its cooldown. A continuously matching aircraft does not alert on every message. Retained cooldowns survive restarts. A global limit of 20 events per minute and bounded delivery queues reduce bursts; suppressed/dropped counts are shown.

Up to 50 rules can be saved. Playback requires an explicit per-rule opt-in and is labelled. Quiet hours keep events in history but silence delivery. Notifications are for hobby monitoring, not aviation safety or emergency response.

<a id="local-alerts"></a>
## Sound and desktop notifications

Local alerts use the system chime and the operating system's native tray notification facility. In-app history remains available when the desktop suppresses a notification.

### Try a local notification

1. Open Receiver & settings → Storage & alerts and choose the local sound/desktop test.
2. Check speaker volume, system sounds and your operating system's notification settings if the chime or message does not appear.
3. For real events, enable the master alert switch and choose Sound or Desktop on the matching rule.
4. Click a delivered desktop notification to open Alerts and inspect the retained event.

### Interpret the result

The local test requests a chime and notification; it does not send email or webhooks. A successful request does not certify that Windows displayed a toast or that you heard the sound. Quiet/focus modes and Linux desktop tray support can change the result.

<a id="email"></a>
## Send an email alert

Email uses your own SMTP provider and verified TLS. The app adds no subscription, but your provider may impose limits or charges. The registration email is not automatically an alert recipient.

### Configure delivery

1. Open Receiver & settings → Storage & alerts. Enter the SMTP server, port, username, sender, recipient and provider-issued password or app password.
2. Choose STARTTLS (commonly port 587) or implicit TLS (commonly port 465) to match the provider. OAuth-only providers need a compatible relay.
3. Enable email delivery and save. Choose Email on the relevant alert rule and enable alerts.
4. The external test button sends a clearly labelled synthetic message to saved enabled destinations. Only use it once you intend to contact those destinations.
5. Review the delivery result and recipient inbox. Service acceptance is not proof that the message was read or delivered to the inbox.

### Dummy form example

| Setting | Example / value | Meaning |
| --- | --- | --- |
| SMTP host / port | smtp.example.invalid / 587 | Reserved, deliberately non-deliverable example; replace with your provider. |
| Username / sender | receiver@example.invalid | Dummy account, not a usable credential. |
| Recipient | observer@example.invalid | Dummy address. Choose your real recipient locally. |
| Password | Leave empty in practice | Never post credentials in an issue or screenshot. |

### Secret handling

Windows secrets are encrypted for the Windows user with DPAPI; Linux uses owner-only files. Leaving a secret field blank preserves the stored secret. SMTP details are separate from the Flightradar24 sharing key. Delivery failures are recorded without automatic retries that could duplicate messages.

<a id="phone"></a>
## Phone, ntfy, Discord and webhooks

HTTPS delivery can use an ntfy topic, a Discord webhook, or a generic JSON endpoint. Some providers offer free tiers or self-hosting; mobile delivery and quotas depend on the provider and phone.

### Set up a destination

1. Create or choose a destination using that service's own instructions. Subscribe on your phone when using ntfy.
2. In Storage & alerts, choose the webhook type, enter the HTTPS URL and optional bearer token, enable delivery and save.
3. Choose Webhook / phone on the matching rule. Use the external test only when you want to send a synthetic event to saved destinations.
4. Check provider acceptance and the phone/channel itself. A phone can suppress background notifications even when the server accepts the event.

### Payload and privacy

Generic JSON contains title, message, icao and time. The message includes available aircraft observations. The practice URL https://example.invalid/aircraft-alerts is deliberately unusable.

Anyone who knows a public ntfy topic may be able to read its messages. Use an authenticated/private destination for sensitive information. Treat webhook URLs as secrets. TLS certificate verification remains enabled; redirects are not followed by the delivery client.

No destination is enabled by default. There are no automatic retries. Stopping cancels queued delivery and waits for any in-flight request.

<a id="quiet"></a>
## Quiet hours, cooldowns and delivery status

Use quiet hours to retain events without disturbing you, and cooldowns to reduce repeat notifications.

### Control alert volume

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Quiet start / end | 22:00 / 07:00 | Local 24-hour time. Set both or leave both blank; overnight windows are supported. |
| Rule repeat interval | 900 seconds | Minimum time between alerts for that rule/aircraft; matching continuously does not create a timed reminder. |
| Master switch | Alerts enabled | Turning it off stops rule alert generation. |
| Rate limit | 20 events per minute | Shared across rules. Check suppressed and delivery-drop counters. |

### Read status correctly

Saved means the event is retained locally. Service acceptance means the remote service accepted the request, not that someone read it. Failure means the delivery attempt did not succeed. Quiet events are retained without channel delivery.

The app, receiver and relevant channels must remain running/enabled. If delivery stops, check quiet hours, rule criteria, cooldown, master switch and saved destination before changing reception settings.

<a id="alert-content"></a>
## What an alert contains

An alert identifies the rule and aircraft, records the event time, and includes only available received measurements. Practice previews are explicitly labelled synthetic.

### Available fields

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Identity | DEMO101 · A00001 | Received callsign and ICAO24. |
| Rule and time | Demo low aircraft; local time with UTC offset | Ties the event to your watch rule. |
| Motion | Altitude, speed, track, vertical rate | Measurements are included when available. |
| Position and status | Latitude/longitude, squawk, emergency state | Missing fields are omitted rather than guessed. |
| Provenance | Source, field ages, playback label | Shows how recent the underlying information is. |

### Information that is absent

No confirmed route, passenger details, registration, aircraft model or airline database is attached. Coordinates are observations, not a guarantee of the aircraft's current location. Alert history is subject to the configured retention/size budget.

<a id="statistics"></a>
## Understand the Statistics screen

Statistics are collected directly from decoded observations, independently of diagnostic log text. New history starts when recording is enabled; old logs are not converted into a flight archive.

### Explore the data

1. Open Statistics. Choose Live or Playback and a retained time period, then refresh.
2. Inspect traffic/message and unique-aircraft charts, range by bearing, source totals and headline measurements.
3. Use encounter, airport and alert views to inspect the retained records. Export the selected table to CSV when needed.

### What the numbers mean

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Unique aircraft | Distinct ICAO24 addresses | Not a count of verified registrations or scheduled flights. |
| Encounter | Reception separated by a 20-minute gap | A local reception encounter, not a confirmed flight. |
| Callsign last seen / Days ago | Latest reception of that callsign and local calendar days since (0 means today) | A callsign can move between aircraft. Dates marked ≈ are approximate: older versions kept the associated encounter end, not the time of the callsign message. |
| Aircraft times seen | Distinct retained encounters for this ICAO24 address across all callsigns | Uses all retained history for the selected source, including encounters outside the displayed period. Live reception and playback are counted separately. |
| Accepted / rejected frames | Input processing counters | Not all frames contain a position or every aircraft field. |
| Vendor aircraft reports sent | Feeder monitor reports | Different from received RF messages or unique aircraft. |
| Range sectors | Maximum received range by bearing | Requires positions and antenna coordinates; not a guaranteed coverage boundary. |
| Maximum altitude/speed; closest distance | Observed retained measurements | Not predictions or complete flight records. |

### Time and limits

Periods use minute-rounded boundaries. Empty minutes can include receiver downtime. Encounter tables show the latest 500 rows and alert tables 200; headline totals cover the retained selected period. Filters do not reduce collected history. Live and playback remain separate.

<a id="airports"></a>
## Airports and proximity counts

The offline reference contains 5,282 large and medium airports worldwide from OurAirports. Search by code, name or city, or add another facility manually.

### Add an airport

1. Use Add airport in Statistics or Storage & alerts.
2. Search for a code such as KJFK. Select the reference result to prefill its name and coordinates, or enter your own facility's details.
3. Set a proximity radius and save. Up to 100 airports can be configured.
4. As new observations arrive, the airport view counts encounters inside that radius. Earlier history is not reclassified when an airport is added.

### Interpret proximity carefully

An aircraft passing near an airport is not necessarily arriving or departing. These counts do not establish route, origin, destination or airport movements. OurAirports data is a public-domain reference, not a certified navigation database.

The practice station is an arbitrary Atlantic point and is unrelated to airport examples or any user's home location.

<a id="storage"></a>
## Limit history and log file growth

Receiver & settings → Storage & alerts controls structured history and rotating diagnostic logs. Oldest managed data is removed when age or size limits are reached.

### Storage settings

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Collect structured history | On by default | Disable new recording without treating logs as an alternative archive. |
| Keep history for | 30 days; range 1–3650 | Age retention for stored observations and events. |
| History database + journal budget | 256 MiB; range 8–4096 | Shared budget includes space reserved for safe SQLite rollback writes. |
| Sample each aircraft | 15 seconds; range 5–300 | At most one sampled snapshot per aircraft per interval; per-minute message counts remain separate. |
| Total diagnostic log budget | 20 MiB; range 1–1024 | Bounded rotating files, independent of history. |
| Keep rotated logs for | 3 days; range 1–365 | Rotated-log age limit. |
| Map cache | 64 MiB fixed | Separate budget for requested background tiles. |

### What budgets exclude

CSV exports, diagnostic ZIPs, manual backups, input recordings, installation packages and build outputs are outside managed budgets. Manage those files yourself. MiB means 1,048,576 bytes.

A full/unavailable disk leaves reception running and reports history loss. Queue overload and dropped reports are counted. Smaller budgets retain less history; sampling less frequently reduces snapshot volume. Back up important data before reducing limits because pruning removes old records.

<a id="export"></a>
## Export aircraft and statistics

CSV exports create a file you choose; nothing is uploaded automatically. Formula-like text is escaped for safer spreadsheet opening.

### Export options

1. Use File → Export aircraft CSV for the currently visible, filtered aircraft. It contains available identity, position, altitude, speed, track, vertical rate, squawk and age.
2. In Statistics, select the desired table and use its CSV export. Check the selected Live/Playback mode and period first.
3. Open the result in your spreadsheet program. Preserve units and the distinction between observations, encounters and confirmed flights.

### Before sharing

Exports can disclose aircraft positions and timestamps. Review the file before posting. Exports are not automatically aged or size-limited by the app. Table exports follow the displayed record limits; they are not an unlimited raw database dump.

<a id="sharing"></a>
## Register and upload to Flightradar24

Optional sharing uses the unchanged official feeder. Local reception works without a sharing key. A receiver key is different from an API credential.

### Configure sharing

1. Stop reception. Open Receiver & settings → Flightradar24.
2. Enter your existing 16-character sharing key in its protected field, or start the official registration wizard.
3. For registration, enter your own email and accurate antenna coordinates/altitude at the prompts. Select Beast TCP when asked; the application manages the local source address.
4. Enable Upload live reception to Flightradar24, save and start receiver.
5. Inspect vendor connection status and aircraft-sent reports. Check your Flightradar24 data-sharing account for online/recent data to corroborate acceptance.

### Understand the boundary

A running process or connected status alone is not proof of accepted aircraft uploads. Use a sharing key on only one receiver at a time. Recorded playback cannot upload, and decoded SBS input cannot recreate the raw data needed for sharing.

On Windows the vendor's Linux x86-64 executable runs through the bundled Blink/Cygwin user-mode runtime; no Linux OS or VM is required. Linux runs its feeder natively. The feeder is proprietary and separately licensed; it is not part of this project's open-source licence.

The public source repository excludes vendor binaries. A full Windows package and feeder runtime must be obtained/prepared separately under their applicable terms.

<a id="mlat"></a>
## MLAT status and verification

Multilateration needs accurately timed observations from multiple receivers and permission from the receiving service. Enabling the option does not establish active MLAT.

### Check prerequisites

1. Use a supported source with valid sample timestamps, accurate antenna coordinates and altitude, and stable reception.
2. Enable Request MLAT with timestamped input in Flightradar24 sharing settings. Allow MLAT without a GPS receiver only as appropriate for your station.
3. Inspect the vendor monitor and account-side status separately from ordinary ADS-B upload.
4. If the monitor says NOT-PERMITTED or mlat-disabled, record that result. It is not evidence that MLAT is working.

### Known limits

Plain AVR without timestamps and decoded SBS cannot supply MLAT timing. The project's live upload path has been tested, but active vendor MLAT has not been verified. A username or display-name change does not itself enable MLAT. Service permission and surrounding receiver coverage cannot be manufactured by local settings.

<a id="outputs"></a>
## RAW, SBS and Beast data outputs

Data settings can expose received streams to other software. They are separate from Flightradar24 uploading and from the native desktop interface.

### Output reference

| Setting | Example / value | Meaning |
| --- | --- | --- |
| RAW / AVR | Port 30002 | Raw messages where the input supports them. |
| BaseStation / SBS | Port 30003 | Decoded text reports. |
| Beast | Port 30005 | Binary stream with receiver timestamps when available. |
| This PC only | 127.0.0.1 | Default binding; other computers cannot connect. |
| Local network · all interfaces | 0.0.0.0 | Binds all interfaces; streams have no application authentication. |

### Configure deliberately

Stop receiver before saving. Enabled output ports must be distinct and between 1024 and 65535. Avoid a port already used by your input server or another application.

Only expose streams to a network you intend to share with. Setup does not create firewall rules. SBS input supports SBS forwarding only; it cannot reconstruct raw messages. Normal desktop use opens no browser and no HTTP control API.

<a id="operation"></a>
## Startup, stale aircraft and reconnects

Data settings controls automatic reception, inactive inputs and how long aircraft remain in the current display.

### Operation reference

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Start receiver when this app opens | Off by default | Starts reception after opening; it does not install a Windows startup task. |
| Remove stale aircraft after | 60 seconds; 10–3600 | Current-display lifetime after last reception, separate from stored history. |
| Reconnect after no frames | 600 seconds; 0–86400 | 0 disables the inactivity timeout. |
| Diagnostic file logging | On by default | Off stops file logging; structured statistics have their own switch. |

### Shutdown and duplicate launches

Close the main window or choose File → Exit to stop receiver/feeder components cleanly. If shutdown reports a failure, read the message and retry after resolving the cause. Closing is not a minimise-to-tray action.

A second launch using the same profile activates the existing application. Different test profiles can run separately; never have two processes compete for the same USB receiver or sharing key.

<a id="import"></a>
## Import and export INI settings

The settings importer previews recognised receiver options from an INI file. It does not execute imported Linux commands.

### Use an INI safely

1. Stop reception and open the INI import/export controls in Data settings.
2. Select an INI and inspect its preview, including warnings for unsupported settings.
3. Apply the recognised values only after reviewing them, then check input, antenna, outputs and sharing settings before starting.
4. Use INI export for a key-free settings file. Store and share it deliberately; settings may still reveal station location or network details.

### Scope

Recognised options are mapped into typed settings. Other original values may be preserved but remain unapplied. Arbitrary Linux procargs, Pi Wi-Fi, OS updates/shutdown and unsupported UAT/Kinetic modes are not recreated. Sharing keys belong in protected storage, not in public fixtures or issue reports.

<a id="playback"></a>
## Recorded playback and dummy practice

Recorded I/Q playback decodes a real file locally. Support practice uses built-in fictional observations and requires neither a recording nor a radio.

### Recorded I/Q

1. Stop reception. Choose Recorded I/Q file in Receiver and browse to an unsigned 8-bit interleaved .uc8 recording.
2. Match its sample rate, save and start. Inspect the decoded aircraft.
3. Choose Playback in Statistics. Live and playback history remain separate.

### Keep examples identifiable

Playback cannot be uploaded as live data. Alerts for playback require per-rule opt-in and include a playback label. A successful recording test verifies decoding, not antenna reception, live upload or MLAT.

The Support practice workspace is even more isolated: it holds examples in memory, does not save settings/history, send notifications, access USB or connect to destinations. Use it to learn filter matching, validation and alert content.

<a id="practice"></a>
## Practice with prefilled dummy data

Choose Support → Practice with dummy data for an editable sandbox. All values are examples and no practice change reaches your receiver profile.

### Try the examples

1. On Receiver, inspect the prefilled USB index, sample coordinates and altitude. Change a field and choose Check example to see validation feedback.
2. On Filters, start with DEMO* and maximum altitude 10000 ft. Preview matches: DEMO101 at 6500 ft matches; DEMO202 at 18000 ft does not. SAMPLE3 has no altitude.
3. Clear the filter values or change maximum altitude to 20000 and preview again. Matching uses the same evaluator as the live app.
4. On Alert, edit the sample rule and preview the synthetic notification text. No sound, desktop notification, email or webhook is sent.
5. On Storage, change retention, budget and sample interval, then validate the example. Reset examples restores the dummy values.

### Dummy identities

DEMO101, DEMO202, SAMPLE3 and A00001–A00003 are fictional examples in this workspace, not asserted real flights. The station 0°, −30° is an arbitrary Atlantic point. example.invalid email/URL values cannot deliver messages. Never use these examples as real antenna or feeder registration details.

<a id="reception"></a>
## No aircraft, sparse reception or blank map

Work from the source outward: USB visibility, decoder status, messages, positions, then map tiles. These are separate checks.

### Recovery checklist

1. If USB scan is empty, reconnect the receiver, use a reliable port and close other SDR applications. Check driver setup for a supported device.
2. If the receiver cannot start, read Logs & diagnostics. Check decoder index, source address, port conflicts, driver access and recording path.
3. If messages arrive but coverage is sparse, improve the 1090 MHz antenna and placement. An unsuitable indoor antenna can receive very little.
4. If the table has aircraft but the map is empty, clear filters and inspect position age. Some messages carry no coordinates.
5. If aircraft/radar work but the basemap is blank, check internet availability and tile-service errors. Offline range rings remains usable.
6. If history is missing, check recording enabled, Live/Playback selection, retention, size budget and history error/drop counters.

### Do not mistake status for evidence

Connected hardware does not prove RF reception; RF messages do not prove a position; feeder connected does not prove uploaded aircraft; upload does not prove MLAT. Compare the relevant stage and report the exact observed error.

<a id="logs"></a>
## Logs, diagnostics and requesting support

Logs & diagnostics shows receiver/controller messages and can export a redacted diagnostic ZIP. Nothing is submitted automatically.

### Prepare a useful report

1. Note what you were trying to do, the exact visible error and steps that reproduce it.
2. Choose Support → Copy support report template. It prefills app version, operating-system family and architecture without your hostname, profile paths, location or credentials.
3. Paste the template into your editor and complete the description. Review any screenshot or attachment for private data.
4. If needed, export diagnostics from Logs & diagnostics and inspect the ZIP before sharing. The support menu can copy the GitHub Issues address for you to visit separately.

### What diagnostics excludes

The built-in ZIP excludes sharing keys, antenna coordinates, raw logs, feeder monitor contents and registration answers. Do not zip your whole application profile: it can contain credentials, locations and a temporary feeder configuration.

There is no automatic crash upload or issue submission. A report is not created by copying the template. Public GitHub issues are visible to everyone; never attach account passwords, sharing keys, webhook secrets or private station details.

<a id="backup"></a>
## Profiles, backups and upgrades

The app keeps each user's settings and history outside the installation directory. An ordinary upgrade preserves that profile.

### Where your data lives

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Windows | %LOCALAPPDATA%\FlightRadarWindows | Established profile name retained across the app rename. |
| Linux | User data directory for FlightRadarWindows | Follows platform data-directory selection. |
| settings.json / features.json | Receiver / filters, alerts and storage preferences | May contain private location or delivery account details. |
| airspace.sqlite3 | Structured reception history | Close the app before making a manual backup. |
| Protected secrets | Sharing key and alert credentials | Windows DPAPI is bound to its Windows user; copying files does not make them portable. |

### Upgrade and recovery

Close the app cleanly before replacing installation files or backing up its profile. Keep the complete executable/runtime/_internal folder together for portable installs. Do not overwrite settings with sample files.

A crash can leave a plaintext feeder configuration needed by the vendor process; normal stop deletes it and the next launch clears stale configuration. Keep profiles private. Manual backups and exports are outside the app's storage limits.

<a id="privacy"></a>
## Privacy, credentials and network access

Normal desktop operation uses native widgets and no browser or HTTP control server. Network access depends on the features you enable.

### Connections and data

| Setting | Example / value | Meaning |
| --- | --- | --- |
| Map tiles | Requested map area and network address | OpenStreetMap tile requests; aircraft payloads and keys are not sent to the tile service. |
| Feeder / registration | Your explicitly configured vendor integration | Official Flightradar24 service and its own terms. |
| Email / HTTPS alerts | Aircraft observations and configured delivery identity | Only enabled destinations; no default recipient. |
| Network inputs/outputs | Configured aircraft data streams | LAN outputs have no built-in authentication. |
| Support / practice | Offline content and in-memory examples | No automatic reporting or external delivery. |

### Protect your credentials

Do not publish the profile, real logs, registration answers, sharing key or webhook URL. Windows uses DPAPI for secrets; Linux uses restricted file permissions. Do not disable TLS verification to solve a connection problem.

A headless loopback HTTP API exists for explicit development tests. It is not part of normal desktop operation and its session URL/token should never be posted.

<a id="glossary"></a>
## Glossary and units

Common terms used throughout the receiver, map, filters and statistics.

### Quick reference

| Setting | Example / value | Meaning |
| --- | --- | --- |
| ADS-B | Automatic Dependent Surveillance–Broadcast | Aircraft broadcast position and other measurements when equipped. |
| Mode S / Comm-B | Transponder message families | May contain identity and measurements without a position. |
| ICAO24 | 24-bit aircraft address | Six hexadecimal characters; distinct from callsign and registration. |
| MLAT | Multilateration | Position derived by a service from accurately timed observations at several stations. |
| AMSL | Above mean sea level | Altitude reference used for the antenna. |
| ft / kt / NM | Feet / knots / nautical miles | 1 NM = 1.852 km; 1 kt = 1 NM per hour; 1 metre ≈ 3.28084 ft. |
| MiB | Mebibyte | 1,048,576 bytes; used by storage budgets. |
| UTC offset | Local time's difference from UTC | Shown with event timestamps to reduce timezone ambiguity. |
| Encounter | Locally observed reception period | Not a confirmed scheduled flight or airport movement. |

<a id="about"></a>
## About, licence and project

FlightRadar24 is an independent open-source desktop project by Kieran O'Rourke. See Support → About for the running software version.

### Open source

Project-authored controller, UI and documentation are licensed under GNU GPL v3 or later. Copyright © 2026 Kieran O'Rourke. The licence text is included with the source and app. There is no warranty to the extent permitted by the licence.

Dependencies retain their own licences. The official Flightradar24 feeder is proprietary and excluded from the project's GPL grant and public source snapshot. OurAirports data is public domain; map data is © OpenStreetMap contributors. Third-party notices describe these boundaries.

### Project and status

Source and issue tracker: https://github.com/ufo8mycow14/FlightRadar24 . Support can copy these addresses; it never launches a browser automatically.

This project is not affiliated with or endorsed by Flightradar24 AB. Windows x64 is the tested target; Linux execution remains unverified and macOS is deferred. Active MLAT and real external notification delivery remain separately verifiable features, not guaranteed outcomes.

<a id="updates"></a>
## Software updates from GitHub

Check for a new Windows release, install it manually or opt into automatic installation.

### Choose how to update

1. Open Support → Software updates, or click Updates in the bottom bar. Check now looks for the latest published stable release in the project’s GitHub repository. A raw source commit without a published installer is not an application update.
2. Automatic checking is on by default in the packaged Windows application. Checks run every 12 hours while the program is open, with an overdue check shortly after startup. No service or scheduled task runs when the program is closed. You can disable these checks.
3. Automatic installation is off by default. Select Automatically download and install updates and Save preferences to enable it. Otherwise choose Download update, then Install and restart.
4. Automatic mode provides a 60-second countdown in the bottom status bar. Open Updates and choose Postpone this version to keep using it until you install manually. Settings edits and active dialogs defer an automatic restart. Save pending changes before installing.

### What installation does

The installer is downloaded over HTTPS from the project’s GitHub release and its size and SHA-256 digest are verified before execution. Older versions, prereleases, missing installers, untrusted URLs and mismatched hashes are rejected. No GitHub account is needed. This validates downloaded bytes against GitHub, not a signed publisher identity.

The app closes the receiver cleanly, waits for the process to exit, runs the installer and reopens the same profile. Reception resumes if it was running beforehand. Saved settings, sharing keys, optional vendor components and history are retained. Application updates skip USB driver provisioning and vendor downloads. Windows elevation prompts can still require your response.

If download or preparation fails, the current app keeps running. If installation fails after shutdown, the helper records the failure and attempts to reopen the existing executable; the update page reports that the previous attempt failed. Review updates/install.log in the profile directory for setup details. Failed installation attempts and postponed versions are not retried automatically until a newer version appears; manual retry remains available. Download failures may retry at the next scheduled check.

Checks and downloads contact GitHub, which sees your connection IP address. The app sends no receiver settings, sharing keys, aircraft data or history. Source and Linux builds do not automatically check or run a Windows installer.

<a id="waypoints"></a>
## Aviation waypoints on the map

Show or hide worldwide historical waypoints, preview fixes ahead of an aircraft, or import your own newer waypoint data.

### Show or hide waypoints

1. Click WPT on the map or View → Aviation waypoints. The on/off choice is saved and starts off. Save pending settings before using the quick toggle.
2. Zoom in to see small purple triangles and identifiers; labels thin out to limit overlap. Hover for coordinates, source and date. Aircraft retain hover and selection priority. Waypoints do not establish an aircraft’s intended route.

### Reference date and coverage

The bundle contains 119,716 worldwide historical fixes from cycle October 2013. When waypoints are enabled, hover over or select a moving aircraft for a yellow dotted guide to a plausible fix ahead. The guide follows the reported ground track and updates as the aircraft approaches a fix. It is not a filed route, current AIRAC data or navigation guidance.

The source is Robin A. Peel / FlightGear, GPL version 2 or later. Original notice, exact source URL/hash and geographic subset details are included in the bundled JSON; the GPL text is included alongside it.

### Import a newer file

1. Open View → Aviation waypoint data. Enter the source cycle/date if known and choose Import waypoint file. Import only data you are entitled to use. The date is user supplied, not verified.
2. Use UTF-8 CSV with ident,latitude,longitude columns and decimal degrees, or X-Plane/FlightGear fix.dat/.dat.gz (600/1100/1101/1200 formats). Files are limited to 12 MB after decompression and 100,000 records. Invalid coordinates/files leave the previous data intact.
3. The imported layer replaces the selected reference. Use historical reference and Use last import switch between them. Imported data stays in the local profile and is never uploaded; program updates retain it.
