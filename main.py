from flask import Flask, request, jsonify, Response
import requests
from datetime import datetime, timedelta
import os
import pytz
from twilio.rest import Client
import json
import uuid
import time

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

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")

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

# Initialisation globale (IMPORTANT POUR GUNICORN)
print("🚀 Chargement du serveur MCP SSE Autoscale Calendar")
print(f"📅 Calendrier: {CALENDAR_ID}")
print(f"📱 SMS depuis: {TWILIO_FROM}")

ACCESS_TOKEN = get_access_token()
if ACCESS_TOKEN:
    print("✅ Access token configuré")
else:
    print("⚠️  Access token non disponible - vérifiez les variables OAuth")

# ... tout le reste de ton code (routes, logique, etc.) inchangé

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Autoscale Calendar MCP"})

# ... tout ton code de routes MCP etc.

# NE RIEN METTRE dans if __name__ == "__main__" pour Railway!
