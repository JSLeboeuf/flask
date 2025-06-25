from flask import Flask, request, jsonify, Response
import requests
from datetime import datetime, timedelta
import os
import pytz
from twilio.rest import Client
import json

app = Flask(__name__)

# Configuration
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
CALENDAR_ID = os.environ.get("CALENDAR_ID")
ACCESS_TOKEN = os.environ.get("GOOGLE_ACCESS_TOKEN")
TIMEZONE = "America/Toronto"

TWILIO_SID = os.environ.get("TWILIO_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN")
TWILIO_FROM = os.environ.get("TWILIO_FROM")
SMS_RECIPIENT = os.environ.get("SMS_RECIPIENT")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT")

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Autoscale Calendar MCP"})

@app.route("/mcp", methods=["GET"])
def mcp_sse():
    """Route SSE pour ElevenLabs MCP"""
    def generate():
        # Format exact attendu par ElevenLabs
        yield "event: ready\n"
        yield f"data: {json.dumps({'ready': True})}\n\n"
        
        # Envoyer la liste des outils disponibles
        tools_event = {
            "type": "tools",
            "tools": [{
                "name": "book_appointment",
                "description": "Réserver un rendez-vous dans Google Calendar avec envoi SMS",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Nom complet du client"
                        },
                        "phone": {
                            "type": "string",
                            "description": "Numéro de téléphone du client (ex: 514-123-4567)"
                        },
                        "email": {
                            "type": "string",
                            "description": "Email du client (optionnel)"
                        },
                        "start": {
                            "type": "string",
                            "description": "Date et heure du rendez-vous au format ISO (ex: 2025-06-27T14:00:00)"
                        }
                    },
                    "required": ["name", "phone", "start"]
                }
            }]
        }
        yield f"data: {json.dumps(tools_event)}\n\n"
    
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )

@app.route("/mcp", methods=["POST"])
def mcp_execute():
    """Exécution des commandes MCP"""
    data = request.json
    print(f"[MCP] Requête reçue: {json.dumps(data)}")
    
    # Extraire les paramètres selon le format ElevenLabs
    if "method" in data and data["method"] == "tools/call":
        tool_name = data.get("params", {}).get("name", "book_appointment")
        params = data.get("params", {}).get("arguments", {})
    else:
        # Format direct
        params = data
    
    name = params.get("name", "Client")
    client_phone = params.get("phone")
    client_email = params.get("email")
    start_str = params.get("start")

    # Validations
    if not start_str:
        return jsonify({"error": "Date et heure requises"}), 400
    
    if not client_phone:
        return jsonify({"error": "Numéro de téléphone requis"}), 400
    
    # Formater le numéro de téléphone
    client_phone = client_phone.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
    if not client_phone.startswith('+'):
        if client_phone.startswith('1'):
            client_phone = '+' + client_phone
        else:
            client_phone = '+1' + client_phone

    # Parser la date
    try:
        if 'T' in start_str:
            # Format ISO complet
            if start_str.endswith('Z'):
                start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                start_time = start_time.astimezone(pytz.timezone(TIMEZONE))
            else:
                start_time = datetime.fromisoformat(start_str)
                if start_time.tzinfo is None:
                    start_time = pytz.timezone(TIMEZONE).localize(start_time)
        else:
            # Format simple
            start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
            start_time = pytz.timezone(TIMEZONE).localize(start_time)
    except:
        return jsonify({"error": "Format de date invalide. Utilisez: 2025-06-27T14:00:00"}), 400
    
    # Vérifier le délai minimum
    now = datetime.now(pytz.timezone(TIMEZONE))
    if start_time < now + timedelta(hours=3):
        return jsonify({"error": "Les réservations doivent être faites au moins 3 heures à l'avance"}), 400

    # Vérifier les heures d'ouverture (9h à 21h)
    if start_time.hour < 9 or start_time.hour >= 21:
        return jsonify({"error": "Les rendez-vous sont disponibles de 9h à 21h seulement"}), 400

    end_time = start_time + timedelta(minutes=30)

    # Headers pour Google API
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # Vérifier la disponibilité
    freebusy_url = "https://www.googleapis.com/calendar/v3/freeBusy"
    busy_check = requests.post(freebusy_url, headers=headers, json={
        "timeMin": start_time.isoformat(),
        "timeMax": end_time.isoformat(),
        "timeZone": TIMEZONE,
        "items": [{"id": CALENDAR_ID}]
    })

    if busy_check.status_code != 200:
        print(f"Erreur freeBusy: {busy_check.text}")
        return jsonify({"error": "Erreur lors de la vérification des disponibilités"}), 500

    busy_slots = busy_check.json().get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
    if busy_slots:
        return jsonify({"error": "Ce créneau est déjà réservé. Veuillez choisir un autre moment."}), 409

    # Créer l'événement
    event_url = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events?conferenceDataVersion=1"
    event_payload = {
        "summary": f"Consultation avec {name}",
        "description": (
            f"Client: {name}\n"
            f"Téléphone: {client_phone}\n"
            f"Email: {client_email or 'Non fourni'}\n\n"
            f"Rendez-vous confirmé automatiquement via l'agent ElevenLabs."
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
                "requestId": f"autoscale-{int(datetime.now().timestamp())}",
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

    if client_email:
        event_payload["attendees"].append({"email": client_email})

    created_event = requests.post(event_url, headers=headers, json=event_payload)
    
    if created_event.status_code != 200:
        print(f"Erreur création événement: {created_event.text}")
        return jsonify({"error": "Erreur lors de la création du rendez-vous"}), 500

    event_data = created_event.json()
    meet_link = "Non disponible"
    
    # Extraire le lien Meet
    if "conferenceData" in event_data and "entryPoints" in event_data["conferenceData"]:
        for entry in event_data["conferenceData"]["entryPoints"]:
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri", "Non disponible")
                break

    # Envoyer le SMS
    if TWILIO_SID and TWILIO_TOKEN:
        try:
            twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
            
            # SMS au client
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
            
            # SMS de notification pour toi
            twilio_client.messages.create(
                body=f"Nouveau RDV: {name} ({client_phone}) - {start_time.strftime('%d/%m à %H:%M')}",
                from_=TWILIO_FROM,
                to=SMS_RECIPIENT
            )
            
            sms_sent = True
        except Exception as e:
            print(f"Erreur Twilio: {e}")
            sms_sent = False
    else:
        sms_sent = False

    # Réponse de succès
    response = {
        "success": True,
        "message": f"Rendez-vous confirmé pour {name} le {start_time.strftime('%d/%m/%Y à %H:%M')}",
        "details": {
            "client_name": name,
            "client_phone": client_phone,
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "meet_link": meet_link,
            "sms_sent": sms_sent
        }
    }

    return jsonify(response)

if __name__ == "__main__":
    # Vérification au démarrage
    if not ACCESS_TOKEN or ACCESS_TOKEN == "PASTE_YOUR_ACCESS_TOKEN_HERE":
        print("⚠️  ERREUR: GOOGLE_ACCESS_TOKEN non configuré!")
        print("Configurez le token dans les variables Railway")
    else:
        print("✅ Serveur MCP Autoscale Calendar démarré")
        print(f"📅 Calendrier: {CALENDAR_ID}")
        print(f"📱 SMS depuis: {TWILIO_FROM}")
    
    app.run(host="0.0.0.0", port=8080)
