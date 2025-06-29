from flask import Flask, request, jsonify, Response, abort
import requests
from datetime import datetime, timedelta
import os
import pytz
from twilio.rest import Client
import json
import uuid
import time
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 Mo max payload

# Rate limiting: 5 POST/minute/IP sur /mcp
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
CALENDAR_ID = os.environ.get("CALENDAR_ID")
TIMEZONE = "America/Toronto"
TWILIO_SID = os.environ.get("TWILIO_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN")
TWILIO_FROM = os.environ.get("TWILIO_FROM")
SMS_RECIPIENT = os.environ.get("SMS_RECIPIENT")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
MCP_SECRET_TOKEN = os.environ.get("MCP_SECRET_TOKEN")
ACCESS_TOKEN = None

def get_access_token():
    global ACCESS_TOKEN
    env_token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if env_token and env_token != "PASTE_YOUR_ACCESS_TOKEN_HERE":
        ACCESS_TOKEN = env_token
        return ACCESS_TOKEN
    if not all([GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET]):
        print("❌ Variables OAuth manquantes pour le refresh token")
        return None
    print("🔄 Rafraîchissement du access token...")
    response = requests.post("https://oauth2.googleapis.com/token", data={
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token"
    })
    if response.status_code == 200:
        token_data = response.json()
        ACCESS_TOKEN = token_data.get("access_token")
        print("✅ Access token rafraîchi avec succès")
        return ACCESS_TOKEN
    else:
        print(f"❌ Erreur lors du refresh: {response.text}")
        return None

print("🚀 Chargement du serveur MCP SSE Autoscale Calendar")
print(f"📅 Calendrier: {CALENDAR_ID}")
print(f"📱 SMS depuis: {TWILIO_FROM}")

ACCESS_TOKEN = get_access_token()
if ACCESS_TOKEN:
    print("✅ Access token configuré")
else:
    print("⚠️  Access token non disponible - vérifiez les variables OAuth")

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Autoscale Calendar MCP"})

@app.route("/mcp", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def mcp_sse():
    # --- Sécurité : vérif secret sur POST
    if request.method == "POST":
        if MCP_SECRET_TOKEN:
            auth = request.headers.get("Authorization", "")
            if not auth or f"Bearer {MCP_SECRET_TOKEN}" not in auth:
                print("Tentative POST sans Authorization correcte : headers=", dict(request.headers))
                abort(401, "Unauthorized")
    if request.method == "GET":
        def generate():
            yield f"event: protocol_version\ndata: {json.dumps({'protocol_version': '0.1.0'})}\n\n"
            yield f"event: capabilities\ndata: {json.dumps({'tools': True})}\n\n"
            yield f"event: message\ndata: {json.dumps({'type': 'connection', 'status': 'connected'})}\n\n"
            tools_data = {
                "type": "tools",
                "tools": [{
                    "name": "book_appointment",
                    "description": "Réserver un rendez-vous dans Google Calendar avec envoi SMS",
                    "inputSchema": {  # <-- camelCase pour ElevenLabs
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Nom complet du client"},
                            "phone": {"type": "string", "description": "Numéro de téléphone (ex: 514-123-4567)"},
                            "start": {"type": "string", "description": "Date et heure (format: 2025-06-27T14:00:00)"}
                        },
                        "required": ["name", "phone", "start"]
                    }
                }]
            }
            yield f"event: tools\ndata: {json.dumps(tools_data)}\n\n"
            yield f"event: ready\ndata: {json.dumps({'ready': True})}\n\n"
        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*'
            }
        )
    elif request.method == "POST":
        try:
            data = request.get_json(force=True)
        except Exception as e:
            print("Erreur parsing JSON POST:", e)
            return jsonify({"success": False, "message": "JSON invalide"}), 400
        print(f"[MCP POST] Données reçues: {json.dumps(data)}")
        method = data.get("method", "")
        if method == "tools/call":
            tool_name = data.get("params", {}).get("name")
            arguments = data.get("params", {}).get("arguments", {})
            if tool_name == "book_appointment":
                result = book_appointment_logic(arguments)
                response_data = {
                    "id": data.get("id", str(uuid.uuid4())),
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": result.get("message", "Erreur lors de la réservation")
                        }]
                    }
                }
                if not result.get("success"):
                    response_data["error"] = {
                        "code": -32000,
                        "message": result.get("message", "Erreur inconnue")
                    }
                return jsonify(response_data)
            else:
                return jsonify({
                    "id": data.get("id"),
                    "error": {
                        "code": -32601,
                        "message": f"Unknown tool: {tool_name}"
                    }
                }), 400
        else:
            result = book_appointment_logic(data)
            return jsonify(result)

def book_appointment_logic(params):
    global ACCESS_TOKEN
    print(f"[BOOKING] Paramètres: {json.dumps(params)}")
    name = params.get("name", "Client")
    client_phone = params.get("phone")
    start_str = params.get("start")
    if not start_str:
        return {"success": False, "message": "Date et heure requises"}
    if not client_phone:
        return {"success": False, "message": "Numéro de téléphone requis"}
    client_phone = client_phone.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
    if not client_phone.startswith('+'):
        if client_phone.startswith('1'):
            client_phone = '+' + client_phone
        else:
            client_phone = '+1' + client_phone
    try:
        if 'T' in start_str:
            if start_str.endswith('Z'):
                start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                start_time = start_time.astimezone(pytz.timezone(TIMEZONE))
            else:
                start_time = datetime.fromisoformat(start_str)
                if start_time.tzinfo is None:
                    start_time = pytz.timezone(TIMEZONE).localize(start_time)
        else:
            start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
            start_time = pytz.timezone(TIMEZONE).localize(start_time)
    except:
        return {"success": False, "message": "Format de date invalide. Utilisez: 2025-06-27T14:00:00"}
    now = datetime.now(pytz.timezone(TIMEZONE))
    if start_time < now + timedelta(hours=3):
        return {"success": False, "message": "Les réservations doivent être faites au moins 3 heures à l'avance"}
    if start_time.hour < 9 or start_time.hour >= 21:
        return {"success": False, "message": "Les rendez-vous sont disponibles de 9h à 21h seulement"}
    end_time = start_time + timedelta(minutes=30)
    if not ACCESS_TOKEN:
        ACCESS_TOKEN = get_access_token()
        if not ACCESS_TOKEN:
            return {"success": False, "message": "Configuration OAuth manquante"}
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    freebusy_url = "https://www.googleapis.com/calendar/v3/freeBusy"
    busy_check = requests.post(freebusy_url, headers=headers, json={
        "timeMin": start_time.isoformat(),
        "timeMax": end_time.isoformat(),
        "timeZone": TIMEZONE,
        "items": [{"id": CALENDAR_ID}]
    })
    if busy_check.status_code == 401:
        ACCESS_TOKEN = get_access_token()
        if ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {ACCESS_TOKEN}"
            busy_check = requests.post(freebusy_url, headers=headers, json={
                "timeMin": start_time.isoformat(),
                "timeMax": end_time.isoformat(),
                "timeZone": TIMEZONE,
                "items": [{"id": CALENDAR_ID}]
            })
    if busy_check.status_code != 200:
        print(f"Erreur freeBusy: {busy_check.text}")
        return {"success": False, "message": "Erreur lors de la vérification des disponibilités"}
    busy_slots = busy_check.json().get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
    if busy_slots:
        return {"success": False, "message": "Ce créneau est déjà réservé. Veuillez choisir un autre moment."}
    event_url = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events?conferenceDataVersion=1"
    event_payload = {
        "summary": f"Consultation avec {name}",
        "description": (
            f"Client: {name}\n"
            f"Téléphone: {client_phone}\n\n"
            f"Rendez-vous confirmé via agent ElevenLabs."
        ),
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": TIMEZONE
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": TIMEZONE
        },
        "attendees": [
            {"email": EMAIL_RECIPIENT}
        ],
        "conferenceData": {
            "createRequest": {
                "requestId": f"autoscale-{int(time.time())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"}
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 30}
            ]
        }
    }
    created_event = requests.post(event_url, headers=headers, json=event_payload)
    if created_event.status_code != 200:
        print(f"Erreur création événement: {created_event.text}")
        return {"success": False, "message": "Erreur lors de la création du rendez-vous"}
    event_data = created_event.json()
    meet_link = "Non disponible"
    if "conferenceData" in event_data and "entryPoints" in event_data["conferenceData"]:
        for entry in event_data["conferenceData"]["entryPoints"]:
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri", "Non disponible")
                break
    sms_sent = False
    if TWILIO_SID and TWILIO_TOKEN:
        try:
            twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
            sms_body = (
                f"Bonjour {name},\n\n"
                f"Votre rendez-vous avec Autoscale AI est confirmé!\n\n"
                f"📅 {start_time.strftime('%d/%m/%Y')}\n"
                f"🕒 {start_time.strftime('%H:%M')}\n"
                f"📍 Vidéoconférence\n\n"
                f"Lien Google Meet:\n{meet_link}\n\n"
                f"À bientôt!"
            )
            twilio_client.messages.create(
                body=sms_body,
                from_=TWILIO_FROM,
                to=client_phone
            )
            if SMS_RECIPIENT:
                twilio_client.messages.create(
                    body=f"Nouveau RDV: {name} ({client_phone}) - {start_time.strftime('%d/%m à %H:%M')}",
                    from_=TWILIO_FROM,
                    to=SMS_RECIPIENT
                )
            sms_sent = True
        except Exception as e:
            print(f"Erreur Twilio: {e}")
            sms_sent = False
    return {
        "success": True,
        "message": f"✅ Rendez-vous confirmé pour {name} le {start_time.strftime('%d/%m/%Y à %H:%M')}. Un SMS de confirmation avec le lien Google Meet a été envoyé au {client_phone}."
    }

@app.route("/mcp", methods=["OPTIONS"])
def mcp_options():
    response = jsonify({})
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response
