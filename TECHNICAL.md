# How acars-ticker works — technical deep dive & FAQ

A technical deep dive into how acars-ticker works, plus answers to the
questions people ask most. Read this and you can explain any part of the
project with confidence.

---

## 1. The one-paragraph version

A cheap **RTL-SDR** radio dongle listens to the VHF band. **`acarsdec`** (an
open-source C program) demodulates the ACARS radio signal and spits out each
decoded message as **JSON over a UDP socket**. Our **Python** program reads
those JSON datagrams, parses them, stores them in **SQLite**, classifies each
message, and looks up the aircraft's identity and position from a free public
**ADS-B API**. A **FastAPI** server pushes every new message to the browser over
a **WebSocket**, where a single-page UI draws a **live map** and a **scrolling
feed**. We did not write the radio decoder — we built everything after it.

---

## 2. The data flow (memorize this diagram)

```
 Antenna          RTL-SDR dongle        acarsdec (C)             our Python
   │                   │                     │                       │
 radio   ─────►   samples 2.4 MS/s  ─────►  demodulates MSK,  ──► UDP :5555 (JSON)
 waves            of RF bandwidth           decodes ACARS            │
 (~131 MHz)                                  frames                  ▼
                                                            collector.py  (parse)
                                                                     │
                                                                     ▼
                                                            pipeline.py
                                                    ┌────────────────┼─────────────────┐
                                                    ▼                ▼                 ▼
                                               classify.py      storage.py        enrichment.py
                                             (label+regex)    (SQLite WAL)        (ADS-B REST API)
                                                    └────────────────┼─────────────────┘
                                                                     ▼
                                                       server.py  (FastAPI)
                                                                     │  WebSocket push
                                                                     ▼
                                                        browser: Leaflet map + ticker feed
```

**One sentence:** `RTL-SDR → acarsdec → UDP JSON → collector → classify/store/enrich → WebSocket → browser.`

---

## 3. Component by component

### 3.1 The radio: RTL-SDR
- A ~$20 USB dongle (chipset **RTL2832U** + **R820T2** tuner) originally made for
  digital TV. The community discovered it's a general-purpose **software-defined
  radio** — it hands raw RF samples to the computer and *software* does the rest.
- It samples about **2.4 million samples/second** of radio bandwidth. The ACARS
  channels we care about all sit inside that slice of spectrum, so **one dongle
  can watch up to 4 channels at once** — acarsdec digitally tunes each one.

### 3.2 The decoder: `acarsdec` (the "black box")
- ACARS is transmitted using **MSK** (Minimum-Shift Keying) modulation at
  **2400 bits/second** on VHF. Demodulating that is real digital signal
  processing — and `acarsdec` already does it well, in C, battle-tested.
- **We deliberately did NOT reimplement this.** Our value is the pipeline, not
  the DSP. We launch it like:
  `acarsdec -r 0 131.525 131.725 131.825 136.900 -j -N 5555`
  (`-j` = JSON output, `-N 5555` = send over the network to UDP port 5555).

### 3.3 The collector: `collector.py`
- Opens a **UDP socket** on `127.0.0.1:5555` and receives acarsdec's JSON
  datagrams. Parses each into a normalized record: timestamp, frequency,
  channel, signal level, error count, **label**, **tail** (registration),
  **flight**, and the **message text**.
- **Supervises** the acarsdec process: if the dongle is unplugged or acarsdec
  crashes, it relaunches with **exponential backoff** and tells the UI
  "receiver down." This is the resilience story.

### 3.4 Classification: `classify.py`
- Every ACARS message has a **2-character label** (a partly-standardized code,
  e.g. `H1`, `5Z`, `80`). We map labels to buckets, then **refine with regex on
  the message text** (text wins because labels are partly airline-defined).
- Buckets: **weather** (METAR/TAF/ATIS), **ops** (gates/fuel/delays), **mechanical**
  (fault reports), **ATC** (clearances, position reports), **misc** (noise).

### 3.5 Storage: `storage.py` — SQLite in WAL mode
- One `messages` table. **WAL** (Write-Ahead Logging) mode lets the collector
  **write** while the web server **reads** concurrently without locking.
- Keeps 30 days, then prunes. Embedded, zero-ops — the right size for a
  single-node receiver.

### 3.6 Enrichment: `enrichment.py` — the ADS-B API
- ACARS tells us the **tail number** but not always the aircraft type or live
  position. We look those up from a free public **ADS-B API** (adsb.lol /
  adsb.fi) by registration, **cached ~10 minutes** so we don't hammer it.
- The **live map traffic** (all the yellow planes) comes from the same API's
  **area query** ("all aircraft within 250 nm of this point"), cached ~10s.
- **Important:** this is **read-only HTTP**. We have **no ADS-B receiver
  hardware**. If a lookup fails, we degrade gracefully and still show the raw
  registration — enrichment never blocks the feed.

### 3.7 The server + UI: `server.py` + `web/`
- **FastAPI** serves the page and a few REST endpoints (`/api/recent`,
  `/api/stats`, `/api/aircraft`, `/api/traffic`, `/api/thread/{tail}`).
- A **WebSocket** pushes each new message to every open browser instantly —
  that's what makes the feed "live" instead of polling.
- Frontend is **vanilla JS + Leaflet** (the map) with OpenStreetMap tiles. No
  framework. The security lens, classification colors, donut, and presentation
  mode are all client-side.

---

## 4. Why we built it this way (the "why did you…" answers)

| Decision | Why |
|---|---|
| **Didn't write the MSK demodulator** | DSP is a solved problem; `acarsdec` is mature C. Our contribution is the pipeline, storage, enrichment, and UX. Reinventing demod would be slower and worse. |
| **UDP between acarsdec and Python** | It's acarsdec's native network output (`-N`). UDP is fire-and-forget and cleanly decouples the two processes. On localhost there's effectively no packet loss. |
| **SQLite, not Postgres/Mongo** | Single node, embedded, zero admin. WAL mode gives concurrent read/write. A receiver station doesn't need a database server. |
| **WebSocket, not polling** | True push = instant feed and lower overhead. The server broadcasts once; every browser updates. |
| **ADS-B from an API, not hardware** | Scope control. ACARS is the project; ADS-B is only used read-only to enrich identity/position. Keeps it to one dongle. |
| **Vanilla JS, no React** | ~one page. A framework would be more weight than the whole app. Keeps it readable and dependency-light. |
| **Replay mode** | Lets us develop and demo with **no radio** — feed recorded JSON through the exact same pipeline. It's also our safety net on stage. |

---

## 5. The security story

- ACARS was designed in **1978** with **no encryption, no authentication, no
  integrity**. Every message is readable plaintext over the air.
- Map it to the OWASP Top 10: **A02 Cryptographic Failures** and **A07
  Identification & Authentication Failures** — but in global aviation
  infrastructure, unpatched, by design.
- Our **security lens** reframes each message as *what it leaks*: POSITION,
  OPS INTEL (gates/fuel/crew), IDENTITY (tail → owner), TECH (fault codes).
- **We are receive-only.** Spoofing uplinks is possible *in theory* (there's no
  authentication) but is **illegal and dangerous** and strictly out of scope.
  We never transmit.

---

## 6. Anticipated questions & confident answers

**Q: Is ACARS the same as ADS-B / Flightradar?**
No. **ADS-B** is a position broadcast on 1090 MHz; **ACARS** is a text
*messaging* link on VHF (~131 MHz). We **decode ACARS**. We only **read ADS-B
from a public API** to add aircraft identity and position to the map.

**Q: How can one dongle decode 4 channels at once?**
The dongle samples ~2.4 MHz of bandwidth. The four ACARS channels all fall
inside that span, so acarsdec digitally down-converts and demodulates each one
in software, in parallel.

**Q: What's the range?**
VHF is **line-of-sight**. From an aircraft at cruise altitude you can hear it up
to roughly **200 nautical miles** away, depending on your antenna and terrain.
Planes on the ground far away, you won't hear.

**Q: Is this legal?**
**Receiving** is legal in many jurisdictions — it's passive listening. We never
transmit, and we don't act on or deanonymize anything. Laws differ by country
(some restrict receiving non-public radio), so know your local rules.

**Q: Could you spoof messages to a plane?**
In theory yes — there's no authentication. But transmitting on aviation
frequencies is **illegal and dangerous**, and it's **out of scope**. We only
listen.

**Q: Isn't a lot of the data garbled?**
Yes — RF has errors. acarsdec reports a **signal level** and **error count** per
message; we surface a live **decode error rate** and bucket unreadable fragments
as "misc." That's honest and expected for over-the-air capture.

**Q: How is the feed "live"?**
The collector hands each parsed message to a pipeline that stores it and
**broadcasts it over a WebSocket** to every connected browser immediately — no
polling.

**Q: Why not decode ACARS yourself in Python?**
MSK demodulation is signal processing better served by mature, optimized C
(`acarsdec`). Our engineering is the **data pipeline and UX** on top of it.

**Q: What about VDL-2 / HFDL / satellite ACARS?**
Out of scope — we deliberately kept it to **VHF ACARS** to keep the project
focused. Those are natural future extensions.

**Q: How accurate is the "security lens"?**
It's a **heuristic** (label + regex) that illustrates what each message
*category* exposes. It's for making the point, not a forensic classifier — we're
upfront about that.

**Q: Does it scale?**
It's a **single-node receiver station**: one dongle, up to four channels, one
machine. That's the whole design point — anyone can run it. Scaling would mean
multiple receivers feeding a shared store (future work).

**Q: Where does the aircraft data come from — are you tracking people?**
Identity/position come from a **public ADS-B API**, read-only and cached. We
don't profile individuals; the security point is that the *capability* exists
cheaply, not that we exercise it.

---

## 7. Honest limitations (say these before someone else does)

- Coverage depends on **antenna and location** — directly overhead is weaker than
  the airways; some areas are quiet.
- Enrichment depends on the **public ADS-B feeders** — not every aircraft is
  live on ADS-B, so some planes show registration only.
- Classification and the security lens are **heuristics**, not perfect.
- It's **VHF ACARS only** — not the whole aviation datalink ecosystem.
- No radio hardware in the room? The **live map needs internet**; the ACARS feed
  (replay) is fully local.

---

## 8. If you get a question you don't know

Don't bluff in front of a security crowd. Say:

> "Great question — we scoped this to VHF ACARS and the receive side, so we
> didn't go deep on that. My instinct is [X], but I'd want to verify it."

Then move on. Honesty + a clear scope answer beats a wrong confident answer
every time.
