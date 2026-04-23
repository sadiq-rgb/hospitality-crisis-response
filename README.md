# Hospitality Crisis Response System

A multi-agent AI system built on Google Cloud for real-time crisis 
detection, triage, and coordinated response in hospitality venues.

## Agents
- **Calling Agent** — transcribes distress calls, extracts incident data via Gemini
- **Triage Agent** — classifies severity, writes to Firestore, routes to downstream agents
- **Response Agent** — generates step-by-step SOP action plans
- **Coordination Agent** — assigns tasks to staff roles
- **Communication Agent** — drafts PA announcements, SMS alerts, first responder briefs
- **Post-Incident Agent** — generates after-action reports, logs to BigQuery

## Stack
- Google Cloud Run, Pub/Sub, Firestore, BigQuery
- Vertex AI — Gemini 2.5 Flash
- Cloud Speech-to-Text
