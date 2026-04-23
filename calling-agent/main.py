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
