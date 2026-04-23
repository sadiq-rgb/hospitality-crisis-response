gcloud iam service-accounts create crisis-agent-sa   --display-name="Crisis Agent Service Account"
# Grant required roles
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID   --member="serviceAccount:crisis-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com"   --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID   --member="serviceAccount:crisis-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com"   --role="roles/pubsub.editor"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID   --member="serviceAccount:crisis-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com"   --role="roles/datastore.user"
# Grant AI Platform User role
gcloud projects add-iam-policy-binding hospitality-crisis-response   --member="serviceAccount:crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com"   --role="roles/aiplatform.user"
# Grant Pub/Sub Editor role
gcloud projects add-iam-policy-binding hospitality-crisis-response   --member="serviceAccount:crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com"   --role="roles/pubsub.editor"
# Grant Cloud Datastore (Firestore) User role
gcloud projects add-iam-policy-binding hospitality-crisis-response   --member="serviceAccount:crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com"   --role="roles/datastore.user"
gcloud iam service-accounts list
gcloud services enable   aiplatform.googleapis.com   speech.googleapis.com   run.googleapis.com   pubsub.googleapis.com   firestore.googleapis.com   bigquery.googleapis.com   cloudbuild.googleapis.com   artifactregistry.googleapis.com   videointelligence.googleapis.com
gcloud billing accounts list
gcloud billing projects link hospitality-crisis-response   --billing-account=019F8C-3A64CC-56C410
gcloud services enable   aiplatform.googleapis.com   speech.googleapis.com   run.googleapis.com   pubsub.googleapis.com   firestore.googleapis.com   bigquery.googleapis.com   cloudbuild.googleapis.com   artifactregistry.googleapis.com   videointelligence.googleapis.com
cd ~
mkdir -p triage-agent
cd triage-agent
touch main.py requirements.txt Dockerfile
ls -la
cat > requirements.txt << 'EOF'
flask==3.0.0
google-cloud-pubsub==2.21.0
google-cloud-aiplatform==1.50.0
google-cloud-firestore==2.16.0
vertexai==1.50.0
EOF

export PROJECT_ID=hospitality-crisis-response
echo $PROJECT_ID
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
EOF

cat > Dockerfile << 'EOF'
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["python", "main.py"]
EOF

# Step 1: Build the container and push it to Google's container registry
gcloud builds submit --tag gcr.io/hospitality-crisis-response/triage-agent
# Step 2: Deploy the container to Cloud Run
# Note: --no-allow-unauthenticated because only Pub/Sub should call this, not the public
gcloud run deploy triage-agent   --image gcr.io/hospitality-crisis-response/triage-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --no-allow-unauthenticated
gcloud config set project hospitality-crisis-response
gcloud builds submit --tag gcr.io/hospitality-crisis-response/triage-agent
nano requirements.text
nano requirements.txt
gcloud builds submit --tag gcr.io/hospitality-crisis-response/triage-agent
gcloud run deploy triage-agent   --image gcr.io/hospitality-crisis-response/triage-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --no-allow-unauthenticated
TRIAGE_URL=$(gcloud run services describe triage-agent \
  --region asia-south1 --format 'value(status.url)')
echo "Triage Agent URL: $TRIAGE_URL"
# First, give Pub/Sub permission to call your Cloud Run service
gcloud projects add-iam-policy-binding hospitality-crisis-response   --member="serviceAccount:crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com"   --role="roles/run.invoker"
# Then configure the push subscription
gcloud pubsub subscriptions modify-push-config triage-sub   --push-endpoint=$TRIAGE_URL/   --push-auth-service-account=crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com
cd ~
mkdir -p calling-agent
cd calling-agent
pwd
# Should show: /home/YOUR_USERNAME/calling-agent
cat > requirements.txt << 'EOF'
flask==3.0.0
google-cloud-speech==2.26.0
google-cloud-pubsub==2.21.0
google-cloud-aiplatform==1.50.0
EOF

cat > main.py << 'EOF'
import os
import json
import base64
from flask import Flask, request, jsonify
from google.cloud import speech, pubsub_v1
import vertexai
from vertexai.generative_models import GenerativeModel

# -------------------------------------------------------
# APP SETUP
# -------------------------------------------------------

app = Flask(__name__)

# PROJECT_ID is set as an environment variable when deploying to Cloud Run
# This way we never hardcode sensitive values in our code
PROJECT_ID = os.environ.get("PROJECT_ID")
LOCATION = "asia-south1"
TOPIC = "crisis-events"

# -------------------------------------------------------
# CLIENT INITIALIZATION
# No API keys needed — all clients authenticate using
# the service account attached to the Cloud Run service
# -------------------------------------------------------

# Initialize Vertex AI with our project
vertexai.init(project=PROJECT_ID, location=LOCATION)

# Load Gemini 1.5 Flash model — faster and free tier friendly
model = GenerativeModel("gemini-1.5-flash")

# Google Cloud Speech-to-Text client
speech_client = speech.SpeechClient()

# Pub/Sub publisher client
publisher = pubsub_v1.PublisherClient()

# -------------------------------------------------------
# GEMINI PROMPT
# This tells Gemini exactly what to extract from the call
# and in what format to return it
# -------------------------------------------------------

SYSTEM_PROMPT = """You are a crisis intake agent for a hospitality venue.
Given a call transcript, extract the following as valid JSON only:
{
  "incident_type": "fire|medical|security|flood|other",
  "severity": "P1|P2|P3",
  "location": "floor/room/area as described",
  "affected_count": integer or null,
  "immediate_danger": true or false,
  "summary": "one sentence description",
  "caller_state": "calm|distressed|panicked"
}

Severity guide:
- P1 = life threatening (fire, cardiac arrest, violence with weapon)
- P2 = serious but stable (injury, aggressive guest, water leak)
- P3 = minor (noise complaint, minor injury, lost property)

Return ONLY the JSON object. No extra text. No markdown."""

# -------------------------------------------------------
# ROUTES
# -------------------------------------------------------

# Health check route — used by Cloud Run to verify the
# service is running. Always include this.
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# Main route — receives call data and processes it
@app.route("/analyze-call", methods=["POST"])
def analyze_call():

    # Get the JSON body from the request
    data = request.json

    if not data:
        return jsonify({"error": "No data provided"}), 400

    # -------------------------------------------------------
    # STEP 1: GET THE TRANSCRIPT
    # Two ways to provide input:
    # Option A — plain text transcript (for demo/testing)
    # Option B — base64 encoded audio (for real phone calls)
    # -------------------------------------------------------

    transcript = data.get("transcript", "")

    # Option B: if audio is provided instead of text
    if not transcript and data.get("audio_base64"):

        # Decode the base64 audio bytes
        audio_bytes = base64.b64decode(data["audio_base64"])

        # Wrap in Google Speech format
        audio = speech.RecognitionAudio(content=audio_bytes)

        # Configure the speech recognition
        # LINEAR16 = standard uncompressed audio format
        # en-IN = English as spoken in India
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-IN",
        )

        # Call Google Speech-to-Text
        stt_response = speech_client.recognize(config=config, audio=audio)

        # Join all recognized segments into one transcript
        transcript = " ".join(
            result.alternatives[0].transcript
            for result in stt_response.results
        )

    # If we still have no transcript, return an error
    if not transcript:
        return jsonify({"error": "No transcript or audio provided"}), 400

    print(f"Processing transcript: {transcript[:100]}...")

    # -------------------------------------------------------
    # STEP 2: SEND TO GEMINI
    # Pass the system prompt + transcript to Gemini
    # Gemini returns structured JSON describing the incident
    # -------------------------------------------------------

    try:
        gemini_response = model.generate_content([
            SYSTEM_PROMPT,
            f"Transcript: {transcript}"
        ])

        # Get the raw text response from Gemini
        raw_text = gemini_response.text.strip()

        # Clean it — Gemini sometimes wraps JSON in markdown code blocks
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()

        # Parse the JSON string into a Python dictionary
        incident_data = json.loads(raw_text)

    except json.JSONDecodeError as e:
        print(f"Gemini returned invalid JSON: {e}")
        print(f"Raw response was: {raw_text}")
        return jsonify({"error": "Gemini returned invalid JSON", "raw": raw_text}), 500

    except Exception as e:
        print(f"Gemini call failed: {e}")
        return jsonify({"error": str(e)}), 500

    # Add extra fields to the incident data
    incident_data["raw_transcript"] = transcript
    incident_data["timestamp"] = data.get("timestamp", "")
    incident_data["source"] = "phone_call"

    print(f"Incident extracted: {incident_data['incident_type']} - {incident_data['severity']}")

    # -------------------------------------------------------
    # STEP 3: PUBLISH TO PUB/SUB
    # Send the structured incident to the crisis-events topic
    # The Triage Agent is subscribed to this topic and will
    # automatically receive and process this message
    # -------------------------------------------------------

    try:
        topic_path = publisher.topic_path(PROJECT_ID, TOPIC)
        future = publisher.publish(
            topic_path,
            json.dumps(incident_data).encode("utf-8")
        )
        message_id = future.result()
        print(f"Published to Pub/Sub with message ID: {message_id}")

    except Exception as e:
        print(f"Pub/Sub publish failed: {e}")
        return jsonify({"error": f"Failed to publish: {str(e)}"}), 500

    # Return success response
    return jsonify({
        "status": "published",
        "message_id": message_id,
        "incident": incident_data
    }), 200


# -------------------------------------------------------
# START THE SERVER
# host="0.0.0.0" means accept connections from anywhere
# port=8080 is the default port Cloud Run expects
# -------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)

EOF

cat > Dockerfile << 'EOF'
# Start from official Python 3.11 image (slim = smaller size)
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy requirements first — this lets Docker cache the pip install
# layer so rebuilds are faster if only main.py changes
COPY requirements.txt .

# Install all Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code
COPY main.py .

# Tell the container to run main.py when it starts
CMD ["python", "main.py"]
EOF

ls -la
# Should show: Dockerfile  main.py  requirements.txt
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
echo "Your Calling Agent URL is: $CALLING_URL"
curl $CALLING_URL/health
# Expected: {"status":"ok"}
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
cd ~/calling-agent
sed -i 's/LOCATION = "asia-south1"/LOCATION = "us-central1"/' main.py
grep "LOCATION" main.py
# Should show: LOCATION = "us-central1"
# Rebuild the container with the fix
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
# Redeploy
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
sed -i 's/LOCATION = "us-central1"/LOCATION = "europe-west1"/' main.py
grep "LOCATION" main.py
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
# Rebuild
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
# Redeploy
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --region asia-south1
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
# Refresh the URL variable
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
echo $CALLING_URL
# Test
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor.",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
cd ~/calling-agent
grep "LOCATION" main.py
cd ~/calling-agent
# Open the file with nano editor
nano main.py
grep -n "LOCATION\|asia-south1\|europe-west1" main.py
# Should return only one line with us-central1
gcloud container images delete gcr.io/hospitality-crisis-response/calling-agent --force-delete-tags --quiet
cd ~/calling-agent
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response,LOCATION=us-central1   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
cd ~/calling-agent
sed -i 's/LOCATION = "us-central1"/LOCATION = os.environ.get("LOCATION", "us-central1")/' main.py
grep "LOCATION" main.py
# Should show: LOCATION = os.environ.get("LOCATION", "us-central1")
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response,LOCATION=us-central1   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
gcloud run revisions list   --service calling-agent   --region asia-south1
gcloud run services describe calling-agent   --region asia-south1   --format "value(spec.template.spec.containers[0].env)"
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. Flames visible under the door.",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
# Navigate to the calling-agent directory if you aren't already there
cd ~/calling-agent
# Replace 'gemini-1.5-flash' with 'gemini-pro' in all files in the current directory
sed -i 's/gemini-1.5-flash/gemini-pro/g' *.py
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --region asia-south1
gcloud config set project hospitality-crisis-response
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --region asia-south1
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. Flames visible under the door.",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
# First, ensure your CALLING_URL variable is set
export CALLING_URL=$(gcloud run services describe calling-agent --region asia-south1 --format="value(status.url)")
# Now run the test
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
gcloud run services update calling-agent   --set-env-vars LOCATION=europe-west1   --region asia-south1   --project hospitality-crisis-response
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
gcloud run services update calling-agent     --update-env-vars LOCATION=europe-west1     --region asia-south1     --project hospitality-crisis-response
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
echo "Your Calling Agent URL is: $CALLING_URL"
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
export CALLING_URL=$(gcloud run services describe calling-agent --region asia-south1 --project hospitality-crisis-response --format="value(status.url)")
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
gcloud services enable aiplatform.googleapis.com
$ gcloud config set project hospitality-crisis-response
gcloud config set project hospitality-crisis-response
gcloud services enable aiplatform.googleapis.com
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
cd ~/calling-agent
cat > main.py << 'EOF'
import os
import json
import base64
from flask import Flask, request, jsonify
from google.cloud import speech, pubsub_v1
import vertexai
from vertexai.generative_models import GenerativeModel

app = Flask(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "hospitality-crisis-response")
LOCATION = "us-central1"
TOPIC = "crisis-events"

vertexai.init(project=PROJECT_ID, location=LOCATION)
model = GenerativeModel("gemini-2.0-flash")
speech_client = speech.SpeechClient()
publisher = pubsub_v1.PublisherClient()

SYSTEM_PROMPT = """You are a crisis intake agent for a hospitality venue.
Given a call transcript, extract the following as valid JSON only:
{
  "incident_type": "fire|medical|security|flood|other",
  "severity": "P1|P2|P3",
  "location": "floor/room/area as described",
  "affected_count": integer or null,
  "immediate_danger": true or false,
  "summary": "one sentence description",
  "caller_state": "calm|distressed|panicked"
}
P1 = life threatening, P2 = serious but stable, P3 = minor.
Return ONLY the JSON object. No extra text. No markdown."""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model": "gemini-2.0-flash", "location": LOCATION}), 200

@app.route("/analyze-call", methods=["POST"])
def analyze_call():
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    transcript = data.get("transcript", "")

    if not transcript and data.get("audio_base64"):
        audio_bytes = base64.b64decode(data["audio_base64"])
        audio = speech.RecognitionAudio(content=audio_bytes)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-IN",
        )
        stt_response = speech_client.recognize(config=config, audio=audio)
        transcript = " ".join(
            result.alternatives[0].transcript
            for result in stt_response.results
        )

    if not transcript:
        return jsonify({"error": "No transcript or audio provided"}), 400

    print(f"Transcript received: {transcript[:80]}...")
    print(f"Using model: gemini-2.0-flash in {LOCATION}")

    try:
        gemini_response = model.generate_content([
            SYSTEM_PROMPT,
            f"Transcript: {transcript}"
        ])
        raw_text = gemini_response.text.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        incident_data = json.loads(raw_text)

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e} | Raw: {raw_text}")
        return jsonify({"error": "Gemini returned invalid JSON", "raw": raw_text}), 500
    except Exception as e:
        print(f"Gemini error: {e}")
        return jsonify({"error": str(e)}), 500

    incident_data["raw_transcript"] = transcript
    incident_data["timestamp"] = data.get("timestamp", "")
    incident_data["source"] = "phone_call"

    try:
        topic_path = publisher.topic_path(PROJECT_ID, TOPIC)
        future = publisher.publish(topic_path, json.dumps(incident_data).encode("utf-8"))
        message_id = future.result()
        print(f"Published to Pub/Sub: {message_id}")
    except Exception as e:
        print(f"Pub/Sub error: {e}")
        return jsonify({"error": f"Pub/Sub failed: {str(e)}"}), 500

    return jsonify({
        "status": "published",
        "message_id": message_id,
        "incident": incident_data
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
EOF

grep -n "LOCATION\|model\|gemini" main.py
# Delete old image
gcloud container images delete gcr.io/hospitality-crisis-response/calling-agent   --force-delete-tags --quiet
# Fresh build
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
# Redeploy
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl $CALLING_URL/health
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
cd ~
python3 << 'EOF'
import vertexai
from vertexai.generative_models import GenerativeModel

vertexai.init(project="hospitality-crisis-response", location="us-central1")

# Test each model name one by one
models_to_try = [
    "gemini-2.0-flash-001",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-1.5-flash-001",
    "gemini-1.5-pro-001",
]

for model_name in models_to_try:
    try:
        model = GenerativeModel(model_name)
        response = model.generate_content("Say hello in one word")
        print(f"✓ WORKS: {model_name} → {response.text.strip()}")
        break
    except Exception as e:
        print(f"✗ FAILED: {model_name} → {str(e)[:80]}")
EOF

cd ~/calling-agent
sed -i 's/GenerativeModel("gemini-2.0-flash")/GenerativeModel("gemini-2.5-flash")/' main.py
# Verify
grep "GenerativeModel" main.py
gcloud container images delete gcr.io/hospitality-crisis-response/calling-agent   --force-delete-tags --quiet
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
curl $CALLING_URL/health
# Check what model is actually in your current main.py
grep "GenerativeModel\|LOCATION" ~/calling-agent/main.py
cat > ~/calling-agent/main.py << 'EOF'
import os
import json
import base64
from flask import Flask, request, jsonify
from google.cloud import speech, pubsub_v1
import vertexai
from vertexai.generative_models import GenerativeModel

app = Flask(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "hospitality-crisis-response")
LOCATION = "us-central1"
MODEL_NAME = "gemini-2.5-flash"
TOPIC = "crisis-events"

vertexai.init(project=PROJECT_ID, location=LOCATION)
model = GenerativeModel(MODEL_NAME)
speech_client = speech.SpeechClient()
publisher = pubsub_v1.PublisherClient()

SYSTEM_PROMPT = """You are a crisis intake agent for a hospitality venue.
Given a call transcript, extract the following as valid JSON only:
{
  "incident_type": "fire|medical|security|flood|other",
  "severity": "P1|P2|P3",
  "location": "floor/room/area as described",
  "affected_count": integer or null,
  "immediate_danger": true or false,
  "summary": "one sentence description",
  "caller_state": "calm|distressed|panicked"
}
P1 = life threatening, P2 = serious but stable, P3 = minor.
Return ONLY the JSON object. No extra text. No markdown."""

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "location": LOCATION,
        "project": PROJECT_ID
    }), 200

@app.route("/analyze-call", methods=["POST"])
def analyze_call():
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    transcript = data.get("transcript", "")

    if not transcript and data.get("audio_base64"):
        audio_bytes = base64.b64decode(data["audio_base64"])
        audio = speech.RecognitionAudio(content=audio_bytes)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-IN",
        )
        stt_response = speech_client.recognize(config=config, audio=audio)
        transcript = " ".join(
            result.alternatives[0].transcript
            for result in stt_response.results
        )

    if not transcript:
        return jsonify({"error": "No transcript or audio provided"}), 400

    print(f"Transcript received: {transcript[:80]}...")
    print(f"Using model: {MODEL_NAME} in {LOCATION}")

    try:
        gemini_response = model.generate_content([
            SYSTEM_PROMPT,
            f"Transcript: {transcript}"
        ])
        raw_text = gemini_response.text.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        incident_data = json.loads(raw_text)

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e} | Raw: {raw_text}")
        return jsonify({"error": "Gemini returned invalid JSON", "raw": raw_text}), 500
    except Exception as e:
        print(f"Gemini error: {e}")
        return jsonify({"error": str(e)}), 500

    incident_data["raw_transcript"] = transcript
    incident_data["timestamp"] = data.get("timestamp", "")
    incident_data["source"] = "phone_call"

    print(f"Incident extracted: {incident_data['incident_type']} - {incident_data['severity']}")

    try:
        topic_path = publisher.topic_path(PROJECT_ID, TOPIC)
        future = publisher.publish(
            topic_path,
            json.dumps(incident_data).encode("utf-8")
        )
        message_id = future.result()
        print(f"Published to Pub/Sub: {message_id}")
    except Exception as e:
        print(f"Pub/Sub error: {e}")
        return jsonify({"error": f"Pub/Sub failed: {str(e)}"}), 500

    return jsonify({
        "status": "published",
        "message_id": message_id,
        "incident": incident_data
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
EOF

grep "MODEL_NAME\|LOCATION\|gemini" ~/calling-agent/main.py
cd ~/calling-agent
gcloud builds submit --tag gcr.io/hospitality-crisis-response/calling-agent
gcloud run deploy calling-agent   --image gcr.io/hospitality-crisis-response/calling-agent   --platform managed   --region asia-south1   --set-env-vars PROJECT_ID=hospitality-crisis-response   --service-account crisis-agent-sa@hospitality-crisis-response.iam.gserviceaccount.com   --allow-unauthenticated   --memory 512Mi   --timeout 60
CALLING_URL=$(gcloud run services describe calling-agent \
  --region asia-south1 --format 'value(status.url)')
# Health check — should now show gemini-2.5-flash
curl $CALLING_URL/health
# Full test
curl -X POST $CALLING_URL/analyze-call   -H "Content-Type: application/json"   -d '{
    "transcript": "Help! There is smoke coming from Room 412 on the fourth floor. I can see flames under the door. There are guests still in the corridor. Please hurry!",
    "timestamp": "2026-04-22T10:30:00Z"
  }'
git config --global user.name "Anieshwar-Saravanan"
git config --global user.email "anieshwars10c@gmail.com"
git config --global credential.helper store
cd ~
# Create all agent folders if they don't exist yet
mkdir -p calling-agent triage-agent response-agent   coordination-agent comms-agent post-incident-agent dashboard
cat > ~/.gitignore << 'EOF'
*.pyc
__pycache__/
.env
.gcloud/
*.log
EOF

cat > ~/README.md << 'EOF'
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
EOF

