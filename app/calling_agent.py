import json
import uuid
from datetime import datetime, timezone

import streamlit as st
import google.generativeai as genai
import streamlit.components.v1 as components

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Crisis Intake Agent",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow:wght@400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Barlow', sans-serif; }
.stApp { background: #080d18; color: #dde6f5; }
section[data-testid="stSidebar"] {
    background: #0c1220 !important;
    border-right: 1px solid #1a2d4a;
}
h1, h2, h3 {
    font-family: 'Share Tech Mono', monospace !important;
    color: #ff4040 !important;
    letter-spacing: 0.06em;
}
.user-bubble {
    background: #131e35; border-left: 3px solid #3a7bd5;
    padding: 10px 15px; border-radius: 3px 10px 10px 3px;
    margin: 7px 0; font-size: 0.94rem; color: #b8cfe8;
}
.agent-bubble {
    background: #0f1828; border-left: 3px solid #ff4040;
    padding: 10px 15px; border-radius: 3px 10px 10px 3px;
    margin: 7px 0; font-size: 0.94rem; color: #dde6f5;
}
.agent-question {
    background: #0e1f10; border-left: 3px solid #4caf50;
    padding: 10px 15px; border-radius: 3px 10px 10px 3px;
    margin: 7px 0; font-size: 0.97rem; color: #7ed47e; font-weight: 600;
}
.complete-banner {
    background: linear-gradient(135deg, #0b2e18, #07180d);
    border: 1px solid #2ecc71; border-radius: 8px;
    padding: 14px 18px; color: #2ecc71;
    font-family: 'Share Tech Mono', monospace; font-size: 1rem;
    margin: 12px 0; text-align: center; letter-spacing: 0.05em;
}
.field-row {
    display: flex; align-items: flex-start; gap: 8px;
    padding: 3px 0; font-size: 0.82rem;
    font-family: 'Share Tech Mono', monospace;
}
.field-label { color: #5580aa; min-width: 135px; }
.field-val   { color: #a8c8f0; word-break: break-word; }
.field-null  { color: #2a3d5a; font-style: italic; }
.dot-ok   { color: #2ecc71; }
.dot-miss { color: #2a3d5a; }
.sev-critical { color: #ff2b2b !important; font-weight: 700; }
.sev-high     { color: #ff8c00 !important; font-weight: 700; }
.sev-medium   { color: #ffd700 !important; font-weight: 600; }
.sev-low      { color: #7cfc00 !important; }
.stTextArea textarea {
    background: #0e1828 !important; color: #dde6f5 !important;
    border: 1px solid #1a2d4a !important; border-radius: 6px;
    font-family: 'Barlow', sans-serif !important; font-size: 0.95rem !important;
}
.stTextInput > div > div > input {
    background: #0e1828 !important; color: #dde6f5 !important;
    border: 1px solid #1a2d4a !important; border-radius: 6px;
}
.stButton > button {
    background: #b81c1c !important; color: white !important;
    border: none !important; border-radius: 6px !important;
    font-family: 'Barlow', sans-serif !important;
    font-weight: 700 !important; letter-spacing: 0.04em;
}
.stButton > button:hover { background: #8a1212 !important; }
</style>
""", unsafe_allow_html=True)

# ─── System Prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a Disaster Incident Intake Agent for an emergency response coordination system.

Your job: collect ALL required information through a calm, professional conversation — one question at a time.
Output ONLY raw JSON. No markdown. No explanations. No extra text.

## INFORMATION TO COLLECT (in priority order)
1. incident_type     — fire / flood / accident / medical / crime / infrastructure / other
2. description       — concise summary of what is happening
3. severity          — low / medium / high / critical  (infer from context, ask if unclear)
4. location.city     — city or district
5. location.address  — street / building address  (ask; null if refused or unknown)
6. location.landmark — nearest well-known landmark (ask; null if refused or unknown)
7. casualties.trapped   — number of people trapped (ask explicitly; 0 if none)
8. casualties.injured   — number injured (ask; 0 if none)
9. casualties.dead      — number dead (ask; 0 if none)
10. hazards          — e.g. fire, chemical spill, downed power lines, gas leak
11. required_services— fire brigade / ambulance / police / rescue / hazmat / etc.
12. reporter.name    — caller's name (ask; null / is_anonymous=true if refused)
13. reporter.phone   — callback number (ask; null if refused)
14. additional_notes — anything else the caller wants to add

## COMPLETION RULE
Set status=complete ONLY when ALL of the following are true:
  ✔ incident_type is filled
  ✔ severity is filled
  ✔ description is filled
  ✔ location has at least city OR landmark (not both required)
  ✔ casualties (injured/dead/trapped) have been explicitly asked AND answered (even if all 0)
  ✔ reporter name and phone have been asked (null/anonymous is acceptable)
If any of the above is still outstanding, status=incomplete and ask the next question.

## BEHAVIOUR
- Extract everything possible from each message before asking.
- Ask EXACTLY ONE focused question per turn — the highest-priority missing field.
- Be empathetic and calm; this is an emergency.
- Accept "I don't know" / "unknown" → store null and move on.
- Never re-ask a field already answered.
- Preserve incident_id across all turns.
- NEVER output anything except the raw JSON object.

## JSON SCHEMA
{
  "status": "incomplete | complete",
  "incident_id": "<UUID — same across all turns>",
  "reported_at": "<ISO 8601 UTC>",
  "incident_type": "fire | flood | accident / medical / crime / infrastructure / other | null",
  "severity": "low | medium | high | critical | null",
  "description": "<string or null>",
  "location": {
    "address": "<string or null>",
    "landmark": "<string or null>",
    "city": "<string or null>",
    "coordinates": { "lat": null, "lng": null }
  },
  "reporter": {
    "name": "<string or null>",
    "phone": "<string or null>",
    "is_anonymous": false
  },
  "casualties": {
    "injured": 0,
    "dead": 0,
    "trapped": 0
  },
  "hazards": [],
  "required_services": [],
  "additional_notes": "<string or null>",
  "question": "<REQUIRED when status=incomplete: single conversational follow-up question>"
}
"""

# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip().strip("`").strip())


def call_gemini(api_key: str, gemini_history: list, new_message: str) -> dict:
    genai.configure(api_key=api_key)
    # Try modern model names in order, fall back gracefully
    for model_name in [
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash-preview-04-17",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-pro",
    ]:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_PROMPT,
            )
            chat = model.start_chat(history=gemini_history)
            response = chat.send_message(new_message)
            return parse_json(response.text)
        except Exception as e:
            last_err = e
            if "not found" in str(e).lower() or "404" in str(e):
                continue
            raise
    raise last_err


def frow(label: str, value) -> str:
    has = value is not None and value != "" and value != [] and value is not False
    dot = '<span class="dot-ok">●</span>' if has else '<span class="dot-miss">○</span>'
    if has:
        display = ', '.join(str(v) for v in value) if isinstance(value, list) else str(value)
        val_html = f'<span class="field-val">{display}</span>'
    else:
        val_html = '<span class="field-null">not yet collected</span>'
    return f'<div class="field-row">{dot}&nbsp;<span class="field-label">{label}</span>{val_html}</div>'


def render_field_progress(inc: dict):
    if not inc:
        st.caption("No data collected yet.")
        return
    loc = inc.get("location") or {}
    cas = inc.get("casualties") or {}
    rep = inc.get("reporter") or {}
    sev = inc.get("severity") or ""
    sev_html = (
        f'<span class="sev-{sev}">{sev.upper()}</span>'
        if sev else '<span class="field-null">not yet collected</span>'
    )
    dot_sev = "●" if sev else "○"
    cls_sev = "dot-ok" if sev else "dot-miss"
    rows = [
        frow("incident_type",  inc.get("incident_type")),
        f'<div class="field-row"><span class="{cls_sev}">{dot_sev}</span>&nbsp;'
        f'<span class="field-label">severity</span>{sev_html}</div>',
        frow("description",    inc.get("description")),
        frow("city",           loc.get("city")),
        frow("address",        loc.get("address")),
        frow("landmark",       loc.get("landmark")),
        frow("trapped",        cas.get("trapped") if cas.get("trapped") else None),
        frow("injured",        cas.get("injured") if cas.get("injured") else None),
        frow("dead",           cas.get("dead")    if cas.get("dead")    else None),
        frow("hazards",        inc.get("hazards") if inc.get("hazards") else None),
        frow("services",       inc.get("required_services") if inc.get("required_services") else None),
        frow("reporter name",  rep.get("name")),
        frow("reporter phone", rep.get("phone")),
        frow("notes",          inc.get("additional_notes")),
    ]
    st.markdown("".join(rows), unsafe_allow_html=True)


# ─── Web Speech API Component ─────────────────────────────────────────────────
# Uses window.SpeechRecognition (built into Chrome, Edge, Safari 15+).
# Zero cost, zero latency, genuinely live word-by-word output.
# Pushes transcribed text into the hidden Streamlit bridge input via DOM
# injection so the textarea updates on every Streamlit rerun.

SPEECH_COMPONENT = """
<div style="font-family:'Barlow',sans-serif;">

  <div id="no-support" style="display:none; background:#2a1000; border:1px solid #cc6600;
       border-radius:6px; padding:8px 12px; color:#ffaa55; margin-bottom:10px; font-size:12px;">
    ⚠️ Web Speech API not available in this browser.
    Please open in <strong>Chrome</strong>, <strong>Edge</strong>, or <strong>Safari 15+</strong>.
  </div>

  <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px;">
    <button id="micBtn" onclick="toggleMic()" style="
        background:#b81c1c; color:#fff; border:none; border-radius:6px;
        padding:9px 22px; font-size:14px; font-weight:700; cursor:pointer;
        display:flex; align-items:center; gap:8px;
    ">
      <span id="micIcon" style="font-size:16px;">🎙</span>
      <span id="micLabel">Start listening</span>
    </button>

    <button onclick="clearAll()" style="
        background:#111d30; color:#6a9acf; border:1px solid #1e3a5a;
        border-radius:6px; padding:9px 16px; font-size:13px;
        font-weight:600; cursor:pointer;
    ">Clear</button>

    <span id="statusMsg" style="font-size:12px; color:#3a6a9a; font-family:'Share Tech Mono',monospace;"></span>
  </div>

  <div style="
      background:#05101f; border:1px solid #132840;
      border-radius:8px; padding:12px 16px;
      min-height:60px; font-size:14px; line-height:1.8;
      word-break:break-word; white-space:pre-wrap;
  ">
    <span id="finalSpan"   style="color:#a8d4f5;"></span><span
          id="interimSpan" style="color:#2e5f8a; font-style:italic;"></span>
    <span id="placeholder" style="color:#1e3d5e; font-style:italic;">
      Transcription appears here as you speak…
    </span>
  </div>

  <div style="margin-top:5px; font-size:11px; color:#1e3a5a;">
    Confirmed words: bright blue &nbsp;·&nbsp; Still processing: grey italic
  </div>

</div>

<script>
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!SR) {
  document.getElementById('no-support').style.display = 'block';
  document.getElementById('micBtn').disabled = true;
  document.getElementById('micBtn').style.background = '#333';
}

let rec = null, active = false, finalText = '';

function setStatus(msg, color) {
  const el = document.getElementById('statusMsg');
  el.textContent = msg; el.style.color = color || '#3a6a9a';
}

function updateDisplay(fin, interim) {
  document.getElementById('finalSpan').textContent   = fin;
  document.getElementById('interimSpan').textContent = interim;
  document.getElementById('placeholder').style.display =
    (fin || interim) ? 'none' : 'inline';
}

function pushToStreamlit(val) {
  // Walk the parent document looking for the hidden bridge input
  // identified by its placeholder attribute __STT_BRIDGE__
  const all = window.parent.document.querySelectorAll('input');
  for (const inp of all) {
    if (inp.placeholder === '__STT_BRIDGE__') {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(inp, val);
      inp.dispatchEvent(new Event('input', {bubbles: true}));
      return;
    }
  }
}

function clearAll() {
  finalText = '';
  updateDisplay('', '');
  pushToStreamlit('');
  setStatus('Cleared', '#3a6a9a');
}

function toggleMic() {
  active ? stopRec() : startRec();
}

function startRec() {
  if (!SR) return;
  rec = new SR();
  rec.continuous     = true;
  rec.interimResults = true;
  rec.lang           = 'en-IN';   // English (India) — change to 'en-US' if preferred

  rec.onstart = () => {
    active = true;
    document.getElementById('micBtn').style.background = '#0d5c1a';
    document.getElementById('micLabel').textContent    = 'Stop listening';
    document.getElementById('micIcon').textContent     = '⏹';
    setStatus('🔴 Listening…', '#ff4040');
  };

  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) finalText += t + ' ';
      else interim += t;
    }
    updateDisplay(finalText, interim);
    pushToStreamlit((finalText + interim).trim());
  };

  rec.onerror = (e) => {
    setStatus('Mic error: ' + e.error, '#ff4040');
    if (e.error === 'not-allowed') {
      document.getElementById('no-support').style.display = 'block';
      document.getElementById('no-support').textContent  =
        '❌ Microphone permission denied. Please allow mic access and reload.';
    }
    stopRec();
  };

  rec.onend = () => {
    // Auto-restart to keep continuous listening while active flag is true
    if (active) { try { rec.start(); } catch(_) {} }
  };

  rec.start();
}

function stopRec() {
  active = false;
  if (rec) { try { rec.stop(); } catch(_) {} rec = null; }
  document.getElementById('micBtn').style.background = '#b81c1c';
  document.getElementById('micLabel').textContent    = 'Start listening';
  document.getElementById('micIcon').textContent     = '🎙';
  setStatus('✅ Done — text ready below', '#2ecc71');
  pushToStreamlit(finalText.trim());
}
</script>
"""


# ─── Session State ────────────────────────────────────────────────────────────
def init_state():
    defaults = {
        "history":        [],
        "gemini_history": [],
        "incident":       None,
        "incident_id":    str(uuid.uuid4()),
        "completed":      False,
        "log":            [],
        "stt_text":       "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚨 Crisis Intake")
    st.markdown("---")
    api_key = st.text_input("Gemini API Key", type="password",
                             placeholder="AIza…", key="api_key_input")
    if not api_key:
        st.warning("Enter your Gemini API key to begin.")

    st.markdown("---")
    st.markdown("### 📊 Field Progress")
    render_field_progress(st.session_state.incident)

    st.markdown("---")
    if st.button("🔄 New Incident", use_container_width=True):
        for k in ["history", "gemini_history", "incident", "completed", "stt_text"]:
            if k in st.session_state:
                del st.session_state[k]
        st.session_state.incident_id = str(uuid.uuid4())
        init_state()
        st.rerun()

    if st.session_state.log:
        st.markdown("---")
        st.markdown("### 📁 Completed")
        for logged in reversed(st.session_state.log[-5:]):
            t = (logged.get("incident_type") or "?").upper()
            c = (logged.get("location") or {}).get("city") or "?"
            s = (logged.get("severity") or "?").upper()
            st.caption(f"[{t}] {c} — {s}")

# ─── Main Layout ──────────────────────────────────────────────────────────────
st.markdown("# 🚨 Disaster Incident Intake Agent")
st.markdown("Describe the emergency. The agent collects all required details one question at a time.")
st.markdown("---")

chat_col, json_col = st.columns([3, 2], gap="large")

# ══ LEFT: Chat + Voice ══
with chat_col:

    # ── Conversation history ────────────────────────────────────────────────
    if not st.session_state.history:
        st.markdown("""
        <div class="agent-question">
        🎙️ <b>Agent:</b> Emergency Intake online. Please describe the incident — what is happening and where?
        </div>
        """, unsafe_allow_html=True)
    else:
        for turn in st.session_state.history:
            if turn["role"] == "user":
                st.markdown(
                    f'<div class="user-bubble">👤 <b>You:</b> {turn["text"]}</div>',
                    unsafe_allow_html=True)
            else:
                cls = "agent-question" if turn.get("is_question") else "agent-bubble"
                ico = "❓" if turn.get("is_question") else "🤖"
                st.markdown(
                    f'<div class="{cls}">{ico} <b>Agent:</b> {turn["text"]}</div>',
                    unsafe_allow_html=True)

    if st.session_state.completed:
        st.markdown(
            '<div class="complete-banner">✅ INCIDENT REPORT COMPLETE — Dispatched to response teams.</div>',
            unsafe_allow_html=True)

    # ── Input area ─────────────────────────────────────────────────────────
    if not st.session_state.completed:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("### 🎤 Live Voice Input")
        st.caption("Built-in browser speech recognition — works in Chrome / Edge / Safari 15+. No API key required.")

        # Render voice widget inside iframe
        components.html(SPEECH_COMPONENT, height=180, scrolling=False)

        # Hidden bridge: JS in the iframe writes into this input's value
        # using its placeholder as a selector key (__STT_BRIDGE__)
        stt_bridge = st.text_input(
            "stt_bridge",
            key="stt_bridge_widget",
            label_visibility="collapsed",
            placeholder="__STT_BRIDGE__",
        )

        # Sync bridge → session state so the textarea below stays updated
        if stt_bridge != st.session_state.get("stt_text", ""):
            st.session_state.stt_text = stt_bridge

        st.markdown("---")
        st.markdown("##### ✏️ Review & Send")

        with st.form("msg_form", clear_on_submit=True):
            user_input = st.text_area(
                "Message",
                value=st.session_state.stt_text,
                placeholder="Voice text appears here automatically. You can also type directly.",
                label_visibility="collapsed",
                height=100,
                disabled=not api_key,
            )
            c1, c2 = st.columns([1, 3])
            with c1:
                send_btn = st.form_submit_button(
                    "📡 Send to Agent",
                    disabled=not api_key,
                    use_container_width=True,
                )
            with c2:
                st.markdown(
                    "<small style='color:#1e3a5a;'>Stop mic first, edit if needed, then Send.</small>",
                    unsafe_allow_html=True,
                )

        if send_btn and user_input.strip() and api_key:
            st.session_state.stt_text = ""
            user_text   = user_input.strip()
            context_msg = (
                f"[incident_id={st.session_state.incident_id}] "
                f"[time={datetime.now(timezone.utc).isoformat()}]\n"
                f"Caller: {user_text}"
            )
            with st.spinner("Agent processing…"):
                try:
                    response = call_gemini(api_key, st.session_state.gemini_history, context_msg)
                except Exception as e:
                    st.error(f"Gemini API error: {e}")
                    st.stop()

            st.session_state.gemini_history.append({"role": "user",  "parts": [context_msg]})
            st.session_state.gemini_history.append({"role": "model", "parts": [json.dumps(response)]})
            st.session_state.incident = response

            if response.get("status") == "complete":
                agent_text  = "Thank you. I have all the information needed. The incident report is complete and dispatched to emergency response teams."
                is_question = False
                st.session_state.completed = True
                st.session_state.log.append(response)
            else:
                agent_text  = response.get("question", "Can you provide more details?")
                is_question = True

            st.session_state.history.append({"role": "user",  "text": user_text})
            st.session_state.history.append({"role": "agent", "text": agent_text, "is_question": is_question})
            st.rerun()

    else:
        # ── Download and Send to Triage ────────────────────────────────────
        col1, col2 = st.columns(2)
        
        with col1:
            if st.session_state.incident:
                st.download_button(
                    "⬇️ Download Incident JSON",
                    data=json.dumps(st.session_state.incident, indent=2),
                    file_name=f"incident_{st.session_state.incident_id[:8]}.json",
                    mime="application/json",
                )
        
        with col2:
            if st.session_state.incident:
                if st.button("🧠 Send to Triage Agent", use_container_width=True):
                    # Store the incident JSON in session state for the triage page
                    st.session_state.shared_incident_json = json.dumps(
                        st.session_state.incident, indent=2
                    )
                    st.success("✅ Incident data transferred! Navigating to Triage Agent...")
                    st.balloons()
                    # Navigate to triage page
                    st.switch_page("triage_agent.py")

# ══ RIGHT: Live JSON ══
with json_col:
    st.markdown("### 📄 Live Incident JSON")
    inc = st.session_state.incident

    if inc is None:
        st.markdown("""
        <div style="
            color:#2a3d5a; font-family:'Share Tech Mono',monospace;
            padding:30px 20px; border:1px dashed #1a2d4a;
            border-radius:8px; font-size:0.82rem; text-align:center;
            margin-top:10px; line-height:2;
        ">
        { }<br><span style="font-size:0.72rem">Waiting for first message…</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        display_inc = {k: v for k, v in inc.items() if k != "fields_collected"}
        st.code(json.dumps(display_inc, indent=2), language="json")

        if inc.get("status") == "complete":
            st.success("✅ Report complete")
        else:
            checks = [
                inc.get("incident_type"),
                inc.get("severity"),
                inc.get("description"),
                (inc.get("location") or {}).get("city") or (inc.get("location") or {}).get("landmark"),
                (inc.get("casualties") or {}).get("trapped") is not None,
                inc.get("hazards"),
                inc.get("required_services"),
                (inc.get("reporter") or {}).get("name"),
            ]
            filled = sum(1 for c in checks if c)
            pct    = filled / len(checks)
            st.progress(pct, text=f"Collecting… ({filled}/{len(checks)} key fields)")