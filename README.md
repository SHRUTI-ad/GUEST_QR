# Event Guest QR (staff only)

Simple event flow:
1. Guest list in the app
2. Send each guest a unique QR PDF (1-click email, or download manually)
3. At the gate, staff (logged in) scan the QR → mark **arrived** and/or **lunch claimed**

Guests only receive the PDF/QR. They do not need any website link.

## Run

```bash
cd Event_Guest_QR
pip install -r requirements.txt
python app.py
```

- PC: http://127.0.0.1:5050  
- Phone (same Wi‑Fi): http://YOUR-PC-IP:5050  

**Login:** `staff` / `event123`

## 1-click email of QR PDFs

1. Copy `email_config.example.json` → `email_config.json`
2. Fill SMTP details (for Gmail use an App Password)
3. Open the guest list → **Send QR PDF to all guests** (or **Email QR** per row)

Without email config, use **PDF** download and send manually (WhatsApp/email).

## QR tip for phones

Set `PUBLIC_BASE_URL` to your PC’s LAN URL so QR codes work from phones:

```powershell
$env:PUBLIC_BASE_URL="http://10.197.190.212:5050"
python app.py
```

Staff must stay logged in on the scanning phone.
