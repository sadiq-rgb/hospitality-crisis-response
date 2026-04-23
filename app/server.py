import streamlit as st

# Define the pages and point them to your files in the same folder
intake_page = st.Page("calling_agent.py", title="1. Crisis Intake", icon="🚨")
triage_page = st.Page("triage_agent.py", title="2. Triage Agent", icon="🧠")
comms_page  = st.Page("comm_agent.py", title="3. Comms Agent", icon="📡")

# Build the navigation menu
pg = st.navigation([intake_page, triage_page, comms_page])

# Run the app
pg.run()