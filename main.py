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
TIMEZONE = "America/Toronto"

TWILIO_SID = os.environ.get("TWILIO_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN")
TWILIO_FROM = os.environ.get("TWILIO_FROM")
SMS_RECIPIENT = os.environ.get("SMS_RECIPIENT")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT")

# OAuth tokens
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")

# Variable globale pour l'access token
ACCESS_TOKEN = None

def get_access_token():
    """Obtient un access token valide en utilisant le refresh token"""
    global ACCESS_TOKEN
    
    # Si on a un token dans les variables d'environnement
    env_token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if env_token and env_token != "PASTE_YOUR_ACCESS_TOKEN_HERE":
        ACCESS_TOKEN = env_token
        return ACCESS_TOKEN
    
    # Sinon, utiliser le refresh token
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

# Initialiser l'access token au démarrage
ACCESS_TOKEN = get_access_token()

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Autoscale Calendar MCP"})

@app.route("/mcp", methods=["GET"])
def mcp_tools():
    """Route pour la découverte des outils - Format JSON simple"""
    return jsonify({
        "name": "Autoscale Calendar MCP",
        "version": "1.0.0",
        "protocol_version": "0.1.0",
        "capabilities": {
            "tools": True
        },
        "tools": [
            {
                "name": "book_appointment",
                "description": "Réserver un rendez-vous dans Google Calendar avec envoi SMS",
                "inputSchema": {
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
            }
        ]
    })

@app.route("/mcp/tools/<tool_name>", methods=["POST"])
def execute_tool(tool_name):
    """Exécution d'un outil spécifique"""
    if tool_name != "book_appointment":
        return jsonify({"error": f"Tool '{tool_name}' not found"}), 404
    
    return book_appointment()

@app.route("/mcp", methods=["POST"])
def mcp_execute():
    """Route POST principale pour l'exécution"""
    data = request.json
    
    # Gérer différents formats de requête
    if data.get("method") == "tools/call":
        # Format MCP standard
        tool_name = data.get("params", {}).get("name", "book_appointment")
        params = data.get("params", {}).get("arguments", {})
    elif "tool" in data:
        # Format avec tool spécifié
        tool_name = data.get("tool")
        params = data.get("arguments", data.get("params", {}))
    else:
        # Format direct
        tool_name = "book_appointment"
        params = data
    
    if tool_name == "book_appointment":
        return book_appointment(params)
    else:
        return jsonify({"error": f"Unknown tool: {tool_name}"}), 400

def book_appointment(params=None):
    """Fonction principale de réservation"""
    global ACCESS_TOKEN
    
    if params is None:
        params = request.json or {}
    
    print(f"[BOOKING] Paramètres reçus: {json.dumps(params)}")
    
    name = params.get("name", "Client")
    client_phone = params.get("phone")
    client_email = params.get("email")
    start_str = params.get("start")

    # Validations
    if not start_str:
        return jsonify({"error": "Date et heure requises", "success": False}), 400
    
    if not client_phone:
        return jsonify({"error": "Numéro de téléphone requis", "success": False}), 400
    
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
        return jsonify({
            "error": "Format de date invalide. Utilisez: 2025-06-27T14:00:00",
            "success": False
        }), 400
    
    # Vérifier le délai minimum
    now = datetime.now(pytz.timezone(TIMEZONE))
    if start_time < now + timedelta(hours=3):
        return jsonify({
            "error": "Les réservations doivent être faites au moins 3 heures à l'avance",
            "success": False
        }), 400

    # Vérifier les heures d'ouverture (9h à 21h)
    if start_time.hour < 9 or start_time.hour >= 21:
        return jsonify({
            "error": "Les rendez-vous sont disponibles de 9h à 21h seulement",
            "success": False
        }), 400

    end_time = start_time + timedelta(minutes=30)

    # Vérifier et rafraîchir le token si nécessaire
    if not ACCESS_TOKEN:
        ACCESS_TOKEN = get_access_token()
        if not ACCESS_TOKEN:
            return jsonify({
                "error": "Configuration OAuth manquante",
                "success": False
            }), 500

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

    # Si token expiré, rafraîchir et réessayer
    if busy_check.status_code == 401:
        print("🔄 Token expiré, rafraîchissement...")
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
        return jsonify({
            "error": "Erreur lors de la vérification des disponibilités",
            "success": False,
            "details": busy_check.text
        }), 500

    busy_slots = busy_check.json().get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
    if busy_slots:
        return jsonify({
            "error": "Ce créneau est déjà réservé. Veuillez choisir un autre moment.",
            "success": False
        }), 409

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
        return jsonify({
            "error": "Erreur lors de la création du rendez-vous",
            "success": False,
            "details": created_event.text
        }), 500

    event_data = created_event.json()
    meet_link = "Non disponible"
    
    # Extraire le lien Meet
    if "conferenceData" in event_data and "entryPoints" in event_data["conferenceData"]:
        for entry in event_data["conferenceData"]["entryPoints"]:
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri", "Non disponible")
                break

    # Envoyer le SMS
    sms_sent = False
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

    # Réponse de succès
    response = {
        "success": True,
        "message": f"Rendez-vous confirmé pour {name} le {start_time.strftime('%d/%m/%Y à %H:%M')}",
        "result": {
            "client_name": name,
            "client_phone": client_phone,
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "meet_link": meet_link,
            "sms_sent": sms_sent,
            "calendar_event_id": event_data.get("id")
        }
    }

    return jsonify(response), 200

# Routes additionnelles pour différents formats
@app.route("/mcp/list-tools", methods=["GET"])
def list_tools():
    """Liste des outils disponibles"""
    return jsonify({
        "tools": ["book_appointment"]
    })

if __name__ == "__main__":
    # Vérification au démarrage
    print("🚀 Démarrage du serveur MCP Autoscale Calendar")
    print(f"📅 Calendrier: {CALENDAR_ID}")
    print(f"📱 SMS depuis: {TWILIO_FROM}")
    
    if ACCESS_TOKEN:
        print("✅ Access token configuré")
    else:
        print("⚠️  Access token non disponible - vérifiez les variables OAuth")
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
