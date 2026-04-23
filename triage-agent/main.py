import os
import json
import base64
from flask import Flask, request, jsonify
from google.cloud import pubsub_v1, firestore
import vertexai
from vertexai.generative_models import GenerativeModel
from datetime import datetime

app = Flask(__name__)

# These are read from environment variables set during Cloud Run deployment
PROJECT_ID = os.environ.get("PROJECT_ID")
LOCATION = "asia-south1"

# Initialize all Google Cloud clients
# No API key needed — uses the attached service account automatically
vertexai.init(project=PROJECT_ID, location=LOCATION)
model = GenerativeModel("gemini-1.5-flash")
db = firestore.Client()
publisher = pubsub_v1.PublisherClient()

TRIAGE_PROMPT = """You are a crisis triage coordinator for a 5-star hotel.
Given this incident report, produce a JSON action plan:
{
  "priority": "P1|P2|P3",
  "category": "fire|medical|security|evacuation|flood|other",
  "affected_zones": ["list of zones/floors/rooms"],
  "recommended_actions": ["action 1", "action 2", "action 3"],
  "staff_roles_needed": ["security", "medical", "reception", "management"],
  "notify_emergency_services": true or false,
  "evacuation_required": true or false,
  "estimated_response_time_minutes": integer
}
P1 = life threatening, P2 = serious but stable, P3 = minor.
Base your response on standard hospitality emergency SOPs.
Return ONLY the JSON object. No extra text."""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["POST"])
def triage():
    # Pub/Sub sends messages via HTTP POST to this route
    # The message body is a JSON envelope containing base64-encoded data
    envelope = request.get_json()

    if not envelope or "message" not in envelope:
        return jsonify({"error": "Invalid Pub/Sub message"}), 400

    # Decode the base64 message from Pub/Sub
    pubsub_message = envelope["message"]
    raw = base64.b64decode(pubsub_message.get("data", "")).decode("utf-8")
    incident = json.loads(raw)

    print(f"Received incident: {json.dumps(incident)}")

    # Send to Gemini for triage decision
    gemini_input = f"Incident report: {json.dumps(incident)}"
    response = model.generate_content([TRIAGE_PROMPT, gemini_input])

    # Clean response in case Gemini wraps in markdown
    raw_text = response.text.strip()
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    triage_plan = json.loads(raw_text)

    # Build the full incident record
    incident_id = f"INC-{int(datetime.utcnow().timestamp())}"
    triage_plan["incident_id"] = incident_id
    triage_plan["original_incident"] = incident
    triage_plan["triage_timestamp"] = datetime.utcnow().isoformat()
    triage_plan["status"] = "active"

    print(f"Triage plan created: {incident_id} - {triage_plan['priority']}")

    # Save to Firestore so the dashboard can display it in real time
    db.collection("incidents").document(incident_id).set(triage_plan)
    print(f"Saved to Firestore: {incident_id}")

    # Publish to triage-output so Response + Coordination agents pick it up
    for topic_name in ["triage-output", "response-tasks"]:
        topic_path = publisher.topic_path(PROJECT_ID, topic_name)
        publisher.publish(topic_path, json.dumps(triage_plan).encode("utf-8"))
        print(f"Published to {topic_name}")

    return jsonify({
        "status": "triaged",
        "incident_id": incident_id,
        "priority": triage_plan["priority"]
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
