# 🏨 Gastino.ai

**Der KI-Assistent für Gastgeber** — Beantwortet Gästeanfragen, routet Roomservice-Bestellungen, verwaltet Reservierungen. Bilingual DE/IT, über WhatsApp.

## Quick Start

```bash
# 1. Repository klonen
cd gastino

# 2. Virtual Environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Dependencies installieren
pip install -r requirements.txt

# 4. Environment Variables
cp .env.example .env
# → .env mit deinen API-Keys füllen

# 5. Starten
python app.py
```

## Architektur

```
Gast (WhatsApp)
    ↓
Meta Cloud API
    ↓
Gastino Core (Flask)
    ├── Tenant Router → Welcher Betrieb?
    ├── Intent Engine → Was will der Gast? (Claude AI)
    ├── Message Router → Wohin routen?
    │   ├── Auto-Reply → AI-generierte Antwort
    │   ├── Order Processor → Küche/Bar (WhatsApp-Gruppe)
    │   ├── Reservation Handler → Tischreservierung
    │   └── Escalation → Rezeption/Betreiber
    └── Response Generator → Antwort in Gastsprache
```

## Betriebstypen

| Typ | Features |
|-----|----------|
| 🏨 Hotel/Pension | Roomservice, Housekeeping, Checkout, Gästeanfragen |
| 🍽️ Restaurant | Reservierungen, Speisekarte, Tischbestellungen |
| 🏠 Ferienwohnung | Verfügbarkeit, Check-in-Infos, lokale Tipps |
| 🍸 Bar/Club | Tischreservierung, Events, Getränkebestellungen |

## API Endpoints

| Method | Route | Beschreibung |
|--------|-------|-------------|
| GET | `/health` | Health Check |
| GET/POST | `/webhook` | WhatsApp Webhook |
| POST | `/api/tenants` | Neuen Betrieb anlegen |
| PUT | `/api/tenants/:id/context` | Knowledge Base updaten |
| POST | `/api/tenants/:id/departments` | Abteilung anlegen |
| GET | `/api/tenants/:id/orders` | Bestellungen auflisten |
| GET | `/api/tenants/:id/reservations` | Reservierungen auflisten |
| GET | `/api/tenants/:id/stats` | Dashboard-Statistiken |

## Deployment (Render)

```bash
# Automatisch via render.yaml
# Oder manuell:
# 1. Neues Web Service auf render.com
# 2. PostgreSQL Datenbank erstellen
# 3. Environment Variables setzen
# 4. Deploy!
```

## Einen neuen Betrieb einrichten

```bash
# 1. Tenant erstellen
curl -X POST http://localhost:5000/api/tenants \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Hotel Sonnenhof",
    "type": "hotel",
    "whatsapp_number": "+39 0471 123456",
    "whatsapp_phone_id": "1234567890",
    "languages": ["de", "it"],
    "system_context": "4-Sterne Hotel in Meran. 30 Zimmer. Frühstück 7-10 Uhr. Wellnessbereich 9-20 Uhr. Tiefgarage kostenlos.",
    "menu_context": "Getränkekarte: Aperol Spritz €8, Hugo €8, Bier 0.5l €5, Hauswein €6/Glas",
    "faq_context": "WLAN: Sonnenhof-Guest / Passwort: willkommen2026. Check-in: ab 14 Uhr. Check-out: bis 10 Uhr."
  }'

# 2. Abteilungen anlegen
curl -X POST http://localhost:5000/api/tenants/TENANT_ID/departments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bar",
    "display_name": "Bar & Lounge",
    "whatsapp_group_id": "120363xxx@g.us",
    "hours": [{"start": "10:00", "end": "23:00"}]
  }'

curl -X POST http://localhost:5000/api/tenants/TENANT_ID/departments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "rezeption",
    "display_name": "Rezeption",
    "whatsapp_group_id": "120363yyy@g.us",
    "is_escalation": true
  }'
```

---

Built with ❤️ in Südtirol · gastino.ai
