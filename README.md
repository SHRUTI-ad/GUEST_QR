# Event Guest QR (staff only)

Simple event flow:
1. Guest list in the app
2. Send each guest a unique QR PDF (1-click email, or download manually)
3. At the gate, staff (logged in) scan the QR → mark **arrived** and/or **lunch claimed**

Guests only receive the PDF/QR. They do not need any website link.

> **Note:** GitHub Pages cannot run this Flask app. Use local run or deploy to Render for a public URL.

## Run on your PC

```bash
cd Event_Guest_QR
pip install -r requirements.txt
python app.py
```

- PC: http://127.0.0.1:5050  
- Phone (same Wi‑Fi): http://YOUR-WIFI-IP:5050  

**Login (local default):** `staff` / `event123`

## Push updated code to GitHub

```powershell
cd C:\Users\shrutia\Downloads\Validation_GUI\Validation_GUI\Event_Guest_QR
git add .
git commit -m "Prepare app for Render deploy"
git push origin main
```

If this folder is not a git repo yet, clone or connect to your repo first:

```powershell
git remote add origin https://github.com/SHRUTI-ad/GUEST_QR.git
git branch -M main
git push -u origin main
```

## Deploy live URL (Render)

1. Go to [https://render.com](https://render.com) → Sign up with GitHub  
2. **New +** → **Web Service** → select `SHRUTI-ad/GUEST_QR`  
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
   - **Plan:** Free  
4. Environment variables:
   - `STAFF_USERNAME` = `staff`
   - `STAFF_PASSWORD` = your strong password
   - `SECRET_KEY` = any long random string
   - After first deploy, set `PUBLIC_BASE_URL` = `https://YOUR-SERVICE.onrender.com`
5. Deploy → open the Render URL → login → download PDFs again so QR codes use the public URL

## 1-click email of QR PDFs

1. Copy `email_config.example.json` → `email_config.json`
2. Fill SMTP details (for Gmail use an App Password)
3. Open the guest list → **Send QR PDF to all guests** (or **Email QR** per row)

Without email config, use **PDF** download and send manually (WhatsApp/email).

## Local phone QR tip

```powershell
$env:PUBLIC_BASE_URL="http://10.19.7.218:5050"
python app.py
```

Staff must stay logged in on the scanning phone.
