import os
import json
import base64
from flask import Flask, request, jsonify
from google.cloud import pubsub_v1, firestore
import vertexai
from vertexai.generative_models import GenerativeModel
from datetime import datetime

app = Flask(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "hospitality-crisis-response")
LOCATION = "us-central1"
MODEL_NAME = "gemini-2.5-flash"

vertexai.init(project=PROJECT_ID, location=LOCATION)
model = GenerativeModel(MODEL_NAME)
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
Return ONLY the JSON object. No extra text. No markdown."""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "location": LOCATION,
        "project": PROJECT_ID
    }), 200

@app.route("/", methods=["POST"])
def triage():
    envelope = request.get_json()

    if not envelope or "message" not in envelope:
        return jsonify({"error": "Invalid Pub/Sub message"}), 400

    pubsub_message = envelope["message"]
    raw = base64.b64decode(pubsub_message.get("data", "")).decode("utf-8")
    incident = json.loads(raw)

    print(f"Received: {incident.get('incident_type')} - {incident.get('severity')}")

    try:
        gemini_input = f"Incident report: {json.dumps(incident)}"
        response = model.generate_content([TRIAGE_PROMPT, gemini_input])
        raw_text = response.text.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        triage_plan = json.loads(raw_text)

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return jsonify({"error": "Gemini returned invalid JSON"}), 500
    except Exception as e:
        print(f"Gemini error: {e}")
        return jsonify({"error": str(e)}), 500

    incident_id = f"INC-{int(datetime.utcnow().timestamp())}"
    triage_plan["incident_id"] = incident_id
    triage_plan["original_incident"] = incident
    triage_plan["triage_timestamp"] = datetime.utcnow().isoformat()
    triage_plan["status"] = "active"

    print(f"Triage complete: {incident_id} - {triage_plan['priority']}")

    try:
        db.collection("incidents").document(incident_id).set(triage_plan)
        print(f"Saved to Firestore: {incident_id}")
    except Exception as e:
        print(f"Firestore error: {e}")
        return jsonify({"error": f"Firestore failed: {str(e)}"}), 500

    # Publish to downstream agents
    try:
        for topic_name in ["triage-output", "response-tasks"]:
            topic_path = publisher.topic_path(PROJECT_ID, topic_name)
            publisher.publish(topic_path, json.dumps(triage_plan).encode("utf-8"))
            print(f"Published to {topic_name}")
    except Exception as e:
        print(f"Pub/Sub error: {e}")

    # Auto-alert authorities for P1 and P2 incidents
    if triage_plan.get("priority") in ["P1", "P2"]:
        try:
            alert_topic = publisher.topic_path(PROJECT_ID, "emergency-alerts")
            publisher.publish(alert_topic, json.dumps(triage_plan).encode("utf-8"))
            print(f"Emergency alert triggered for {triage_plan['priority']} incident")
        except Exception as e:
            print(f"Alert publish error: {e}")

    return jsonify({
        "status": "triaged",
        "incident_id": incident_id,
        "priority": triage_plan["priority"],
        "evacuation_required": triage_plan["evacuation_required"]
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
