from flask import Flask, request, jsonify
import googlemaps
import os
import vertexai
from dotenv import load_dotenv
from vertexai.generative_models import GenerativeModel

load_dotenv()

app = Flask(__name__)

# --- CONFIG ---
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

vertexai.init(project="your-project-id", location="us-central1")
model = GenerativeModel("gemini-1.5-flash")

# --- TRIAGE ---
@app.route("/triage", methods=["POST"])
def triage():
    data = request.json

    prompt = f"""
    Analyze this incident and return JSON:
    {data}
    """

    response = model.generate_content(prompt)

    return jsonify({
        "triage_output": response.text
    })







# 🔥 GLOBAL STORAGE
latest_map_result = None


@app.route("/map", methods=["POST"])
def map_agent():
    global latest_map_result

    try:
        data = request.get_json()

        print("MAP AGENT RECEIVED:", data)

        location = data["payload"]["affected_area"]
        services = data["payload"]["required_services"]

        if not location or not services:
            return jsonify({"error": "Invalid input"}), 400

        # 🔥 STEP 1: Geocode
        geo = gmaps.geocode(location)
        if not geo:
            return jsonify({"error": "Geocoding failed"}), 400

        lat = geo[0]["geometry"]["location"]["lat"]
        lng = geo[0]["geometry"]["location"]["lng"]

        service_map = {
            "fire": "fire_station",
            "ambulance": "hospital",
            "police": "police"
        }

        authorities = []

        # 🔥 STEP 2
        for s in services:
            collected = []

            if s == "ambulance":
                queries = [
                    f"emergency care  hospital near {location}",
                    f"government hospital near {location}",
                    f"multi speciality hospital near {location}"
                    
                     ]

                for query in queries:
                    places = gmaps.places(query=query)

                    for p in places.get("results", []):
                        name = (p.get("name") or "").lower()
                        rating = p.get("rating", 0)

                        if rating < 3.8:
                            continue

                        loc = p.get("geometry", {}).get("location")
                        if not loc:
                            continue

                        collected.append({
                            "name": p.get("name"),
                            "service": s,
                            "lat": loc["lat"],
                            "lng": loc["lng"]
                        })

                    if len(collected) >= 3:
                        break

            else:
                places = gmaps.places_nearby(
                    location=(lat, lng),
                    radius=5000,
                    type=service_map.get(s)
                )

                for p in places.get("results", []):
                    loc = p.get("geometry", {}).get("location")
                    if not loc:
                        continue

                    collected.append({
                        "name": p.get("name"),
                        "service": s,
                        "lat": loc["lat"],
                        "lng": loc["lng"]
                    })

            # remove duplicates
            unique = {}
            for a in collected:
                unique[a["name"]] = a

            # 🔥 LIMIT TO 2-5 PER SERVICE, prefer closer ones
            service_authorities = list(unique.values())
            
            if service_authorities:
                # Sort by approximate distance
                service_authorities.sort(key=lambda a: (a["lat"] - lat) ** 2 + (a["lng"] - lng) ** 2)
                service_authorities = service_authorities[:5]  # Max 5
            
            if len(service_authorities) == 0:
                # No authorities for this service, skip
                continue
            
            authorities.extend(service_authorities)

        if not authorities:
            return jsonify({"error": "No authorities found"}), 404

        # 🔥 SORT ALL BY APPROXIMATE DISTANCE
        authorities.sort(key=lambda a: (a["lat"] - lat) ** 2 + (a["lng"] - lng) ** 2)

        # distance matrix - calculate for all
        destinations = [f"{a['lat']},{a['lng']}" for a in authorities]

        matrix = gmaps.distance_matrix(
            origins=[f"{lat},{lng}"],
            destinations=destinations,
            mode="driving"
        )

        elements = matrix["rows"][0]["elements"]

        for i, e in enumerate(elements):
            if e["status"] == "OK":
                authorities[i]["distance_km"] = round(e["distance"]["value"] / 1000, 2)
                authorities[i]["eta"] = e["duration"]["value"] // 60
            else:
                authorities[i]["distance_km"] = None
                authorities[i]["eta"] = None

            # 🔥 DO NOT SET ALERT YET - will set only for primaries
            authorities[i]["alert_sent"] = False

        authorities.sort(key=lambda x: x.get("distance_km") or 9999)

        # 🔥 ORGANIZE BY SERVICE TYPE - Create primary for each service + set alerts
        primaries_by_service = {}
        all_by_service = {}

        for service in services:
            all_by_service[service] = [a for a in authorities if a.get("service") == service]
            if all_by_service[service]:
                # Mark only the primary (first/closest) as alerted
                primaries_by_service[service] = all_by_service[service][0]
                all_by_service[service][0]["alert_sent"] = True

        # 🔥 STORE RESULT
        latest_map_result = {
            "location": location,
            "coordinates": {"lat": lat, "lng": lng},
            "requested_services": services,
            "primary": authorities[0],  # Overall closest authority
            "primaries_by_service": primaries_by_service,  # Primary for each service type
            "authorities_by_service": all_by_service,  # All authorities grouped by service
            "authorities": authorities  # All authorities in distance order
        }

        return jsonify({"status": "stored"})

    except Exception as e:
        print("BACKEND ERROR:", e)
        return jsonify({"error": str(e)}), 500


# 🔥 NEW ENDPOINT
@app.route("/map-result", methods=["GET"])
def get_map_result():
    global latest_map_result
    return jsonify(latest_map_result or {})


if __name__ == "__main__":
    app.run(port=8081)