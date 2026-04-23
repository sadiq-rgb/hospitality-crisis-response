"""
Communications Agent — powered by Gemini API (google-genai)
------------------------------------------------------------
Gemini decides EVERYTHING:
  • Which responders are closest (coordinate reasoning)
  • The incident summary
  • Tailored dispatch message per responder unit

Install:
    pip install streamlit networkx matplotlib google-genai

Run:
    streamlit run app.py
"""

import streamlit as st
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import math, time, json, os

# ── Gemini SDK ───────────────────────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Comms Agent", page_icon="🚨", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Rajdhani', sans-serif;
    background-color: #0d0f14;
    color: #e0e6f0;
}
h1, h2, h3 { font-family: 'Share Tech Mono', monospace; }
.stApp { background-color: #0d0f14; }

section[data-testid="stSidebar"] {
    background-color: #111520;
    border-right: 1px solid #1e2a3a;
}
.block-container { padding-top: 2rem; }

div[data-testid="stTextArea"] textarea {
    background-color: #111520 !important;
    color: #7efff5 !important;
    font-family: 'Share Tech Mono', monospace !important;
    font-size: 13px !important;
    border: 1px solid #1e3a52 !important;
    border-radius: 4px !important;
}
div[data-testid="stTextInput"] input {
    background-color: #111520 !important;
    color: #c0d8f0 !important;
    font-family: 'Share Tech Mono', monospace !important;
    font-size: 13px !important;
    border: 1px solid #1e3a52 !important;
}
div.stButton > button {
    background: linear-gradient(135deg, #c0392b, #e74c3c);
    color: white;
    font-family: 'Share Tech Mono', monospace;
    font-size: 15px; font-weight: bold; letter-spacing: 2px;
    border: none; border-radius: 4px; padding: 0.6rem 2rem; width: 100%;
    box-shadow: 0 0 12px rgba(231,76,60,0.4);
}
div.stButton > button:hover {
    box-shadow: 0 0 22px rgba(231,76,60,0.7);
}
.alert-box {
    background: #12191f; border-left: 4px solid #e74c3c; border-radius: 4px;
    padding: 1rem 1.4rem; margin-bottom: 0.8rem;
    font-family: 'Share Tech Mono', monospace; color: #f8c6c2; font-size: 14px;
    box-shadow: 0 0 12px rgba(231,76,60,0.15);
}
.gemini-box {
    background: #0c1a10; border-left: 4px solid #27ae60; border-radius: 4px;
    padding: 1rem 1.4rem; margin-bottom: 0.8rem;
    font-family: 'Share Tech Mono', monospace; color: #a8f0c0; font-size: 13px;
    box-shadow: 0 0 10px rgba(39,174,96,0.12);
}
.msg-row {
    background: #0f161d; border: 1px solid #1e2f40; border-radius: 4px;
    padding: 0.6rem 1rem; margin-bottom: 0.5rem;
    font-family: 'Share Tech Mono', monospace; font-size: 12px; color: #8ecae6;
    line-height: 1.8;
}
.msg-row span.sent      { color: #2ecc71; font-weight: bold; }
.msg-row span.node-name { color: #f0c060; font-size: 13px; }
.stat-card {
    background: #111520; border: 1px solid #1e2a3a; border-radius: 6px;
    padding: 0.9rem 1.2rem; text-align: center;
}
.stat-num   { font-size: 2rem; font-family: 'Share Tech Mono', monospace; font-weight: bold; }
.stat-label { font-size: 12px; color: #607080; letter-spacing: 1px; }
.tag-gemini {
    display: inline-block; background: #0c1a10; border: 1px solid #27ae60;
    color: #2ecc71; font-family: 'Share Tech Mono', monospace;
    font-size: 10px; padding: 1px 8px; border-radius: 3px; margin-left: 6px;
    vertical-align: middle;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# MOCK RESPONDER DATA — fed to Gemini as context
# ─────────────────────────────────────────────
RESPONDERS = [
    # Fire stations
    {"name": "Fire Stn Alpha", "type": "fire",     "lat": 13.06, "lng": 80.24},
    {"name": "Fire Stn Bravo", "type": "fire",     "lat": 13.10, "lng": 80.29},
    {"name": "Fire Stn Delta", "type": "fire",     "lat": 13.04, "lng": 80.31},
    # Police
    {"name": "Police Post-1",  "type": "police",   "lat": 13.09, "lng": 80.26},
    {"name": "Police Post-2",  "type": "police",   "lat": 13.07, "lng": 80.22},
    {"name": "Police Post-3",  "type": "police",   "lat": 13.12, "lng": 80.28},
    # Hospitals
    {"name": "City Hospital",  "type": "hospital", "lat": 13.08, "lng": 80.30},
    {"name": "Metro Med Ctr",  "type": "hospital", "lat": 13.05, "lng": 80.25},
    {"name": "Apollo Clinic",  "type": "hospital", "lat": 13.11, "lng": 80.23},
]

NODE_COLORS = {
    "fire":     "#e67e22",
    "police":   "#3498db",
    "hospital": "#2ecc71",
    "llm":      "#e74c3c",
}

DEFAULT_JSON = """{
  "type": "fire",
  "severity": "high",
  "location": {
    "lat": 13.08,
    "lng": 80.27,
    "name": "Anna Nagar"
  },
  "people": 5
}"""

# ─────────────────────────────────────────────
# GEMINI SYSTEM PROMPT
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are an AI emergency communications agent.

You receive:
1. A JSON incident report (type, severity, location with lat/lng, number of people).
2. A list of available responders (name, type, lat, lng).

Your tasks:
A. Calculate which single responder is closest for each type (fire, police, hospital)
   using coordinate distance. Pick exactly ONE per type.
B. Write a concise 1-sentence incident SUMMARY for a dispatcher.
C. Write a short, tailored DISPATCH MESSAGE for each selected unit — specific to their role.

Respond ONLY in this exact JSON format with no extra text or markdown fences:
{
  "summary": "<concise 1-sentence incident summary>",
  "selected": [
    {
      "name": "<exact responder name from the list>",
      "type": "<fire|police|hospital>",
      "distance_km": <float, approximate km from incident>,
      "reason": "<one sentence: why this unit is the closest of its type>",
      "message": "<tailored dispatch message for this unit>"
    }
  ]
}"""

# ─────────────────────────────────────────────
# GEMINI API CALL
# ─────────────────────────────────────────────
import re

def extract_json(text: str) -> dict:
    """Robustly pull the first valid JSON object out of any Gemini response."""
    text = text.strip()

    # 1. Direct parse (ideal case)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Strip ```json ... ``` or ``` ... ``` fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Grab the first { ... } block (handles leading/trailing prose)
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass

    # 4. Nothing worked — surface raw output
    raise ValueError(f"Could not parse JSON from Gemini.\n\nRaw response:\n{text[:1500]}")


def call_gemini(api_key: str, incident: dict) -> dict:
    client = genai.Client(api_key=api_key)

    prompt = f"""Incident report:
{json.dumps(incident, indent=2)}

Available responders:
{json.dumps(RESPONDERS, indent=2)}

Analyze coordinates and return your JSON decision."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.1,
            thinking_config=types.ThinkingConfig(thinking_budget=0),  # disable thinking tokens
        ),
    )

    return extract_json(response.text)

# ─────────────────────────────────────────────
# GRAPH DRAWING
# ─────────────────────────────────────────────
def draw_graph(selected_nodes: list, edges_to_show: list):
    fig, ax = plt.subplots(figsize=(8, 6))
    fig.patch.set_facecolor("#0d0f14")
    ax.set_facecolor("#0d0f14")

    G = nx.DiGraph()
    G.add_node("GEMINI\nAGENT", ntype="llm")
    for n in selected_nodes:
        G.add_node(n["name"], ntype=n["type"])
    for src, dst in edges_to_show:
        G.add_edge(src, dst)

    pos = {"GEMINI\nAGENT": (0, 0)}
    total = max(len(selected_nodes), 1)
    for i, n in enumerate(selected_nodes):
        angle = 2 * math.pi * i / total
        pos[n["name"]] = (math.cos(angle) * 2.6, math.sin(angle) * 2.6)

    node_colors = [NODE_COLORS[G.nodes[n]["ntype"]] for n in G.nodes]
    node_sizes  = [2000 if G.nodes[n]["ntype"] == "llm" else 1300 for n in G.nodes]

    nx.draw_networkx_nodes(G, pos, node_color=node_colors,
                           node_size=node_sizes, alpha=0.93, ax=ax)
    nx.draw_networkx_labels(G, pos, font_color="white",
                            font_size=7.5, font_family="monospace", ax=ax)
    if edges_to_show:
        nx.draw_networkx_edges(
            G, pos, edgelist=edges_to_show,
            edge_color="#e74c3c", arrows=True,
            arrowstyle="-|>", arrowsize=20,
            width=2.2, ax=ax,
            connectionstyle="arc3,rad=0.08",
        )

    legend_handles = [
        mpatches.Patch(color=NODE_COLORS["llm"],      label="Gemini Agent"),
        mpatches.Patch(color=NODE_COLORS["fire"],     label="Fire Station"),
        mpatches.Patch(color=NODE_COLORS["police"],   label="Police"),
        mpatches.Patch(color=NODE_COLORS["hospital"], label="Hospital"),
    ]
    ax.legend(handles=legend_handles, loc="lower right",
              facecolor="#111520", edgecolor="#1e2a3a",
              labelcolor="white", fontsize=8)
    ax.axis("off")
    plt.tight_layout()
    return fig


# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚨 COMMS AGENT")
    st.markdown("<span class='tag-gemini'>Gemini 2.5 Flash</span>", unsafe_allow_html=True)
    st.markdown("---")

    st.markdown("**Gemini API Key**")
    api_key = st.text_input(
        "",
        value=os.environ.get("GEMINI_API_KEY", ""),
        placeholder="AIza...",
        type="password",
        label_visibility="collapsed",
    )
    st.markdown("---")

    st.markdown("**Triage JSON Input**")
    incoming_triage = st.session_state.get("shared_triage_json", DEFAULT_JSON)
    raw_json = st.text_area("", value=incoming_triage, height=230,
                             label_visibility="collapsed")
    dispatch_btn = st.button("⚡ DISPATCH")

    st.markdown("---")
    st.markdown("""
    <div style='font-size:11px;color:#445566;font-family:monospace;line-height:1.9'>
    Gemini will:<br>
    🧠 Reason over lat/lng coordinates<br>
    📋 Write the incident summary<br>
    🎯 Pick the closest unit per type<br>
    📡 Craft tailored dispatch messages<br><br>
    Types: fire / police / medical<br>
    Severities: low / medium / high / critical
    </div>
    """, unsafe_allow_html=True)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
st.markdown("# COMMUNICATIONS AGENT")
st.markdown(
    "<p style='color:#445566;font-family:monospace;margin-top:-12px'>"
    "LLM-driven Emergency Dispatch &nbsp;·&nbsp; Gemini 2.5 Flash"
    "</p>",
    unsafe_allow_html=True,
)

col_graph, col_log = st.columns([3, 2], gap="large")

graph_ph = col_graph.empty()
log_ph   = col_log.empty()

# Idle state
with graph_ph:
    st.pyplot(draw_graph([], []))
    plt.close()

with log_ph:
    st.markdown(
        "<div style='color:#334455;font-family:monospace;font-size:13px;padding:2rem 0'>"
        "↑ Paste your Gemini API key, enter JSON, click DISPATCH</div>",
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────
# DISPATCH FLOW
# ─────────────────────────────────────────────
if dispatch_btn:

    if not GENAI_AVAILABLE:
        st.error("Missing dependency. Run:  pip install google-genai")
        st.stop()

    if not api_key.strip():
        st.error("Paste your Gemini API key in the sidebar.")
        st.stop()

    try:
        incident = json.loads(raw_json)
    except json.JSONDecodeError as e:
        st.error(f"Invalid JSON: {e}")
        st.stop()

    # ── Call Gemini ──────────────────────────
    with st.spinner("🧠 Gemini is analysing the incident and selecting responders..."):
        try:
            result = call_gemini(api_key.strip(), incident)
        except json.JSONDecodeError as e:
            st.error(f"Gemini returned malformed JSON: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Gemini API error: {e}")
            st.stop()

    summary  = result.get("summary", "No summary.")
    selected = result.get("selected", [])

    if not selected:
        st.warning("Gemini returned no responders. Check your API key or JSON.")
        st.stop()

    # ── Stats row ────────────────────────────
    st.markdown("---")
    s1, s2, s3, s4 = st.columns(4)
    vals = [
        (incident.get("severity", "?").upper(), "SEVERITY",         "#e74c3c"),
        (incident.get("type",     "?").upper(), "INCIDENT TYPE",    "#e67e22"),
        (incident.get("people",    0),           "PEOPLE INVOLVED",  "#3498db"),
        (len(selected),                          "UNITS DISPATCHED", "#2ecc71"),
    ]
    for col, (val, label, color) in zip([s1, s2, s3, s4], vals):
        col.markdown(
            f'<div class="stat-card">'
            f'<div class="stat-num" style="color:{color}">{val}</div>'
            f'<div class="stat-label">{label}</div></div>',
            unsafe_allow_html=True,
        )

    # ── Alert + Gemini reasoning note ────────
    col_graph.markdown(
        f'<div class="alert-box">🚨 ALERT: {summary}</div>',
        unsafe_allow_html=True,
    )
    col_graph.markdown(
        f'<div class="gemini-box">🤖 <b>Gemini selected</b> {len(selected)} nearest units '
        f'by reasoning over coordinates across {len(RESPONDERS)} available responders.</div>',
        unsafe_allow_html=True,
    )

    # ── Animate edges + dispatch log ─────────
    edges_so_far = []
    log_lines    = []

    for node in selected:
        edges_so_far.append(("GEMINI\nAGENT", node["name"]))

        with graph_ph:
            st.pyplot(draw_graph(selected, edges_so_far))
            plt.close()

        emoji    = {"fire": "🔥", "police": "🚔", "hospital": "🏥"}.get(node["type"], "📡")
        dist     = node.get("distance_km", "?")
        dist_str = f"{dist:.1f} km" if isinstance(dist, (int, float)) else f"{dist} km"

        log_lines.append(
            f'<div class="msg-row">'
            f'{emoji} → <span class="node-name">{node["name"]}</span>'
            f' &nbsp;<span style="color:#445566;font-size:11px">({dist_str})</span><br>'
            f'<span style="color:#4a7090;font-size:11px">💡 {node.get("reason", "")}</span><br>'
            f'<span style="color:#6090a8">{node.get("message", "")}</span><br>'
            f'<span class="sent">✔ DISPATCHED</span>'
            f'</div>'
        )

        with log_ph:
            st.markdown(
                "**DISPATCH LOG** <span class='tag-gemini'>AI-generated</span>",
                unsafe_allow_html=True,
            )
            st.markdown("".join(log_lines), unsafe_allow_html=True)

        time.sleep(1.2)

    col_log.success(f"✅ Gemini dispatched {len(selected)} units successfully.")