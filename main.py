from flask import Flask, request, jsonify
import requests
from datetime import datetime, timedelta
import os
import pytz
from twilio.rest import Client
import locale

# Configurer le locale en français
try:
    locale.setlocale(locale.LC_TIME, 'fr_CA.UTF-8')
except:
    pass  # Pas grave si ça échoue

app = Flask(__name__)

# Configuration via variables d'environnement
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
CALENDAR_ID = os.environ.get("CALENDAR_ID")
ACCESS_TOKEN = os.environ.get("GOOGLE_ACCESS_TOKEN")
TIMEZONE = "America/Toronto"

TWILIO_SID = os.environ.get("TWILIO_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN")
TWILIO_FROM = os.environ.get("TWILIO_FROM")
SMS_RECIPIENT = os.environ.get("SMS_RECIPIENT")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT")

"""
L'agent ElevenLabs doit envoyer un JSON comme:
{
    "name": "Jean Dupont",
    "phone": "+15141234567",
    "email": "jean.dupont@email.com",
    "start": "2025-06-27T14:00:00"
}
"""

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Autoscale Calendar MCP"})

@app.route("/mcp", methods=["POST"])
def mcp():
    data = request.json
    print(f"[MCP] Requête reçue: {data}")
    
    name = data.get("name", "Client")
    client_phone = data.get("phone")  # Numéro du client
    client_email = data.get("email")  # Email du client
    start_str = data.get("start")  # Format ISO 8601 requis

    if not start_str:
        return jsonify({"error": "Missing start time"}), 400
    
    if not client_phone:
        return jsonify({"error": "Missing client phone number"}), 400
    
    # Formater le numéro de téléphone
    if not client_phone.startswith('+'):
        if client_phone.startswith('1'):
            client_phone = '+' + client_phone
        else:
            client_phone = '+1' + client_phone.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')

    # Convertir et vérifier le délai minimum de 3h
    try:
        # Si la date n'a pas de timezone, on assume America/Toronto
        if start_str.endswith('Z') or '+' in start_str or '-' in start_str[-6:]:
            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            start_time = start_time.astimezone(pytz.timezone(TIMEZONE))
        else:
            start_time = pytz.timezone(TIMEZONE).localize(datetime.fromisoformat(start_str))
    except:
        return jsonify({"error": "Invalid date format"}), 400
        
    now = datetime.now(pytz.timezone(TIMEZONE))
    if start_time < now + timedelta(hours=3):
        return jsonify({"error": "Trop tôt pour réserver"}), 400

    end_time = start_time + timedelta(minutes=30)

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # Vérification des disponibilités (API freeBusy)
    freebusy_url = "https://www.googleapis.com/calendar/v3/freeBusy"
    busy_check = requests.post(freebusy_url, headers=headers, json={
        "timeMin": start_time.isoformat(),
        "timeMax": end_time.isoformat(),
        "timeZone": TIMEZONE,
        "items": [{"id": CALENDAR_ID}]
    })

    busy_slots = busy_check.json().get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
    if busy_slots:
        return jsonify({"error": "Ce créneau est déjà pris"}), 409

    # Création de l'événement avec lien Google Meet
    event_url = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events?conferenceDataVersion=1"
    event_payload = {
        "summary": f"Consultation avec {name}",
        "description": (
            f"Client: {name}\n"
            f"Téléphone: {client_phone}\n"
            f"Email: {client_email or 'Non fourni'}\n\n"
            f"📅 Date : {start_time.strftime('%Y-%m-%d')}\n"
            f"🕒 Heure : {start_time.strftime('%H:%M')}\n"
            f"⏱ Durée : 30 minutes\n\n"
            f"Le client recevra le lien Google Meet par SMS."
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
            {"email": EMAIL_RECIPIENT},  # Toi (organisateur)
            {"email": client_email} if client_email else {"email": EMAIL_RECIPIENT}  # Le client
        ],
        "conferenceData": {
            "createRequest": {
                "requestId": f"req-{int(datetime.now().timestamp())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"}
            }
        }
    }

    created_event = requests.post(event_url, headers=headers, json=event_payload)
    event_data = created_event.json()
    meet_link = event_data.get("conferenceData", {}).get("entryPoints", [{}])[0].get("uri", "Non disponible")

    # Envoi du SMS via Twilio
    sms_message = (
        f"Bonjour {name},\n\n"
        f"Votre rendez-vous avec Autoscale AI est confirmé!\n\n"
        f"📅 {start_time.strftime('%d %B %Y')}\n"
        f"🕒 {start_time.strftime('%H:%M')}\n"
        f"📍 Vidéoconférence Google Meet\n\n"
        f"Lien: {meet_link}\n\n"
        f"À bientôt!"
    )

    if TWILIO_SID and TWILIO_TOKEN:
        try:
            twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
            # Envoyer au CLIENT
            twilio_client.messages.create(
                body=sms_message,
                from_=TWILIO_FROM,
                to=client_phone  # Numéro du CLIENT
            )
            
            # Optionnel: t'envoyer une copie à toi aussi
            twilio_client.messages.create(
                body=f"[COPIE] Nouveau RDV confirmé pour {name} - {client_phone}",
                from_=TWILIO_FROM,
                to=SMS_RECIPIENT  # Ton numéro
            )
        except Exception as e:
            print(f"Erreur SMS: {e}")
            # Continue même si SMS échoue

    return jsonify({
        "status": "confirmed",
        "client_name": name,
        "client_phone": client_phone,
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "meet_link": meet_link
    })

if __name__ == "__main__":
    # Vérification au démarrage
    if not ACCESS_TOKEN or ACCESS_TOKEN == "PASTE_YOUR_ACCESS_TOKEN_HERE":
        print("⚠️ ERREUR: GOOGLE_ACCESS_TOKEN non configuré!")
        print("Ajoute ton access token dans les variables Railway")
    else:
        print("✅ Configuration OK - Serveur MCP prêt")
    
    app.run(host="0.0.0.0", port=8080)
