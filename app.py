"""
╔══════════════════════════════════════════════════════════╗
║         ACHADOS — Servidor para Render.com               ║
╠══════════════════════════════════════════════════════════╣
║  Como colocar no Render (grátis):                        ║
║  1. Vai a render.com e cria conta grátis                 ║
║  2. New → Web Service → conecta ao teu GitHub            ║
║  3. Selecciona o repositório com este ficheiro           ║
║  4. Build Command:  pip install -r requirements.txt      ║
║  5. Start Command:  python app.py                        ║
║  6. Clica Deploy!                                        ║
║                                                          ║
║  O URL fica: https://achados-api.onrender.com            ║
╚══════════════════════════════════════════════════════════╝
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os, json, datetime, base64, hashlib, time

app = Flask(__name__)
CORS(app, origins="*")

# ══════════════════════════════════════════════════════════
#  BASE DE DADOS
#  Render tem disco efémero — os dados apagam ao reiniciar.
#  Para produção real usa Supabase (já tens configurado).
#  Para testes, usamos ficheiro JSON.
# ══════════════════════════════════════════════════════════

DB_FILE    = "/tmp/dados.json"   # /tmp funciona no Render
UPLOAD_DIR = "/tmp/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def load():
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except: pass
    return {
        "missing_persons": [],
        "private_cases":   [],
        "alerts":          [],
        "scan_logs":       [],
        "users":           [],
        "devices":         {}
    }

def save(db):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def nid(p=""):
    return f"{p}{int(time.time()*1000)}"

def save_photo(b64, filename):
    try:
        if ',' in b64: b64 = b64.split(',')[1]
        path = os.path.join(UPLOAD_DIR, filename)
        with open(path, 'wb') as f:
            f.write(base64.b64decode(b64))
        return f"/uploads/{filename}"
    except:
        return None

# ══════════════════════════════════════════════════════════
#  ROTAS
# ══════════════════════════════════════════════════════════

@app.route('/')
def index():
    return jsonify({
        "app":     "ACHADOS API",
        "status":  "online",
        "version": "1.0",
        "docs":    "/api/status"
    })

@app.route('/api/status')
def status():
    db = load()
    return jsonify({
        "status":        "online",
        "server":        "ACHADOS Render v1.0",
        "timestamp":     now(),
        "missing_count": len([p for p in db["missing_persons"] if p.get("status") == "active"]),
        "private_count": len([p for p in db["private_cases"]   if p.get("status") == "active"]),
        "alerts_count":  len(db["alerts"]),
        "devices":       list(db["devices"].keys())
    })

# ── ESP32 / Raspberry Pi — Biometria ──────────────────────
@app.route('/api/biometric', methods=['POST'])
def biometric():
    try:
        d = request.get_json()
        if not d: return jsonify({"error": "Sem dados"}), 400

        device_id = d.get("device_id", "desconhecido")
        location  = d.get("location",  "Local desconhecido")
        scan_type = d.get("type",      "both")

        print(f"[{now()}] Scan de {device_id} em '{location}'")

        db = load()
        db["devices"][device_id] = {"last_seen": now(), "location": location}
        db["scan_logs"].insert(0, {
            "id": nid("log_"), "device_id": device_id,
            "location": location, "type": scan_type,
            "result": "processing", "timestamp": now()
        })

        matches = []
        all_persons = db["missing_persons"] + db["private_cases"]

        for p in all_persons:
            if p.get("status") != "active": continue
            if d.get("fingerprint_hash") and p.get("fingerprint_hash") == d["fingerprint_hash"]:
                matches.append({
                    "id": p["id"], "name": p["name"],
                    "age": p.get("age",""), "contact": p.get("contact",""),
                    "confidence": 99.0, "method": "impressão digital"
                })
            if d.get("face_hash") and p.get("face_hash") == d["face_hash"]:
                matches.append({
                    "id": p["id"], "name": p["name"],
                    "age": p.get("age",""), "contact": p.get("contact",""),
                    "confidence": 95.0, "method": "reconhecimento facial"
                })

        if not matches:
            db["scan_logs"][0]["result"] = "no_match"
            save(db)
            return jsonify({"match": False, "message": "Sem correspondência", "timestamp": now()})

        best  = max(matches, key=lambda m: m["confidence"])
        alert = {
            "id": nid("a_"), "person_name": best["name"],
            "device_id": device_id, "location": location,
            "method": best["method"], "confidence": best["confidence"],
            "contact": best.get("contact",""), "timestamp": now()
        }
        db["alerts"].insert(0, alert)
        db["scan_logs"][0]["result"] = "match"
        db["scan_logs"][0]["person"] = best["name"]
        save(db)

        print(f"[{now()}] ✅ MATCH! {best['name']} ({best['confidence']}%)")
        return jsonify({
            "match": True, "person": best,
            "location": location, "action": "ALERT_SENT", "timestamp": now()
        })

    except Exception as e:
        print(f"Erro biometric: {e}")
        return jsonify({"error": str(e)}), 500

# ── Pessoas Desaparecidas ──────────────────────────────────
@app.route('/api/missing', methods=['GET'])
def get_missing():
    db = load()
    return jsonify({
        "persons": [p for p in db["missing_persons"] if p.get("status") == "active"],
        "total":   len(db["missing_persons"])
    })

@app.route('/api/missing', methods=['POST'])
def add_missing():
    try:
        d  = request.get_json()
        if not d or not d.get("name"):
            return jsonify({"error": "Nome obrigatório"}), 400
        db = load()
        pu = save_photo(d["photo_base64"], f"m_{nid()}.jpg") if d.get("photo_base64") else None
        p  = {
            "id": nid("p_"), "name": d["name"], "age": d.get("age",""),
            "location": d.get("location",""), "disappeared_at": d.get("date",""),
            "description": d.get("description",""), "contact": d.get("contact",""),
            "photo_url": pu, "face_hash": d.get("face_hash"),
            "fingerprint_hash": d.get("fingerprint_hash"),
            "status": "active", "created_at": now()
        }
        db["missing_persons"].insert(0, p)
        save(db)
        return jsonify({"success": True, "id": p["id"]}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/missing/<pid>', methods=['DELETE'])
def del_missing(pid):
    db = load()
    for p in db["missing_persons"]:
        if p["id"] == pid:
            p["status"] = "found"
            save(db)
            return jsonify({"success": True})
    return jsonify({"error": "Não encontrado"}), 404

# ── Casos Confidenciais ────────────────────────────────────
@app.route('/api/private', methods=['GET'])
def get_private():
    db = load()
    return jsonify({"cases": [p for p in db["private_cases"] if p.get("status") == "active"]})

@app.route('/api/private', methods=['POST'])
def add_private():
    try:
        d  = request.get_json()
        if not d or not d.get("location"):
            return jsonify({"error": "Local obrigatório"}), 400
        db = load()
        pu = save_photo(d["photo_base64"], f"priv_{nid()}.jpg") if d.get("photo_base64") else None
        c  = {
            "id": nid("priv_"), "name": d.get("name","Desconhecido"),
            "age": d.get("age",""), "location": d["location"],
            "occurred_at": d.get("date",""), "department": d.get("dept",""),
            "urgency": d.get("urgency","normal"), "internal_notes": d.get("notes",""),
            "contact": d.get("contact",""), "photo_url": pu,
            "face_hash": d.get("face_hash"), "fingerprint_hash": d.get("fingerprint_hash"),
            "registered_by": d.get("registered_by",""), "status": "active", "created_at": now()
        }
        db["private_cases"].insert(0, c)
        save(db)
        return jsonify({"success": True, "id": c["id"]}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Alertas & Logs ─────────────────────────────────────────
@app.route('/api/alerts')
def get_alerts():
    return jsonify({"alerts": load()["alerts"][:30]})

@app.route('/api/logs')
def get_logs():
    return jsonify({"logs": load()["scan_logs"][:50]})

@app.route('/api/devices')
def get_devices():
    return jsonify({"devices": load()["devices"]})

# ── Autenticação ───────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        d     = request.get_json()
        email = d.get("email","").lower().strip()
        pw    = d.get("password","")

        # Contas fixas de demonstração
        if email == "admin@achados.pt" and pw == "admin123":
            return jsonify({"success": True, "user": {
                "id": "admin", "name": "Super Admin", "email": email, "role": "admin"
            }})
        if email == "demo@achados.pt" and pw == "demo123":
            return jsonify({"success": True, "user": {
                "id": "demo", "name": "Demo Cidadão", "email": email, "role": "citizen"
            }})

        # Utilizadores registados
        db = load()
        ph = hashlib.sha256(pw.encode()).hexdigest()
        for u in db.get("users", []):
            if u["email"].lower() == email and u.get("pw_hash") == ph:
                return jsonify({"success": True, "user": {
                    k: v for k, v in u.items() if k != "pw_hash"
                }})

        return jsonify({"success": False, "error": "Credenciais inválidas"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        d     = request.get_json()
        name  = d.get("name","").strip()
        email = d.get("email","").lower().strip()
        pw    = d.get("password","")

        if not name or not email or not pw:
            return jsonify({"error": "Preenche todos os campos"}), 400
        if len(pw) < 6:
            return jsonify({"error": "Palavra-passe curta (mín. 6)"}), 400

        db = load()
        if any(u["email"] == email for u in db.get("users",[])):
            return jsonify({"error": "Email já registado"}), 409

        u = {
            "id": nid("u_"), "name": name, "email": email,
            "pw_hash": hashlib.sha256(pw.encode()).hexdigest(),
            "role": "citizen", "institution": "",
            "status": "active", "created_at": now()
        }
        db.setdefault("users", []).append(u)
        save(db)
        return jsonify({"success": True, "user": {
            k: v for k, v in u.items() if k != "pw_hash"
        }}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Utilizadores (admin) ───────────────────────────────────
@app.route('/api/users', methods=['GET'])
def get_users():
    db = load()
    return jsonify({"users": [
        {k: v for k, v in u.items() if k != "pw_hash"}
        for u in db.get("users", [])
    ]})

@app.route('/api/users', methods=['POST'])
def add_user():
    try:
        d     = request.get_json()
        db    = load()
        email = d.get("email","").lower().strip()
        if any(u["email"] == email for u in db.get("users",[])):
            return jsonify({"error": "Email já existe"}), 409
        pw = d.get("password","Temp1234!")
        u  = {
            "id": nid("u_"), "name": d.get("name",""), "email": email,
            "pw_hash": hashlib.sha256(pw.encode()).hexdigest(),
            "role": d.get("role","citizen"), "institution": d.get("institution",""),
            "status": "active", "created_at": now()
        }
        db.setdefault("users",[]).append(u)
        save(db)
        return jsonify({"success": True, "id": u["id"]}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/users/<uid>', methods=['PUT'])
def update_user(uid):
    try:
        d  = request.get_json()
        db = load()
        for u in db.get("users",[]):
            if u["id"] == uid:
                for k in ["name","role","institution","status"]:
                    if k in d: u[k] = d[k]
                save(db)
                return jsonify({"success": True})
        return jsonify({"error": "Não encontrado"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/users/<uid>', methods=['DELETE'])
def del_user(uid):
    db = load()
    db["users"] = [u for u in db.get("users",[]) if u["id"] != uid]
    save(db)
    return jsonify({"success": True})

# ══════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n✅ ACHADOS API a correr na porta {port}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
