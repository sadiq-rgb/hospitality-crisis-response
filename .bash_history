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
EOF

cat > Dockerfile << 'EOF'
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["python", "main.py"]
EOF

gcloud builds submit --tag gcr.io/hospitality-crisis-response/alert-agent
gcloud run deploy alert-agent   --image gcr.io/hospitality-crisis-response/alert-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --no-allow-unauthenticated   --memory 512Mi   --timeout 60
ALERT_URL=$(gcloud run services describe alert-agent \
  --region asia-south1 --format 'value(status.url)')
echo "Alert Agent URL: $ALERT_URL"
gcloud run services add-iam-policy-binding alert-agent   --region asia-south1   --member="serviceAccount:crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com"   --role="roles/run.invoker"
gcloud pubsub subscriptions modify-push-config alert-sub   --push-endpoint=$ALERT_URL/   --push-auth-service-account=crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com
gcloud pubsub subscriptions create alert-sub     --topic=crisis-alerts     --project=hospitality-crisis-response
gcloud pubsub topics create crisis-alerts     --project=hospitality-crisis-response
gcloud pubsub subscriptions create alert-sub     --topic=crisis-alerts     --project=hospitality-crisis-response
ALERT_URL=$(gcloud run services describe alert-agent \
  --region asia-south1 --format 'value(status.url)')
echo "Alert Agent URL: $ALERT_URL"
gcloud run services add-iam-policy-binding alert-agent   --region asia-south1   --member="serviceAccount:crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com"   --role="roles/run.invoker"
gcloud pubsub subscriptions modify-push-config alert-sub   --push-endpoint=$ALERT_URL/   --push-auth-service-account=crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. Guests are trapped in the corridor!",
    "timestamp": "2026-04-23T10:30:00Z"
  }'
gcloud logging read   "resource.type=cloud_run_revision AND resource.labels.service_name=alert-agent"   --limit 50   --format "value(textPayload)"   --project hospitality-crisis-response
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. Guests are trapped in the corridor!",
    "timestamp": "2026-04-23T10:30:00Z"
  }'
gcloud logging read   "resource.type=cloud_run_revision AND resource.labels.service_name=alert-agent"   --limit 50   --format "value(textPayload)"   --project hospitality-crisis-response
# 1. Get the alert agent URL
ALERT_URL=$(gcloud run services describe alert-agent \
  --region asia-south1 --format 'value(status.url)')
echo "Alert URL: $ALERT_URL"
# 2. Make sure service account can invoke alert agent
gcloud run services add-iam-policy-binding alert-agent   --region asia-south1   --member="serviceAccount:crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com"   --role="roles/run.invoker"
# 3. Also give Pub/Sub service account permission to invoke
PROJECT_NUMBER=$(gcloud projects describe hospitality-crisis-response \
  --format="value(projectNumber)")
gcloud run services add-iam-policy-binding alert-agent   --region asia-south1   --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"   --role="roles/run.invoker"
# 4. Rewire the push subscription
gcloud pubsub subscriptions modify-push-config alert-sub   --push-endpoint=$ALERT_URL/   --push-auth-service-account=crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com
grep -n "emergency-alerts" ~/triage-agent/main.py
gcloud beta run services logs tail alert-agent   --region asia-south1   --project hospitality-crisis-response
sudo apt-get install google-cloud-cli-log-streaming
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "FIRE! Smoke and flames in Room 412 fourth floor. Guests trapped in corridor. Emergency!",
    "timestamp": "2026-04-23T10:30:00Z"
  }'
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "FIRE! Smoke and flames in Room 412 fourth floor. Guests trapped in corridor. Emergency!",
    "timestamp": "2026-04-23T10:30:00Z"
  }'
gcloud pubsub subscriptions describe alert-sub   --format="value(pushConfig.pushEndpoint)"
gcloud logging read   "resource.type=cloud_run_revision AND resource.labels.service_name=triage-agent"   --limit 20   --format "value(textPayload)"   --project hospitality-crisis-response   --freshness=10m
gcloud pubsub subscriptions describe alert-sub   --format="value(pushConfig.pushEndpoint)"
cat ~/triage-agent/main.py | grep -n "emergency-alerts\|alert_topic\|authorities_alerted"
grep -n "emergency-alerts\|EMERGENCY ALERT\|authorities_alerted" ~/triage-agent/main.py
cd ~/triage-agent
cat > main.py << 'EOF'
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
Return ONLY the JSON object. No extra text. No markdown."""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "version": "v2-with-alerts"
    }), 200

@app.route("/", methods=["POST"])
def triage():
    envelope = request.get_json()
    if not envelope or "message" not in envelope:
        return jsonify({"error": "Invalid Pub/Sub message"}), 400

    pubsub_message = envelope["message"]
    raw = base64.b64decode(pubsub_message.get("data", "")).decode("utf-8")
    incident = json.loads(raw)

    print(f"TRIAGE RECEIVED: {incident.get('incident_type')} {incident.get('severity')}")

    try:
        response = model.generate_content([
            TRIAGE_PROMPT,
            f"Incident report: {json.dumps(incident)}"
        ])
        raw_text = response.text.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        triage_plan = json.loads(raw_text)
    except Exception as e:
        print(f"Gemini error: {e}")
        return jsonify({"error": str(e)}), 500

    incident_id = f"INC-{int(datetime.utcnow().timestamp())}"
    triage_plan["incident_id"] = incident_id
    triage_plan["original_incident"] = incident
    triage_plan["triage_timestamp"] = datetime.utcnow().isoformat()
    triage_plan["status"] = "active"

    print(f"TRIAGE COMPLETE: {incident_id} priority={triage_plan['priority']}")

    try:
        db.collection("incidents").document(incident_id).set(triage_plan)
        print(f"FIRESTORE SAVED: {incident_id}")
    except Exception as e:
        print(f"FIRESTORE ERROR: {e}")
        return jsonify({"error": str(e)}), 500

    try:
        for topic_name in ["triage-output", "response-tasks"]:
            topic_path = publisher.topic_path(PROJECT_ID, topic_name)
            publisher.publish(topic_path, json.dumps(triage_plan).encode("utf-8"))
            print(f"PUBLISHED TO: {topic_name}")
    except Exception as e:
        print(f"PUBSUB ERROR: {e}")

    # Alert authorities for P1 and P2 only
    priority = triage_plan.get("priority")
    if priority in ["P1", "P2"]:
        try:
            alert_path = publisher.topic_path(PROJECT_ID, "emergency-alerts")
            publisher.publish(alert_path, json.dumps(triage_plan).encode("utf-8"))
            print(f"EMERGENCY ALERT PUBLISHED: {priority} incident {incident_id}")
        except Exception as e:
            print(f"ALERT ERROR: {e}")
    else:
        print(f"P3 INCIDENT: no authority alert needed")

    return jsonify({
        "status": "triaged",
        "incident_id": incident_id,
        "priority": priority,
        "evacuation_required": triage_plan.get("evacuation_required"),
        "authorities_alerted": priority in ["P1", "P2"]
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
EOF

cd ~/triage-agent
cat > main.py << 'EOF'
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
Return ONLY the JSON object. No extra text. No markdown."""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "version": "v2-with-alerts"
    }), 200

@app.route("/", methods=["POST"])
def triage():
    envelope = request.get_json()
    if not envelope or "message" not in envelope:
        return jsonify({"error": "Invalid Pub/Sub message"}), 400

    pubsub_message = envelope["message"]
    raw = base64.b64decode(pubsub_message.get("data", "")).decode("utf-8")
    incident = json.loads(raw)

    print(f"TRIAGE RECEIVED: {incident.get('incident_type')} {incident.get('severity')}")

    try:
        response = model.generate_content([
            TRIAGE_PROMPT,
            f"Incident report: {json.dumps(incident)}"
        ])
        raw_text = response.text.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        triage_plan = json.loads(raw_text)
    except Exception as e:
        print(f"Gemini error: {e}")
        return jsonify({"error": str(e)}), 500

    incident_id = f"INC-{int(datetime.utcnow().timestamp())}"
    triage_plan["incident_id"] = incident_id
    triage_plan["original_incident"] = incident
    triage_plan["triage_timestamp"] = datetime.utcnow().isoformat()
    triage_plan["status"] = "active"

    print(f"TRIAGE COMPLETE: {incident_id} priority={triage_plan['priority']}")

    try:
        db.collection("incidents").document(incident_id).set(triage_plan)
        print(f"FIRESTORE SAVED: {incident_id}")
    except Exception as e:
        print(f"FIRESTORE ERROR: {e}")
        return jsonify({"error": str(e)}), 500

    try:
        for topic_name in ["triage-output", "response-tasks"]:
            topic_path = publisher.topic_path(PROJECT_ID, topic_name)
            publisher.publish(topic_path, json.dumps(triage_plan).encode("utf-8"))
            print(f"PUBLISHED TO: {topic_name}")
    except Exception as e:
        print(f"PUBSUB ERROR: {e}")

    # Alert authorities for P1 and P2 only
    priority = triage_plan.get("priority")
    if priority in ["P1", "P2"]:
        try:
            alert_path = publisher.topic_path(PROJECT_ID, "emergency-alerts")
            publisher.publish(alert_path, json.dumps(triage_plan).encode("utf-8"))
            print(f"EMERGENCY ALERT PUBLISHED: {priority} incident {incident_id}")
        except Exception as e:
            print(f"ALERT ERROR: {e}")
    else:
        print(f"P3 INCIDENT: no authority alert needed")

    return jsonify({
        "status": "triaged",
        "incident_id": incident_id,
        "priority": priority,
        "evacuation_required": triage_plan.get("evacuation_required"),
        "authorities_alerted": priority in ["P1", "P2"]
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
EOF

nano main.py
grep -n "emergency-alerts\|EMERGENCY ALERT\|authorities_alerted" ~/triage-agent/main.py
cd ~/triage-agent
# Delete old image
gcloud container images delete gcr.io/hospitality-crisis-response/triage-agent   --force-delete-tags --quiet
# Fresh build
gcloud builds submit --tag gcr.io/hospitality-crisis-response/triage-agent
# Redeploy
gcloud run deploy triage-agent   --image gcr.io/hospitality-crisis-response/triage-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --no-allow-unauthenticated   --memory 512Mi   --timeout 60
TRIAGE_URL=$(gcloud run services describe triage-agent \
  --region asia-south1 --format 'value(status.url)')
curl $TRIAGE_URL/health
gcloud run services add-iam-policy-binding triage-agent   --member="allUsers"   --role="roles/run.invoker"   --region=asia-south1   --project=hospitality-crisis-response
TRIAGE_URL=$(gcloud run services describe triage-agent \
  --region asia-south1 --format 'value(status.url)')
curl $TRIAGE_URL/health
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "FIRE! Smoke and flames in Room 412 fourth floor. Guests trapped in corridor. Emergency!",
    "timestamp": "2026-04-23T10:30:00Z"
  }'
git add . 
git commit -m "alert agent added - pls link it"
git push origin main
git push origin main --force
# Remove the credentials file from the git index
git rm --cached .git-credentials
# Add it to .gitignore so it never gets added again
echo ".git-credentials" >> .gitignore
echo ".env" >> .gitignore
