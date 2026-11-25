# ACARS Ticker — demo runbook

Everything you need to run the live demo without surprises.

---

## 0. The one-line story (say this if you say nothing else)

> "Aircraft over our heads are broadcasting text messages **in plaintext** — no
> encryption, no authentication. We built a tool that pulls them out of the air
> with a **$20 radio** and shows them live, tagged with exactly what each message
> leaks."

---

## 1. Before you leave home (do this, don't improvise at the venue)

```bash
cd ~/Desktop/Air-Bas
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# smoke test — should open a map centered on Amman with planes + a scrolling feed
python -m acars_ticker --replay samples/sample.jsonl --replay-loop
```

Open **http://127.0.0.1:8080**, click **start the feed**, and confirm:

- [ ] Intro splash shows "ACARS TICKER" + your three names
- [ ] Map centers on Amman, planes appear (needs internet for the live traffic layer)
- [ ] Feed scrolls, messages are tagged (ops/weather/ATC/…)
- [ ] `S` turns on the Security Lens → POSITION / OPS INTEL / IDENTITY tags appear
- [ ] `P` = presentation mode (bigger), `F` = fullscreen
- [ ] Red **UNENCRYPTED · UNAUTHENTICATED** badge is visible top-left

Then **leave that command running** — `--replay-loop` keeps the feed flowing forever.

> **Tip:** zoom the browser to ~110–125% (Ctrl +) on the projector so the back row
> can read the feed. Or just hit `P` for presentation mode.

---

## 2. Two things that can go wrong, and the fix

| If… | Then… |
|---|---|
| **No internet at the venue** | The map tiles and the live "nearby traffic" planes won't load, but the **ACARS feed and security lens still work** (replay is local). Talk to the feed, not the map. |
| **You brought the radio but there's no signal** | Just keep the `--replay-loop` command running — it looks identical on screen. Don't fight the antenna live. |
| **The feed looks empty** | You forgot `--replay-loop`, or the venv isn't active. Re-run the command in section 1. |
| **Browser shows "connection lost"** | The Python process died — restart the command; the page auto-reconnects. |

**Golden rule:** replay mode is the safety net. If anything RF misbehaves, fall
back to replay and nobody can tell the difference.

---

## 3. The demo flow (keep it under ~4 minutes)

1. **Open on the intro splash** — let it sit for a beat, read the tagline aloud, click **start the feed**.
2. **The map** — "these are real aircraft around us; the 🏠 is our receiver, the cyan plane is one we're actually decoding ACARS from."
3. **Click a plane** → its message thread + position. "This is one aircraft's conversation with the ground."
4. **Press `S` (Security Lens)** — scroll the feed slowly. Call out tags:
   - `🔓 POSITION` — "where the aircraft is"
   - `🔓 OPS INTEL` — "gates, fuel, delays, crew"
   - `🔓 IDENTITY` — "the tail number tells us the owner"
5. **Point at the red UNENCRYPTED badge** — "none of this is encrypted or signed. We didn't break anything. We just listened."
6. (Optional) **Press `P`** for a clean full-screen look while you talk.

Hotkeys: **`P`** present · **`S`** security lens · **`F`** fullscreen · **`⌖ my location`** recenters the map.

---

## 4. Talk flow (slide order)

The deck runs in this order, with speaker notes on every slide:

1. Cover → hook → what is ACARS → the security gap → what an eavesdropper learns → legal/ethical.
2. Architecture → stack → features → **live demo** → resilience.
3. Lessons learned → defenses → try it yourself → close + Q&A.

---

## 5. Q&A cheat-sheet

- **Is this legal?** Receiving is legal in many jurisdictions; we only *receive*, never transmit. Laws differ — some countries restrict receiving non-public radio, so know your local rules.
- **Could you spoof messages to a plane?** In theory yes (no authentication), but that's illegal and dangerous — strictly out of scope. We never transmit.
- **How far does it reach?** Line-of-sight VHF — up to ~200 nautical miles from an aircraft at cruise altitude.
- **Is this ADS-B / Flightradar?** No. ADS-B is a separate system; we use a public ADS-B API *read-only* to add aircraft identity and position. ACARS is the messaging link we decode.
- **Did you build the radio decoder?** No — `acarsdec` (open source) does the signal demodulation. We built everything after it: the pipeline, storage, classification, enrichment, and the live UI.
- **Why does it matter?** It's a live, cheap demonstration that the same failures we chase in web apps — A02 Cryptographic Failures, A07 Authentication Failures — still run unpatched in global critical infrastructure.

---

## 6. The deck

Slides are in the Claude artifact (15 slides, dark SDR theme). Export to **PDF**
from the deck's Share → Export menu and put that PDF on the presenting laptop as
an offline backup in case the venue Wi-Fi is bad.
