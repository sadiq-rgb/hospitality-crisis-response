import streamlit as st

# Define the pages and point them to your files in the same folder
intake_page    = st.Page("calling_agent.py",      title="1. Crisis Intake",      icon="🚨")
triage_page    = st.Page("triage_agent.py",       title="2. Triage Agent",       icon="🧠")
map_page       = st.Page("map_agent.py",          title="3. Map Agent",          icon="🗺️")
comms_page     = st.Page("comm_agent.py",         title="4. Comms Agent",        icon="📡")
services_page  = st.Page("services_dashboard.py", title="5. Services Dashboard", icon="🖥️")
summary_page   = st.Page("summary_agent.py",      title="6. Summary Agent",      icon="📋")

# Build the navigation menu
pg = st.navigation([intake_page, triage_page, map_page, comms_page, services_page, summary_page])

# Run the app
pg.run()