"""
Triage Agent — powered by Gemini API (google-genai)
----------------------------------------------------
Central orchestrator in the disaster response pipeline.

Receives a structured incident JSON (from Crisis Intake Agent),
then uses Gemini to:
  • Classify and re-validate incident type & severity
  • Build an action plan and SOP
  • Assign tasks to Coordination agent
  • Decide which downstream agents to activate
  • Produce comms payload for the Communications Agent

Handles both "complete" and "incomplete" incident statuses.
For incomplete incidents, displays the follow-up question and
allows triage to proceed with available information.

Install:
    pip install streamlit google-genai

Run:
    streamlit run triage_agent.py
"""

import streamlit as st
import json
import re
import os
import uuid
from datetime import datetime, timezone

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Triage Agent",
    page_icon="🧠",
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
    color: #f0a500 !important;
    letter-spacing: 0.06em;
}

.triage-card {
    background: #0f1828;
    border-left: 3px solid #f0a500;
    padding: 12px 16px;
    border-radius: 3px 10px 10px 3px;
    margin: 8px 0;
    font-size: 0.92rem;
    color: #dde6f5;
}
.route-card {
    background: #0e1f10;
    border-left: 3px solid #4caf50;
    padding: 10px 14px;
    border-radius: 3px 10px 10px 3px;
    margin: 6px 0;
    font-size: 0.9rem;
    color: #7ed47e;
}
.sop-card {
    background: #0c1830;
    border-left: 3px solid #3a7bd5;
    padding: 12px 16px;
    border-radius: 3px 10px 10px 3px;
    margin: 8px 0;
    font-size: 0.9rem;
    color: #a8c8f0;
    line-height: 1.7;
}
.alert-banner {
    background: linear-gradient(135deg, #1a0a00, #2a1000);
    border: 1px solid #ff4040;
    border-radius: 8px;
    padding: 14px 18px;
    color: #ff6060;
    font-family: 'Share Tech Mono', monospace;
    font-size: 1rem;
    margin: 12px 0;
    text-align: center;
    letter-spacing: 0.05em;
}
.complete-banner {
    background: linear-gradient(135deg, #0b2e18, #07180d);
    border: 1px solid #2ecc71;
    border-radius: 8px;
    padding: 14px 18px;
    color: #2ecc71;
    font-family: 'Share Tech Mono', monospace;
    font-size: 1rem;
    margin: 12px 0;
    text-align: center;
    letter-spacing: 0.05em;
}
.incomplete-banner {
    background: linear-gradient(135deg, #1a1200, #2a1e00);
    border: 1px solid #f0a500;
    border-radius: 8px;
    padding: 14px 18px;
    color: #f0a500;
    font-family: 'Share Tech Mono', monospace;
    font-size: 1rem;
    margin: 12px 0;
    text-align: center;
    letter-spacing: 0.05em;
}
.question-card {
    background: #12100a;
    border-left: 3px solid #f0a500;
    border-radius: 3px 10px 10px 3px;
    padding: 14px 18px;
    margin: 10px 0;
    color: #f5d98a;
    font-size: 0.95rem;
    line-height: 1.7;
}
.question-label {
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.72rem;
    color: #7a5c00;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.field-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 3px 0;
    font-size: 0.82rem;
    font-family: 'Share Tech Mono', monospace;
}
.field-label { color: #5580aa; min-width: 130px; }
.field-val   { color: #a8c8f0; word-break: break-word; }
.field-null  { color: #2a3d5a; font-style: italic; }
.sev-critical { color: #ff2b2b !important; font-weight: 700; }
.sev-high     { color: #ff8c00 !important; font-weight: 700; }
.sev-medium   { color: #ffd700 !important; font-weight: 600; }
.sev-low      { color: #7cfc00 !important; }

.status-badge-incomplete {
    display: inline-block;
    background: #2a1e00;
    border: 1px solid #f0a500;
    color: #f0a500;
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.72rem;
    padding: 2px 8px;
    border-radius: 4px;
    letter-spacing: 0.08em;
    margin-left: 8px;
    vertical-align: middle;
}
.status-badge-complete {
    display: inline-block;
    background: #0a2e14;
    border: 1px solid #2ecc71;
    color: #2ecc71;
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.72rem;
    padding: 2px 8px;
    border-radius: 4px;
    letter-spacing: 0.08em;
    margin-left: 8px;
    vertical-align: middle;
}

.stTextArea textarea {
    background: #0e1828 !important;
    color: #dde6f5 !important;
    border: 1px solid #1a2d4a !important;
    border-radius: 6px;
    font-family: 'Share Tech Mono', monospace !important;
    font-size: 0.85rem !important;
}
.stButton > button {
    background: #b87800 !important;
    color: white !important;
    border: none !important;
    border-radius: 6px !important;
    font-family: 'Barlow', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: 0.04em;
}
.stButton > button:hover { background: #8a5a00 !important; }
</style>
""", unsafe_allow_html=True)

# ─── System Prompt ─────────────────────────────────────────────────────────────
TRIAGE_SYSTEM_PROMPT = """You are the Triage Agent in a multi-agent disaster response system.
You sit at the center of the pipeline and receive structured incident reports from the Intake Agent.

The incident may have status "complete" or "incomplete". 
- For "incomplete" incidents, triage using the best available information. Note any critical missing data in additional_notes.
- The input may contain a "question" field — this is a pending follow-up question from the Intake Agent. Acknowledge it in additional_notes if relevant.

Your responsibilities:
1. CLASSIFY — validate/correct incident_type and severity based on all available details.
2. PRIORITISE — assign a numeric priority score (1=highest, 5=lowest) and an escalation_level.
3. ACTION PLAN — write a concise, numbered step-by-step action plan for first responders.
4. SOP — reference which Standard Operating Procedure(s) apply (e.g. "SOP-FIRE-002", "SOP-MED-007").
5. ROUTE — decide which downstream agents must be activated:
   - response_agent    → always activated; receives action_plan + SOP
   - coordination_agent→ activated when multiple services or inter-agency coordination is needed
   - comms_agent       → always activated; receives summary + alert level for broadcasts
   - escalate_to_command → true only for critical/mass-casualty incidents
6. COMMS PAYLOAD — produce a short alert message (≤60 words) for the Comms Agent to broadcast.
7. COORDINATION PAYLOAD — list task assignments per required_service unit.

Output ONLY raw JSON. No markdown. No explanations. No extra text.

## OUTPUT SCHEMA
{
  "triage_id": "<UUID>",
  "triaged_at": "<ISO 8601 UTC>",
  "incident_id": "<from input>",
  "intake_status": "<complete | incomplete — copied from input status field>",
  "pending_question": "<copy the question field from input if present, else null>",
  "incident_type": "<validated type>",
  "severity": "low | medium | high | critical",
  "priority_score": <1-5 integer>,
  "escalation_level": "routine | urgent | major | critical",
  "classification_notes": "<one sentence: why this severity/priority>",

  "action_plan": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ..."
  ],
  "sop_references": ["SOP-XXX-000"],

  "routing": {
    "response_agent":     true,
    "coordination_agent": <true|false>,
    "comms_agent":        true,
    "escalate_to_command":<true|false>
  },

  "comms_payload": {
    "alert_level": "green | amber | red | black",
    "broadcast_message": "<≤60 word alert for dispatch broadcast>",
    "affected_area": "<city / zone>",
    "required_services": ["fire", "ambulance", "police"]
  },

  "coordination_payload": {
    "task_assignments": [
      { "unit": "<service name>", "task": "<specific task for this unit>" }
    ],
    "on_scene_commander": "<recommended lead agency>",
    "staging_area": "<suggested staging location or null>"
  },

  "post_incident_flag": <true|false>,
  "additional_notes": "<triage observations, notes about missing data from incomplete report, or null>"
}
"""

# ─── Sample Incident JSON ──────────────────────────────────────────────────────
SAMPLE_INCIDENT = """{
  "status": "complete",
  "incident_id": "0556ec85-afca-4c2f-9403-868ac29eb59c",
  "reported_at": "2026-04-23T13:21:34.231620+00:00",
  "incident_type": "fire",
  "severity": "critical",
  "description": "Fire on 4th floor of ABC apartments, smoke and flames visible under the door. 5 people are trapped inside the caller's room.",
  "location": {
    "address": "4th floor, ABC apartments",
    "landmark": null,
    "city": "Chennai",
    "coordinates": {
      "lat": null,
      "lng": null
    }
  },
  "reporter": {
    "name": "Anish",
    "phone": "7417450999",
    "is_anonymous": false
  },
  "casualties": {
    "injured": 0,
    "dead": 0,
    "trapped": 5
  },
  "hazards": [
    "fire"
  ],
  "required_services": [
    "fire brigade",
    "rescue",
    "ambulance"
  ],
  "additional_notes": null,
  "question": null
}"""

# ─── Helpers ──────────────────────────────────────────────────────────────────

def extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from Gemini.\n\nRaw:\n{text[:2000]}")


def call_triage_gemini(api_key: str, incident: dict) -> dict:
    client = genai.Client(api_key=api_key)

    prompt = f"""Incident report to triage:
{json.dumps(incident, indent=2)}

Triage this incident and return ONLY the JSON output per your schema."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=TRIAGE_SYSTEM_PROMPT,
            temperature=0.15,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return extract_json(response.text)


def sev_class(sev: str) -> str:
    return f"sev-{sev}" if sev in ("critical", "high", "medium", "low") else ""


def frow(label: str, value) -> str:
    has = value is not None and value != "" and value != []
    dot = '<span style="color:#2ecc71">●</span>' if has else '<span style="color:#2a3d5a">○</span>'
    if has:
        display = ', '.join(str(v) for v in value) if isinstance(value, list) else str(value)
        val_html = f'<span class="field-val">{display}</span>'
    else:
        val_html = '<span class="field-null">—</span>'
    return f'<div class="field-row">{dot}&nbsp;<span class="field-label">{label}</span>{val_html}</div>'


# ─── Session State ─────────────────────────────────────────────────────────────
if "triage_result" not in st.session_state:
    st.session_state.triage_result = None
if "triage_log" not in st.session_state:
    st.session_state.triage_log = []

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🧠 Triage Agent")
    st.markdown("---")
    api_key = st.text_input(
        "Gemini API Key",
        type="password",
        placeholder="AIza…",
        value=os.environ.get("GEMINI_API_KEY", ""),
    )
    if not api_key:
        st.warning("Enter your Gemini API key to begin.")

    st.markdown("---")
    st.markdown("### 📋 Triage Summary")
    t = st.session_state.triage_result
    if t:
        sev = t.get("severity", "")
        sev_html = f'<span class="sev-{sev}">{sev.upper()}</span>' if sev else "—"
        intake_status = t.get("intake_status", "")
        status_badge = (
            f'<span class="status-badge-incomplete">INCOMPLETE</span>'
            if intake_status == "incomplete"
            else f'<span class="status-badge-complete">COMPLETE</span>'
        )
        rows = [
            frow("incident_id",    (t.get("incident_id", "") or "")[:12] + "…"),
            f'<div class="field-row"><span style="color:#2ecc71">●</span>&nbsp;<span class="field-label">intake_status</span>{status_badge}</div>',
            f'<div class="field-row"><span style="color:#2ecc71">●</span>&nbsp;<span class="field-label">severity</span>{sev_html}</div>',
            frow("priority_score", t.get("priority_score")),
            frow("escalation",     t.get("escalation_level")),
            frow("alert_level",    (t.get("comms_payload") or {}).get("alert_level")),
            frow("sop_refs",       t.get("sop_references")),
            frow("resp_agent",     "✔ active" if (t.get("routing") or {}).get("response_agent") else None),
            frow("coord_agent",    "✔ active" if (t.get("routing") or {}).get("coordination_agent") else None),
            frow("comms_agent",    "✔ active" if (t.get("routing") or {}).get("comms_agent") else None),
            frow("cmd_escalate",   "⚠ YES" if (t.get("routing") or {}).get("escalate_to_command") else None),
            frow("pending_q",      "⚠ YES" if t.get("pending_question") else None),
        ]
        st.markdown("".join(rows), unsafe_allow_html=True)
    else:
        st.caption("No triage result yet.")

    st.markdown("---")
    if st.button("🔄 Clear", use_container_width=True):
        st.session_state.triage_result = None
        st.rerun()

    if st.session_state.triage_log:
        st.markdown("---")
        st.markdown("### 📁 Recent Triages")
        for entry in reversed(st.session_state.triage_log[-5:]):
            itype  = (entry.get("incident_type") or "?").upper()
            sev    = (entry.get("severity") or "?").upper()
            city   = (entry.get("comms_payload") or {}).get("affected_area") or "?"
            status = entry.get("intake_status", "?")
            badge  = "⚠" if status == "incomplete" else "✓"
            st.caption(f"{badge} [{itype}] {city} — {sev}")

# ─── Main Layout ──────────────────────────────────────────────────────────────
st.markdown("# 🧠 Triage Agent")
st.markdown("Paste a completed **or incomplete** incident JSON from the Intake Agent. Gemini will classify, prioritise, and route it.")
st.markdown("---")

input_col, output_col = st.columns([2, 3], gap="large")
incoming_data = st.session_state.get("shared_incident_json", SAMPLE_INCIDENT)
# ── LEFT: Input ──
with input_col:
    st.markdown("### 📥 Incident JSON Input")
    incident_json = st.text_area(
        "Paste incident JSON",
        value=incoming_data,
        height=420,
        label_visibility="collapsed",
        disabled=not api_key,
    )

    # Parse and show live status + question preview
    try:
        _preview = json.loads(incident_json)
        _status  = _preview.get("status", "unknown")
        _q       = _preview.get("question")
        if _status == "incomplete":
            st.markdown(
                '<div class="incomplete-banner">⚠ INCOMPLETE REPORT — Triage will proceed with available data</div>',
                unsafe_allow_html=True,
            )
        if _q:
            st.markdown(
                f'<div class="question-card">'
                f'<div class="question-label">📣 Pending intake question</div>'
                f'{_q}'
                f'</div>',
                unsafe_allow_html=True,
            )
    except (json.JSONDecodeError, AttributeError):
        pass

    triage_btn = st.button(
        "🧠 Run Triage",
        disabled=not api_key,
        use_container_width=True,
    )

    if triage_btn and api_key:
        if not GENAI_AVAILABLE:
            st.error("Missing dependency. Run:  pip install google-genai")
            st.stop()
        try:
            incident_data = json.loads(incident_json)
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")
            st.stop()

        with st.spinner("🧠 Gemini is triaging the incident…"):
            try:
                result = call_triage_gemini(api_key.strip(), incident_data)
            except Exception as e:
                st.error(f"Gemini API error: {e}")
                st.stop()

        # Ensure triage_id and triaged_at are present
        if not result.get("triage_id"):
            result["triage_id"] = str(uuid.uuid4())
        if not result.get("triaged_at"):
            result["triaged_at"] = datetime.now(timezone.utc).isoformat()

        # Carry over intake_status and pending_question if Gemini missed them
        if not result.get("intake_status"):
            result["intake_status"] = incident_data.get("status", "unknown")
        if not result.get("pending_question"):
            result["pending_question"] = incident_data.get("question")

        st.session_state.triage_result = result
        st.session_state.triage_log.append(result)
        st.rerun()

    # Download completed triage
    if st.session_state.triage_result:
        tid = st.session_state.triage_result.get("triage_id", "triage")[:8]
        st.download_button(
            "⬇️ Download Triage JSON",
            data=json.dumps(st.session_state.triage_result, indent=2),
            file_name=f"triage_{tid}.json",
            mime="application/json",
        )

        st.session_state.shared_triage_json = json.dumps(st.session_state.triage_result, indent=2)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➡️ Approve & Send to Comms Agent", use_container_width=True):
            st.switch_page("comm_agent.py")

# ── RIGHT: Output ──
with output_col:
    st.markdown("### 📤 Triage Output")
    result = st.session_state.triage_result

    if result is None:
        st.markdown("""
        <div style="
            color:#2a3d5a;
            font-family:'Share Tech Mono',monospace;
            padding:40px 20px;
            border:1px dashed #1a2d4a;
            border-radius:8px;
            font-size:0.82rem;
            text-align:center;
            margin-top:10px;
            line-height:2.2;
        ">
        { }<br><span style="font-size:0.72rem">Awaiting triage run…</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        sev           = result.get("severity", "")
        escl          = result.get("escalation_level", "")
        prio          = result.get("priority_score", "?")
        alert         = (result.get("comms_payload") or {}).get("alert_level", "")
        routing       = result.get("routing") or {}
        intake_status = result.get("intake_status", "")
        pending_q     = result.get("pending_question")

        # Status banner
        if sev == "critical" or routing.get("escalate_to_command"):
            suffix = " | ⚠ INCOMPLETE DATA" if intake_status == "incomplete" else ""
            st.markdown(
                f'<div class="alert-banner">🚨 CRITICAL — COMMAND ESCALATION REQUIRED | Priority {prio}{suffix}</div>',
                unsafe_allow_html=True,
            )
        elif intake_status == "incomplete":
            st.markdown(
                f'<div class="incomplete-banner">⚠ TRIAGE ON INCOMPLETE REPORT | {escl.upper()} | Priority {prio} | Alert: {alert.upper()}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="complete-banner">✅ TRIAGE COMPLETE | {escl.upper()} | Priority {prio} | Alert: {alert.upper()}</div>',
                unsafe_allow_html=True,
            )

        # Pending question notice (always show if present)
        if pending_q:
            st.markdown(
                f'<div class="question-card">'
                f'<div class="question-label">📣 Pending intake question (awaiting reporter response)</div>'
                f'{pending_q}'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Tabs for structured view ──
        tab1, tab2, tab3, tab4, tab5 = st.tabs(
            ["📋 Classification", "📜 Action Plan & SOP", "📡 Routing", "📣 Comms Payload", "🗂 Raw JSON"]
        )

        with tab1:
            status_badge_html = (
                '<span style="color:#f0a500;font-size:0.8rem"> ⚠ INCOMPLETE</span>'
                if intake_status == "incomplete"
                else '<span style="color:#2ecc71;font-size:0.8rem"> ✓ COMPLETE</span>'
            )
            st.markdown(f"""
            <div class="triage-card">
            <b>Incident Type:</b> {result.get('incident_type','—').upper()}<br>
            <b>Severity:</b> <span class="{sev_class(sev)}">{sev.upper()}</span><br>
            <b>Priority Score:</b> {prio} / 5<br>
            <b>Escalation Level:</b> {escl.upper()}<br>
            <b>Intake Status:</b> {intake_status.upper()}{status_badge_html}<br>
            <b>Notes:</b> {result.get('classification_notes','—')}
            </div>
            """, unsafe_allow_html=True)

            if result.get("additional_notes"):
                st.markdown(f"""
                <div class="triage-card" style="border-left-color:#5580aa">
                <b>Additional Triage Notes:</b><br>
                {result.get('additional_notes')}
                </div>
                """, unsafe_allow_html=True)

        with tab2:
            steps = result.get("action_plan") or []
            sops  = result.get("sop_references") or []
            plan_html = "<br>".join(f"<b>{i+1}.</b> {s}" for i, s in enumerate(steps)) or "—"
            sop_html  = ", ".join(f"<code>{s}</code>" for s in sops) or "—"
            st.markdown(f"""
            <div class="sop-card">
            <b>SOPs:</b> {sop_html}<br><br>
            {plan_html}
            </div>
            """, unsafe_allow_html=True)

        with tab3:
            r = result.get("routing") or {}
            active_agents = []
            if r.get("response_agent"):     active_agents.append("✅ Response Agent — Action plan + SOP")
            if r.get("coordination_agent"): active_agents.append("✅ Coordination Agent — Task assignments + escalation")
            if r.get("comms_agent"):        active_agents.append("✅ Comms Agent — Alerts + broadcasts")
            if r.get("escalate_to_command"):active_agents.append("⚠️ Command Dashboard — Command escalation required")

            for line in active_agents:
                cls = "alert-banner" if "Command" in line else "route-card"
                st.markdown(f'<div class="{cls}">{line}</div>', unsafe_allow_html=True)

            cp = result.get("coordination_payload") or {}
            tasks = cp.get("task_assignments") or []
            if tasks:
                st.markdown("**Task Assignments:**")
                for t in tasks:
                    st.markdown(
                        f'<div class="triage-card"><b>{t.get("unit","?")}</b>: {t.get("task","")}</div>',
                        unsafe_allow_html=True,
                    )
            if cp.get("on_scene_commander"):
                st.markdown(f"**On-scene commander:** {cp['on_scene_commander']}")
            if cp.get("staging_area"):
                st.markdown(f"**Staging area:** {cp['staging_area']}")

        with tab4:
            cp = result.get("comms_payload") or {}
            al = cp.get("alert_level", "")
            color_map = {"black": "#ff2b2b", "red": "#ff6060", "amber": "#f0a500", "green": "#2ecc71"}
            color = color_map.get(al, "#aaa")
            st.markdown(f"""
            <div class="triage-card" style="border-left-color:{color}">
            <b>Alert Level:</b> <span style="color:{color};font-weight:700">{al.upper()}</span><br>
            <b>Affected Area:</b> {cp.get('affected_area','—')}<br>
            <b>Required Services:</b> {', '.join(cp.get('required_services',[]) or [])}<br><br>
            <b>Broadcast Message:</b><br>
            <span style="color:#dde6f5;font-size:1rem">{cp.get('broadcast_message','—')}</span>
            </div>
            """, unsafe_allow_html=True)

        with tab5:
            st.code(json.dumps(result, indent=2), language="json")