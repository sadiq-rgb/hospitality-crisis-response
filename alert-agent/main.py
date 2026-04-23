import os
import json
import base64
from flask import Flask, request, jsonify
from google.cloud import firestore
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

# Mock authority contact database
AUTHORITY_CONTACTS = {
    "fire": {
        "name": "Chennai Fire and Rescue Services",
        "number": "101",
        "department": "Fire Department",
        "response_time": "8-12 minutes"
    },
    "medical": {
        "name": "Tamil Nadu Emergency Medical Services",
        "number": "108",
        "department": "Ambulance Services",
        "response_time": "10-15 minutes"
    },
    "security": {
        "name": "Chennai City Police",
        "number": "100",
        "department": "Police Department",
        "response_time": "5-10 minutes"
    },
    "flood": {
        "name": "Chennai Disaster Management Authority",
        "number": "1913",
        "department": "Disaster Management",
        "response_time": "15-20 minutes"
    },
    "other": {
        "name": "National Emergency Number",
        "number": "112",
        "department": "General Emergency",
        "response_time": "10-15 minutes"
    }
}

ALERT_PROMPT = """You are an emergency dispatcher for a hotel crisis system.
Generate realistic mock emergency call scripts and notifications as JSON:
{
  "emergency_call_script": "Exact words the hotel should say when calling emergency services. Include: hotel name, address, incident type, location within hotel, number of people affected, current situation status, contact person name and number.",
  "sms_to_authorities": "Structured SMS under 200 chars with key facts",
  "whatsapp_to_management": "WhatsApp message to hotel management group with full situation update",
  "call_log": {
    "called_at": "timestamp",
    "authority_name": "name of authority called",
    "contact_number": "number called",
    "call_duration_seconds": integer,
    "information_provided": ["key point 1", "key point 2", "key point 3"],
    "authority_response": "what the authority said they will do",
    "eta_minutes": integer
  }
}
Make the call script realistic and professional.
Return ONLY the JSON. No extra text. No markdown."""

def determine_authorities(incident_type, priority):
    """Determine which authorities to contact based on incident type and priority"""
    authorities = []

    # Always call 112 for P1
    if priority == "P1":
        authorities.append(AUTHORITY_CONTACTS.get(incident_type, AUTHORITY_CONTACTS["other"]))
        # P1 fire also needs ambulance
        if incident_type == "fire":
            authorities.append(AUTHORITY_CONTACTS["medical"])
        # P1 security also needs police
        if incident_type == "security":
            authorities.append(AUTHORITY_CONTACTS["security"])
    # P2 calls relevant authority
    elif priority == "P2":
        authorities.append(AUTHORITY_CONTACTS.get(incident_type, AUTHORITY_CONTACTS["other"]))

    return authorities

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "agent": "alert-agent",
        "model": MODEL_NAME
    }), 200

@app.route("/", methods=["POST"])
def alert():
    envelope = request.get_json()

    if not envelope or "message" not in envelope:
        return jsonify({"error": "Invalid Pub/Sub message"}), 400

    pubsub_message = envelope["message"]
    raw = base64.b64decode(pubsub_message.get("data", "")).decode("utf-8")
    triage_plan = json.loads(raw)

    incident_id = triage_plan.get("incident_id", "UNKNOWN")
    priority = triage_plan.get("priority", "P3")
    incident_type = triage_plan.get("category", "other")

    print(f"Alert triggered for: {incident_id} - {priority} {incident_type}")

    # Determine which authorities to contact
    authorities = determine_authorities(incident_type, priority)

    if not authorities:
        print(f"No authorities needed for {priority} incident")
        return jsonify({"status": "no_alert_needed"}), 200

    all_alerts = []

    for authority in authorities:
        print(f"Generating mock call to: {authority['name']} ({authority['number']})")

        try:
            # Generate realistic call script using Gemini
            context = f"""
            Hotel: Grand Hospitality Hotel, Chennai
            Incident: {json.dumps(triage_plan)}
            Authority to call: {json.dumps(authority)}
            Current time: {datetime.utcnow().strftime('%H:%M')} UTC
            """

            response = model.generate_content([ALERT_PROMPT, context])
            raw_text = response.text.strip()
            raw_text = raw_text.replace("```json", "").replace("```", "").strip()
            alert_data = json.loads(raw_text)

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            continue
        except Exception as e:
            print(f"Gemini error: {e}")
            continue

        # Add metadata
        alert_data["authority"] = authority
        alert_data["incident_id"] = incident_id
        alert_data["alerted_at"] = datetime.utcnow().isoformat()
        alert_data["mock"] = True  # Flag this as a mock call

        all_alerts.append(alert_data)

        # Print the mock call to logs — this is the "call output"
        print("\n" + "="*60)
        print(f"MOCK EMERGENCY CALL — {authority['department']}")
        print("="*60)
        print(f"Calling: {authority['name']}")
        print(f"Number:  {authority['number']}")
        print(f"ETA:     {authority['response_time']}")
        print("-"*60)
        print("CALL SCRIPT:")
        print(alert_data.get("emergency_call_script", "N/A"))
        print("-"*60)
        print("SMS SENT:")
        print(alert_data.get("sms_to_authorities", "N/A"))
        print("="*60 + "\n")

    # Save all alerts to Firestore
    try:
        db.collection("incidents").document(incident_id).update({
            "emergency_alerts": all_alerts,
            "authorities_notified": [a["authority"]["name"] for a in all_alerts],
            "alert_timestamp": datetime.utcnow().isoformat()
        })

        # Also save to a separate alerts collection for audit trail
        db.collection("emergency_alerts").document(incident_id).set({
            "incident_id": incident_id,
            "priority": priority,
            "alerts": all_alerts,
            "total_authorities_notified": len(all_alerts),
            "created_at": datetime.utcnow().isoformat()
        })

        print(f"Alerts saved to Firestore for: {incident_id}")

    except Exception as e:
        print(f"Firestore error: {e}")
        return jsonify({"error": f"Firestore failed: {str(e)}"}), 500

    return jsonify({
        "status": "authorities_notified",
        "incident_id": incident_id,
        "authorities_called": [a["authority"]["name"] for a in all_alerts],
        "total_calls_made": len(all_alerts),
        "mock": True
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
