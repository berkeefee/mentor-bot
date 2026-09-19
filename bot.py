import os
import sys
import sqlite3
import re
from datetime import datetime, timedelta, timezone
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import asyncio
import json

TR_TZ = timezone(timedelta(hours=3))

# --- ARKA PLAN ÇALIŞMA LOG YÖNLENDİRMESİ ---
# Bulut ortamlarında (Render/Railway vb.) logları konsoldan izleyebilmek için,
# PORT veya bulut ortam değişkenleri tanımlı ise log dosyası yönlendirmesi devre dışı bırakılır.
if os.name == 'nt' and not os.environ.get("PORT") and not os.environ.get("RENDER") and not os.environ.get("RAILWAY_STATIC_URL"):
    log_dir = os.path.dirname(os.path.abspath(__file__))
    sys.stdout = open(os.path.join(log_dir, "bot_run.log"), "w", encoding="utf-8", buffering=1)
    sys.stderr = open(os.path.join(log_dir, "bot_err.log"), "w", encoding="utf-8", buffering=1)

# --- TCL/TK HATASINI EZEN ARKA PLAN AYARI ---
import matplotlib
matplotlib.use('Agg')  
import matplotlib.pyplot as plt

# --- GOOGLE VE TELEGRAM KÜTÜPHANELERİ ---
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

main_event_loop = None
telegram_app = None

# --- PORT BINDING FOR CLOUD HEALTH CHECKS & WEBHOOKS (Render/Railway) ---
class CloudServerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"200 Bot is running.")

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            if body and telegram_app and main_event_loop:
                data = json.loads(body)
                update = Update.de_json(data, telegram_app.bot)
                asyncio.run_coroutine_threadsafe(telegram_app.process_update(update), main_event_loop)
            self.send_response(200)
            self.end_headers()
        except Exception as e:
            print(f"[Webhook Hata]: POST istegi islenirken hata: {e}", file=sys.stderr)
            self.send_response(200)
            self.end_headers()

    def log_message(self, format, *args):
        return

# --- KİMLİK DOĞRULAMALARI ---
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip()
    except Exception as e:
        print(f"[Uyari]: .env dosyasi yuklenemedi: {e}", file=sys.stderr)

GEMINI_KEY = os.environ.get("GEMINI_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not GEMINI_KEY or not TELEGRAM_TOKEN:
    print("[Hata]: GEMINI_KEY veya TELEGRAM_TOKEN cevre degiskeni eksik! Lutfen ayarlayin.", file=sys.stderr)

client = genai.Client(api_key=GEMINI_KEY, http_options=types.HttpOptions(timeout=180000)) if GEMINI_KEY else None
DB_FILE = os.environ.get("DATABASE_PATH", "ajan_hafiza.db")

# --- 1. SABİTLER, YARDIMCILAR VE SÖZ HAVUZU ---

ALANLAR = ["BESLENME", "SPOR", "UYKU", "KİŞİSEL GELİŞİM", "FİNANS", "SOSYAL İLİŞKİLER", "YAZILIM"]

ALAN_ESLESTIRME = {
    "beslenme": "BESLENME", "diyet": "BESLENME", "yemek": "BESLENME", "gida": "BESLENME", "nutrition": "BESLENME",
    "spor": "SPOR", "fitness": "SPOR", "antrenman": "SPOR", "egzersiz": "SPOR", "gym": "SPOR", "kosu": "SPOR",
    "uyku": "UYKU", "sleep": "UYKU",
    "kisisel gelisim": "KİŞİSEL GELİŞİM", "kisisel": "KİŞİSEL GELİŞİM", "kitap": "KİŞİSEL GELİŞİM", "okuma": "KİŞİSEL GELİŞİM", "gelisim": "KİŞİSEL GELİŞİM",
    "finans": "FİNANS", "para": "FİNANS", "ekonomi": "FİNANS", "borsa": "FİNANS", "yatirim": "FİNANS", "finance": "FİNANS",
    "sosyal iliskiler": "SOSYAL İLİŞKİLER", "sosyal": "SOSYAL İLİŞKİLER", "iliskiler": "SOSYAL İLİŞKİLER", "arkadas": "SOSYAL İLİŞKİLER", "aile": "SOSYAL İLİŞKİLER", "social": "SOSYAL İLİŞKİLER",
    "yazilim": "YAZILIM", "kod": "YAZILIM", "kodlama": "YAZILIM", "software": "YAZILIM", "programlama": "YAZILIM", "proje": "YAZILIM", "developer": "YAZILIM"
}

def _tr_baslik(metin: str) -> str:
    if not metin: return ""
    kucuk = metin.replace("I", "ı").replace("İ", "i").lower()
    return " ".join(("İ" + k[1:]) if k[0] == "i" else (k[0].upper() + k[1:])
                    for k in kucuk.split() if k)

def _alan_normalize(metin: str) -> str | None:
    if not metin: return None
    temiz = metin.strip().lower().replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    if temiz in ALAN_ESLESTIRME:
        return ALAN_ESLESTIRME[temiz]
    for k, v in ALAN_ESLESTIRME.items():
        if k in temiz:
            return v
    return None

SOZ_HAVUZU = [
    "İstemediğin şeyleri yapabildiğin zaman disiplin sahibi olursun.",
    "Olayları kontrol edebildiğin zaman disiplin sahibi olursun.",
    "Eğer güçlüysen daha da güçlenirsin. Ama eğer zayıfsan zayıflarsın.",
    "Sadece kontrol edebildiğin şeylere odaklan.",
    "Pes ettiğin zaman başarısız olursun.",
    "Uzun vadeli bakış açısını benimse ve küçük başarılarla ilerle.",
    "Kaygılarının davranışlarının önüne geçmesine izin verme.",
    "Zirve yalnızlarındır.",
    "Bu hayat kendini başkalarına beğendirmeye ve başkalarına benzemeye çalışacak kadar uzun ve kalitesiz değil. Be yourself.",
    "Başarısızlığın üstesinden en iyi bahaneler gelir.",
    "Çok denemekten, çok çalışmaktan hiçbir şey kaybetmezsin. Bunları yapmayı bıraktığında kaybedersin. Herkes yatağında uyurken koşuya çıkmak, herkes telefondayken bir şey daha öğrenmek, herkes dizi izlerken üretmek. Israrla devam ettiğinde bir şey olmaz diğeri olur; yolun sonunda hayal ettiğin şeyler bir şekilde oluyor.",
    "İnsan rutinlerinin ve ritüellerinin çocuğudur.",
    "Kervan yolda düzülür.",
    "Akıllı düşünene kadar deli köprüyü geçermiş; o yüzden hızlı aksiyon al.",
    "Haz mutluluk değildir.",
    "Beklenmeyeni bekle.",
    "Kaygını besleyecek davranışlardan bilinçli olarak kaçın ve onları besleme.",
    "Kaygının yarattığı felç edici durağanlığa teslim olmak yerine, odağı günlük rutinlere ve sorumluluklara çevirerek hayatı sürdürmek en sağlam zırhtır.",
    "Kendi işini kurmak çok zordur ve sadece acıya katlanabilenler başarılı olur.",
    "Başarılı olmamın nedeni: zor zamanlarda asla vazgeçmedim, bırakmadım.",
    "Başarı için rakiplerinden daha fazla acıya katlanman lazım.",
    "Dişi ağrıyan insan, dişi ağrımayan herkesi mutlu zanneder. — Peyami Safa",
    "Mutluluk hayatından razı olmakla ilgilidir.",
    "Atılırsan ekmek yersin.",
    "O işi daha önce çok iyi yapmış en iyi kişiyi taklit etmen en önemlisi.",
    "'Bugün ne yapayım' diye kalkıyorsan sabah hiç kalkma daha iyi.",
    "Zayıflar yanlışlar yapar; güçlü insanlar hatalar yapıp onlardan gerekli dersleri alıp devam eder.",
    "İnsanlara yapılacak en kötü şey, onlar sana yalvarmadıkça onlara yardım etmendir.",
    "'Güç istemiyorum' tamamen yalandır; herkes çok fazla güç ister.",
    "Güç istemekteki amacımız insanları yönetmek değil, güce muhtaç kalmamak olmalıdır.",
    "İnsanlara hayır diyemiyorsan kendine evet diyemiyorsun.",
    "Sencil olmak için bencil ol.",
    "Mükemmeliyetçi olacağıma ölürüm daha iyi; mükemmel diye bir şey yok.",
    "Her şeyi yaz, çiz, matematiksel hesaba dök.",
    "En güzel veri toplama yöntemi hiçbir şey söylemeden karşı tarafı dinlemek.",
    "İnsanları özgüvensiz veya emin olmadıkları noktalarda cesaretlendir; çok iyi olduğunu düşündüğü konularda fazla pohpohlama.",
    "Direkt kendin bir şey yapma; onlardan ne kapabilirim diye bak.",
    "İnsanların etiketlerine kanma; genelde çoğunluk buna kanar.",
]

def gunun_sozu(analiz: str, tarih: str) -> str:
    secilen_id = None
    m = re.search(r"SÖZ ID:\s*(\d+)", analiz, re.IGNORECASE)
    if m:
        try:
            val = int(m.group(1))
            if 0 <= val < len(SOZ_HAVUZU):
                secilen_id = val
        except (ValueError, TypeError):
            pass
    if secilen_id is None:
        try:
            dt = datetime.strptime(tarih, "%Y-%m-%d")
            secilen_id = dt.toordinal() % len(SOZ_HAVUZU)
        except Exception:
            secilen_id = 0
    soz = SOZ_HAVUZU[secilen_id]
    return f"🗣️ **GÜNÜN SÖZÜ**\n\n> \"{soz}\""

# --- 2. VERİTABANI VE GRAFİK YÖNETİCİSİ ---
class DatabaseManager:
    def __init__(self):
        self.db_url = os.environ.get("DATABASE_URL")
        self.is_postgres = self.db_url is not None and self.db_url.startswith("postgres")

    def get_connection(self):
        if self.is_postgres:
            try:
                import psycopg2
                conn = psycopg2.connect(self.db_url, connect_timeout=3)
                return conn, "%s"
            except Exception as e:
                print(f"[Veritabani Uyari]: DATABASE_URL tanimli ancak baglanti kurulamadi ({e}). Yerel SQLite'a geciliyor...", file=sys.stderr)
        
        db_dir = os.path.dirname(DB_FILE)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        return sqlite3.connect(DB_FILE), "?"

    def veritabanini_hazirla(self):
        try:
            conn, p = self.get_connection()
            cursor = conn.cursor()
            if p == "%s":
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS gunluk_hafiza (
                        id SERIAL PRIMARY KEY,
                        tarih VARCHAR(50),
                        girdi TEXT,
                        analiz TEXT,
                        total_puan REAL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS gun_odak (
                        tarih VARCHAR(50) PRIMARY KEY,
                        alan TEXT,
                        hedef TEXT
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS aktif_kisitlar (
                        id SERIAL PRIMARY KEY,
                        kisit TEXT,
                        baslangic_tarih VARCHAR(50),
                        bitis_tarih VARCHAR(50),
                        aktif INTEGER
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS gunluk_hafiza (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tarih TEXT,
                        girdi TEXT,
                        analiz TEXT,
                        total_puan REAL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS gun_odak (
                        tarih TEXT PRIMARY KEY,
                        alan TEXT,
                        hedef TEXT
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS aktif_kisitlar (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kisit TEXT,
                        baslangic_tarih TEXT,
                        bitis_tarih TEXT,
                        aktif INTEGER
                    )
                """)
            conn.commit()

            yeni_kolonlar = [
                ("uyku_saat", "REAL"),
                ("ana_odak", "TEXT"),
                ("odak_hedef", "TEXT"),
                ("puanlar_json", "TEXT"),
                ("istisna_modu", "INTEGER"),
            ]
            for ad, tip in yeni_kolonlar:
                try:
                    cursor.execute(f"ALTER TABLE gunluk_hafiza ADD COLUMN {ad} {tip}")
                    conn.commit()
                except Exception:
                    try: conn.rollback()
                    except Exception: pass

            conn.close()
        except Exception as e:
            print(f"[Veritabani Hata]: veritabanini_hazirla basarisiz: {e}", file=sys.stderr)

db_manager = DatabaseManager()

def veritabanini_hazirla():
    db_manager.veritabanini_hazirla()

def hafizaya_kaydet(belirlenen_tarih: str, metin: str, analiz_sonucu: str, total_puan: float | None, detay: dict = None) -> bool:
    detay = detay or {}
    uyku_saat = detay.get("uyku_saat")
    ana_odak = detay.get("ana_odak")
    odak_hedef = detay.get("odak_hedef")
    puanlar_json = json.dumps(detay.get("puanlar", {}), ensure_ascii=False) if detay.get("puanlar") is not None else None
    istisna_modu = 1 if detay.get("istisna_modu") else 0
    conn = None
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM gunluk_hafiza WHERE tarih = {p}", (belirlenen_tarih,))
        cursor.execute(
            f"""INSERT INTO gunluk_hafiza 
                (tarih, girdi, analiz, total_puan, uyku_saat, ana_odak, odak_hedef, puanlar_json, istisna_modu) 
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})""",
            (belirlenen_tarih, metin, analiz_sonucu, total_puan, uyku_saat, ana_odak, odak_hedef, puanlar_json, istisna_modu)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Veritabani Hata]: hafizaya_kaydet basarisiz: {e}", file=sys.stderr)
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception: pass
        return False

def son_kayitlari_getir(limit=5) -> str:
    try:
        conn, _ = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT tarih, girdi, analiz FROM gunluk_hafiza ORDER BY tarih DESC LIMIT {int(limit)}")
        rows = cursor.fetchall()
        conn.close()
        if not rows: return "Henüz geçmiş kayıt bulunmuyor."
        
        rows.reverse()
        hafiza_metni = ""
        for row in rows:
            hafiza_metni += f"--- Kayıt Tarihi: {row[0]} ---\nGirdi: {row[1]}\nAnaliz: {row[2]}\n\n"
        return hafiza_metni
    except Exception as e:
        print(f"[Veritabani Uyari]: son_kayitlari_getir hatası: {e}", file=sys.stderr)
        return "Geçmiş kayıtlar geçici olarak yüklenemedi."

def spesifik_tarih_getir(hedef_tarih: str):
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT girdi, analiz, total_puan FROM gunluk_hafiza WHERE tarih = {p} ORDER BY id DESC LIMIT 1", (hedef_tarih,))
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(f"[Veritabani Hata]: spesifik_tarih_getir basarisiz: {e}", file=sys.stderr)
        return None

# --- ODAK VE KISIT YARDIMCILARI ---

def odak_kaydet(tarih: str, alan: str, hedef: str) -> bool:
    conn = None
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM gun_odak WHERE tarih = {p}", (tarih,))
        cursor.execute(f"INSERT INTO gun_odak (tarih, alan, hedef) VALUES ({p}, {p}, {p})", (tarih, alan, hedef))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Veritabani Hata]: odak_kaydet basarisiz: {e}", file=sys.stderr)
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception: pass
        return False

def odak_getir(tarih: str) -> tuple:
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT alan, hedef FROM gun_odak WHERE tarih = {p}", (tarih,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return (row[0], row[1])
        return (None, None)
    except Exception as e:
        print(f"[Veritabani Hata]: odak_getir basarisiz: {e}", file=sys.stderr)
        return (None, None)

def onceki_odak_getir(tarih: str) -> tuple:
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT o.alan, o.hedef, h.puanlar_json, h.total_puan 
                FROM gun_odak o 
                LEFT JOIN gunluk_hafiza h ON o.tarih = h.tarih 
                WHERE o.tarih < {p} 
                ORDER BY o.tarih DESC LIMIT 1""", 
            (tarih,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            o_alan, o_hedef, o_puanlar_json, o_total_puan = row
            odak_puani = None
            if o_puanlar_json:
                try:
                    pj = json.loads(o_puanlar_json)
                    odak_puani = pj.get(o_alan)
                except Exception:
                    pass
            if odak_puani is None:
                odak_puani = o_total_puan
            return (o_alan, o_hedef, odak_puani)
        return (None, None, None)
    except Exception as e:
        print(f"[Veritabani Hata]: onceki_odak_getir basarisiz: {e}", file=sys.stderr)
        return (None, None, None)

def kisit_ekle(kisit: str, baslangic_tarih: str) -> bool:
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"INSERT INTO aktif_kisitlar (kisit, baslangic_tarih, bitis_tarih, aktif) VALUES ({p}, {p}, NULL, 1)", (kisit, baslangic_tarih))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Veritabani Hata]: kisit_ekle basarisiz: {e}", file=sys.stderr)
        return False

def kisit_kapat(kisit_anahtar: str, bitis_tarih: str) -> bool:
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        if p == "%s":
            cursor.execute(f"UPDATE aktif_kisitlar SET aktif = 0, bitis_tarih = %s WHERE aktif = 1 AND kisit ILIKE %s", (bitis_tarih, f"%{kisit_anahtar}%"))
        else:
            cursor.execute(f"UPDATE aktif_kisitlar SET aktif = 0, bitis_tarih = ? WHERE aktif = 1 AND LOWER(kisit) LIKE LOWER(?)", (bitis_tarih, f"%{kisit_anahtar}%"))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Veritabani Hata]: kisit_kapat basarisiz: {e}", file=sys.stderr)
        return False

def aktif_kisitlari_getir() -> list:
    try:
        conn, _ = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT kisit FROM aktif_kisitlar WHERE aktif = 1")
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows] if rows else []
    except Exception as e:
        print(f"[Veritabani Uyari]: aktif_kisitlari_getir hatasi: {e}", file=sys.stderr)
        return []

# --- 3. PUANLAMA, UYKU VE TREND HESAPLAMALARI ---

def puanlari_ayristir(analiz: str) -> dict:
    sonuc = {alan: None for alan in ALANLAR}
    m = re.search(r"PUANLAR:\s*([^\n]+)", analiz, re.IGNORECASE)
    if m:
        satir = m.group(1).strip()
        parcalar = [p.strip() for p in satir.split(";") if p.strip()]
        for p in parcalar:
            if "=" in p:
                k, v = p.split("=", 1)
                alan_norm = _alan_normalize(k.strip())
                if alan_norm:
                    v_clean = v.strip().upper()
                    if v_clean in ["N/A", "NA", "NONE", "-", ""]:
                        sonuc[alan_norm] = None
                    else:
                        val_m = re.search(r"(\d+(?:\.\d+)?)", v_clean)
                        if val_m:
                            try:
                                sonuc[alan_norm] = float(val_m.group(1))
                            except Exception:
                                sonuc[alan_norm] = None
        if any(v is not None for v in sonuc.values()):
            return sonuc

    for alan in ALANLAR:
        pattern = rf"(?:[🍎🏋️😴📚💰🤝💻]\s*)?\*?\*?{re.escape(alan)}\*?\*?[:\s]+(\d+(?:\.\d+)?|N/A)"
        fm = re.search(pattern, analiz, re.IGNORECASE)
        if fm:
            val_str = fm.group(1).strip().upper()
            if val_str not in ["N/A", "NA"]:
                try:
                    sonuc[alan] = float(val_str)
                except Exception:
                    pass
    return sonuc

def agirlikli_skor(puanlar: dict, ana_odak: str = None) -> float | None:
    if not puanlar:
        return None
    gecerli_puanlar = {k: v for k, v in puanlar.items() if v is not None}
    if not gecerli_puanlar:
        return None

    odak_norm = _alan_normalize(ana_odak) if ana_odak else None
    if odak_norm and odak_norm in gecerli_puanlar:
        pay = 0.0
        payda = 0.0
        for alan, puan in gecerli_puanlar.items():
            agirlik = 1.6 if alan == odak_norm else 1.0
            pay += puan * agirlik
            payda += agirlik
        skor = pay / payda
    else:
        skor = sum(gecerli_puanlar.values()) / len(gecerli_puanlar)

    skor = max(0.0, min(10.0, skor))
    return round(skor, 1)

def en_dusuk_iki_alan(puanlar: dict) -> list:
    gecerli = [(k, v) for k, v in puanlar.items() if v is not None]
    if not gecerli:
        return []
    gecerli.sort(key=lambda x: x[1])
    return [k for k, _ in gecerli[:2]]

def ayikla_uyku_saat(metin: str, gemini_yanit: str = "") -> float | None:
    m_gemini = re.search(r"UYKU_SAAT:\s*(\d+(?:\.\d+)?)", gemini_yanit, re.IGNORECASE)
    if m_gemini:
        try:
            return float(m_gemini.group(1))
        except Exception:
            pass
    m_metin = re.search(r"(\d+(?:\.\d+)?)\s*(?:saat|st)\s*(?:uyudum|uyku|yattim|yattım)", metin, re.IGNORECASE)
    if m_metin:
        try:
            return float(m_metin.group(1))
        except Exception:
            pass
    return None

def trend_ozeti(tarih: str) -> str:
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT tarih, total_puan, uyku_saat, ana_odak, puanlar_json, istisna_modu
                FROM gunluk_hafiza
                WHERE tarih <= {p} AND (istisna_modu IS NULL OR istisna_modu = 0)
                ORDER BY tarih DESC LIMIT 7""",
            (tarih,)
        )
        rows = cursor.fetchall()
        conn.close()

        if len(rows) < 4:
            return ""

        rows.reverse()
        maddeler = []

        uykular = [r[2] for r in rows if r[2] is not None]
        if uykular:
            alti_alti = sum(1 for u in uykular if u < 6.0)
            if alti_alti > 0:
                maddeler.append(f"Uyku son {len(rows)} günde {alti_alti} kez 6 saatin altında kaldı.")
            else:
                ortalama_uyku = round(sum(uykular) / len(uykular), 1)
                maddeler.append(f"Uyku ortalaması {ortalama_uyku} saat ile dengeli.")

        alan_puanlari = {a: [] for a in ALANLAR}
        for r in rows:
            if r[4]:
                try:
                    pjs = json.loads(r[4])
                    for a, val in pjs.items():
                        if val is not None and a in alan_puanlari:
                            alan_puanlari[a].append(val)
                except Exception:
                    pass
        for alan, p_list in alan_puanlari.items():
            if len(p_list) >= 3 and (max(p_list) - min(p_list) >= 4):
                dalga_str = " / ".join(str(int(x) if x == int(x) else x) for x in p_list)
                maddeler.append(f"{_tr_baslik(alan)} puanları dalgalı ({dalga_str}).")
                break

        odakli_puanlar = [r[1] for r in rows if r[3] and r[1] is not None]
        odaksiz_puanlar = [r[1] for r in rows if not r[3] and r[1] is not None]
        if odakli_puanlar and odaksiz_puanlar:
            fark = (sum(odakli_puanlar)/len(odakli_puanlar)) - (sum(odaksiz_puanlar)/len(odaksiz_puanlar))
            if abs(fark) >= 0.5:
                if fark > 0:
                    maddeler.append(f"Ana Odak beyan edilen günlerde ortalama skor {round(fark, 1)} puan daha yüksek.")
                else:
                    maddeler.append(f"Ana Odak beyan edilen günlerde ortalama skor {round(abs(fark), 1)} puan daha düşük.")

        if not maddeler:
            toplam_skorlar = [r[1] for r in rows if r[1] is not None]
            if toplam_skorlar:
                ort = round(sum(toplam_skorlar) / len(toplam_skorlar), 1)
                maddeler.append(f"Son {len(rows)} günün genel performans ortalaması {ort} / 10.")

        cikti = "### 📈 TREND\nSon 7 günde:\n" + "\n".join(f"- {m}" for m in maddeler)
        return cikti
    except Exception as e:
        print(f"[Trend Hata]: trend_ozeti olusturulamadi: {e}", file=sys.stderr)
        return ""

def raporu_birlestir(analiz_metni: str, soz_blogu: str, total_puan: float | None, trend_metni: str, istisna_modu: bool) -> str:
    temiz_analiz = re.sub(r"SÖZ ID:\s*\d+\s*", "", analiz_metni, flags=re.IGNORECASE)
    temiz_analiz = re.sub(r"KISIT EKLE:\s*[^\n]+\n?", "", temiz_analiz, flags=re.IGNORECASE)
    temiz_analiz = re.sub(r"KISIT KAPAT:\s*[^\n]+\n?", "", temiz_analiz, flags=re.IGNORECASE)
    temiz_analiz = re.sub(r"PUANLAR:\s*[^\n]+\n?", "", temiz_analiz, flags=re.IGNORECASE)
    temiz_analiz = re.sub(r"UYKU_SAAT:\s*[^\n]+\n?", "", temiz_analiz, flags=re.IGNORECASE)

    if istisna_modu:
        skor_blogu = "\n### 🧮 PERFORMANS SKORU\n⚠️ **İSTİSNA MODU AKTİF:** Puanlama askıya alındı. Toparlanmaya odaklanın.\n"
    elif total_puan is not None:
        skor_blogu = f"\n### 🧮 PERFORMANS SKORU\n🔢 **TOTAL GÜN PUANI:** {total_puan} / 10\n"
    else:
        skor_blogu = "\n### 🧮 PERFORMANS SKORU\nℹ️ **TOTAL GÜN PUANI:** N/A (Puanlanacak yeterli veri yok)\n"

    if "### 🧮 PERFORMANS SKORU" in temiz_analiz:
        temiz_analiz = re.sub(r"### 🧮 PERFORMANS SKORU.*?(?=###|\Z)", skor_blogu, temiz_analiz, flags=re.DOTALL)
    else:
        if "### 🚀 YARIN İÇİN STRATEJİK EMİRLER" in temiz_analiz:
            temiz_analiz = temiz_analiz.replace("### 🚀 YARIN İÇİN STRATEJİK EMİRLER", f"{skor_blogu}\n### 🚀 YARIN İÇİN STRATEJİK EMİRLER")
        else:
            temiz_analiz += f"\n{skor_blogu}"

    son_rapor = f"{soz_blogu}\n\n{temiz_analiz.strip()}"
    if trend_metni and trend_metni.strip():
        son_rapor += f"\n\n{trend_metni.strip()}"

    return son_rapor.strip()

# --- 4. GRAFİK OLUŞTURMA (HER GÜN ÇİZİM & İSTİSNA MODU DESTEKLİ) ---

def grafik_olustur():
    try:
        conn, _ = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tarih, total_puan, istisna_modu 
            FROM gunluk_hafiza 
            ORDER BY tarih ASC, id ASC
        """)
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"[Veritabani Hata]: grafik_olustur DB hatasi: {e}", file=sys.stderr)
        return False

    if not rows:
        return False

    kayitlar = {}
    valid_dates = []
    for r in rows:
        t_str = r[0].strip() if r[0] else ""
        try:
            d_obj = datetime.strptime(t_str, "%Y-%m-%d").date()
            kayitlar[t_str] = (r[1], r[2])
            valid_dates.append(d_obj)
        except Exception:
            continue

    if not valid_dates:
        return False

    min_date = min(valid_dates)
    max_date = max(max(valid_dates), datetime.now(TR_TZ).date())

    all_dates = []
    curr = min_date
    while curr <= max_date:
        all_dates.append(curr)
        curr += timedelta(days=1)

    tarih_str_list = [d.strftime("%d.%m") for d in all_dates]
    
    raw_scores = []
    is_exception_list = []
    
    for d in all_dates:
        d_str = d.strftime("%Y-%m-%d")
        if d_str in kayitlar:
            puan, istisna = kayitlar[d_str]
            is_exc = (istisna == 1)
            is_exception_list.append(is_exc)
            if is_exc:
                raw_scores.append(None)
            elif puan is not None:
                raw_scores.append(round(float(puan), 2))
            else:
                raw_scores.append(0.0)
        else:
            is_exception_list.append(False)
            raw_scores.append(0.0)

    trend_puanlari = [s for s in raw_scores if s is not None]
    genel_ortalama = (sum(trend_puanlari) / len(trend_puanlari)) if trend_puanlari else 5.0
    
    plot_scores = []
    for i, s in enumerate(raw_scores):
        if s is not None:
            plot_scores.append(s)
        else:
            prev_s = plot_scores[i-1] if i > 0 else genel_ortalama
            plot_scores.append(prev_s)

    x_indices = list(range(len(all_dates)))

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(11, 5.5), facecolor='#121214')
    ax.set_facecolor('#18181c')

    split_idx = max(0, len(x_indices) - 10)

    # 1. Önceki Günler: Yeşil Performans Trend Çizgisi & Dolgusu
    if split_idx > 0:
        past_x = x_indices[:split_idx + 1]
        past_y = plot_scores[:split_idx + 1]
        ax.plot(past_x, past_y, color='#10b981', linewidth=2.0, label='Geçmiş Performans Trendi')
        ax.fill_between(past_x, past_y, color='#10b981', alpha=0.08)
        
        # Geçmiş normal noktalar
        past_norm_x = [x for x in x_indices[:split_idx] if not is_exception_list[x]]
        past_norm_y = [plot_scores[x] for x in past_norm_x]
        if past_norm_x:
            ax.scatter(past_norm_x, past_norm_y, color='#10b981', edgecolor='#ffffff', s=25, linewidth=1.2, zorder=3)

    # 2. Son 10 Gün: Parlak Mavi Çizgi & Dolgu & Vurgulu Noktalar
    recent_x = x_indices[split_idx:]
    recent_y = plot_scores[split_idx:]
    ax.plot(recent_x, recent_y, color='#0284c7', linewidth=3.2, label='Son 10 Gün (Güncel Veriler)')
    ax.fill_between(recent_x, recent_y, color='#0284c7', alpha=0.12)

    # Son 10 gün normal noktaları (Mavi)
    recent_norm_x = [x for x in recent_x if not is_exception_list[x]]
    recent_norm_y = [plot_scores[x] for x in recent_norm_x]
    if recent_norm_x:
        ax.scatter(recent_norm_x, recent_norm_y, color='#38bdf8', edgecolor='#ffffff', s=60, linewidth=1.8, zorder=5)

    # 3. İstisna Noktaları (Gri)
    exc_x = [x for x, exc in zip(x_indices, is_exception_list) if exc]
    exc_y = [plot_scores[x] for x in exc_x]
    if exc_x:
        ax.scatter(exc_x, exc_y, color='#6b7280', edgecolor='#9ca3af', s=55, linewidth=1.5, zorder=6, label='İstisna Günü')

    ax.grid(True, linestyle=':', color='#27272a', alpha=0.7)
    ax.tick_params(colors='#a1a1aa', labelsize=9)

    # Puan etiketlerini akıllı yerleştir (Son 10 gün mavi, Eylül'deki diğer kayıtlı günler yeşil)
    for i in x_indices:
        d = all_dates[i]
        if i in recent_x:
            if is_exception_list[i]:
                lbl = "İstisna"
                c = '#9ca3af'
            else:
                lbl = f"{raw_scores[i]}"
                c = '#38bdf8'
            ax.annotate(lbl, (i, plot_scores[i]), textcoords='offset points',
                        xytext=(0, 10), ha='center', fontsize=9.0, fontweight='bold', color=c)
        elif raw_scores[i] is not None and raw_scores[i] > 0 and d >= datetime(2026, 9, 1).date():
            lbl = f"{raw_scores[i]}"
            c = '#10b981'
            ax.annotate(lbl, (i, plot_scores[i]), textcoords='offset points',
                        xytext=(0, 10), ha='center', fontsize=9.0, fontweight='bold', color=c)

    step = max(1, len(x_indices) // 14)
    tick_positions = x_indices[::step]
    if x_indices[-1] not in tick_positions:
        tick_positions.append(x_indices[-1])

    tick_labels = [tarih_str_list[i] for i in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=30, color='#e4e4e7')

    ax.set_title('Gelişim ve Performans Trend Grafiği (Son 10 Gün Vurgulu Görünüm)', color='#f4f4f5', fontsize=13, fontweight='bold', pad=18)
    ax.set_ylabel('Puan (10 Üzerinden)', color='#a1a1aa', fontsize=11, labelpad=10)
    ax.set_ylim(-0.5, 11)

    for spine in ['top', 'right', 'left', 'bottom']:
        ax.spines[spine].set_visible(False)

    legend = ax.legend(facecolor='#18181c', edgecolor='#27272a', labelcolor='#e4e4e7', loc='upper left')
    legend.get_frame().set_linewidth(1.0)

    plt.tight_layout()

    grafik_yolu = "ilerleme_grafigi.png"
    plt.savefig(grafik_yolu, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    return grafik_yolu

def format_date_tr(date_str: str) -> str:
    if not date_str: return ""
    try:
        parts = date_str.strip().split("-")
        if len(parts) == 3:
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
    except Exception:
        pass
    return date_str

def tarih_ayıkla(metin: str):
    temiz_metin = metin.strip()
    ilk_40 = temiz_metin[:40]

    # 1. Format: YYYY-MM-DD
    m1 = re.search(r"\[?(\d{4}-\d{2}-\d{2})\]?", temiz_metin)
    if m1:
        return m1.group(1), temiz_metin.replace(m1.group(0), "").strip()

    # 2. Format a: 3 parçalı yıl içeren DD.MM.YYYY, DD/MM/YYYY, DD-MM-YYYY
    m2_3part = re.search(r"\[?(\d{1,2})[\./\-](\d{1,2})[\./\-](\d{4})\]?", ilk_40)
    if m2_3part:
        gun = int(m2_3part.group(1))
        ay = int(m2_3part.group(2))
        yil = int(m2_3part.group(3))
        if 1 <= gun <= 31 and 1 <= ay <= 12:
            return f"{yil:04d}-{ay:02d}-{gun:02d}", temiz_metin.replace(m2_3part.group(0), "").strip()

    # 2. Format b: 2 parçalı SADECE / ve - kabul (Hata 1: nokta kabul edilmez!)
    m2_2part = re.search(r"\[?(\d{1,2})[/\-](\d{1,2})\]?", ilk_40)
    if m2_2part:
        gun = int(m2_2part.group(1))
        ay = int(m2_2part.group(2))
        yil = datetime.now(TR_TZ).year
        if 1 <= gun <= 31 and 1 <= ay <= 12:
            return f"{yil:04d}-{ay:02d}-{gun:02d}", temiz_metin.replace(m2_2part.group(0), "").strip()

    # 3. Format: 16 eylül, 16 eylul 2026 vb.
    aylar = {
        "ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, 
        "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12
    }
    m3 = re.search(r"\[?(\d{1,2})\s+([a-zA-ZğüşıöçĞÜŞİÖÇ]+)(?:\s+(\d{4}))?\]?", temiz_metin)
    if m3:
        gun = int(m3.group(1))
        ay_str = m3.group(2).lower().replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
        if ay_str in aylar and 1 <= gun <= 31:
            ay = aylar[ay_str]
            yil = int(m3.group(3)) if m3.group(3) else datetime.now(TR_TZ).year
            return f"{yil:04d}-{ay:02d}-{gun:02d}", temiz_metin.replace(m3.group(0), "").strip()

    # 4. Göreceli Kelimeler: sadece dün / dun tam kelime kontrolü
    if re.search(r"\b(dün|dun|dünün|dunun)\b", ilk_40, re.IGNORECASE):
        parsed = (datetime.now(TR_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        return parsed, temiz_metin

    return datetime.now(TR_TZ).strftime("%Y-%m-%d"), temiz_metin

# --- 5. DİNAMİK SİSTEM TALİMATI ---

TEMEL_TALIMAT = """Sen kullanıcının 7/24 gelişimini takip eden, tavizsiz, profesyonel bir Demir İrade Yaşam Mentörü ve Performans Analistisin.
Görevin, kullanıcının gününe ait aktivitelerini analiz etmek, 7 kategoride değerlendirmek ve karne üretmektir.

KATEGORİLER:
1. BESLENME | 2. SPOR | 3. UYKU | 4. KİŞİSEL GELİŞİM | 5. FİNANS | 6. SOSYAL İLİŞKİLER | 7. YAZILIM

DEĞERLENDİRME VE TON İLKELERİ:
- TAVİZSİZ, DİREKT, NET, GERÇEKÇİ, DİSİPLİNLİ. Gerektiğinde sert.
- Saldırı DAVRANIŞA, KARARA, EYLEMSİZLİĞE, SONUCA, DİSİPLİNSİZLİĞE yönelir. ASLA karaktere, kişiliğe, değere yönelmez.
- Dramatizasyon ve kanıtsız yorum yasaktır.
- VERİ UYDURMA YASAĞI: Kullanıcının söylemediği aktiviteyi, sonucu, sağlık durumunu veya geçmiş bilgiyi UYDURMA. Bilgi yoksa alan 'N/A' olur.
- ÇIKTI > ZAMAN: Temel soru: 'Bugünün Ana Odağında somut olarak ne ürettin?'. Tamamlanan iş harcanan süreden önemlidir. Kullanıcı yalnızca süre verdiyse olmayan çıktı uydurma, süre üzerinden değerlendir ve çıktı hedefi belirlemesini iste.
- GROWTH ≠ MAINTENANCE: Ana Odak '🔥 GROWTH MODE', diğer alanlar '🟢 MAINTENANCE MODE' olarak işaretlenir. Ana odak dışındaki bir alanın düşüklüğü tek başına büyük felaket değildir. Ancak ihmal edildiyse dürüstçe söylenir.
- UYKU DEĞERLENDİRMESİ VE ÇAPA TABLOSU:
  Kullanıcı saat söylemese bile niteliksel ifadelerden 0-10 puan ver:
  * 9-10 : dinlenmiş uyandı, düzenli saatte yattı
  * 7-8  : iyi uyudum / yeterliydi
  * 5-6  : idare eder, biraz yorgun
  * 3-4  : kötü uyudum / bölük börçük / geç yattım
  * 1-2  : neredeyse hiç uyumadım
  * N/A  : uyku hakkında HİÇBİR şey söylenmedi
  (Puan ile süre bağımsızdır; süre söylenmedi diye puan atlanmaz.)
- İSTİSNA PROTOKOLÜ: Girdide ciddi finansal kriz, sakatlık, kaza, yas, ağır hastalık veya acil durum varsa puanlama askıya alınır. Çıktının en başına 'İSTİSNA MODU: AKTİF' yaz, tek bir toparlanma adımı ver.
- KISITLAR (HARD CONSTRAINT): Aktif kısıtlar HARD CONSTRAINT'tir; hiçbir öneri bunlarla çelişemez. Sağlık ve rehabilitasyonda tıbbi talimat üretme, hekime yönlendir. Girdide yeni kısıt başlarsa 'KISIT EKLE: <kısıt>', kısıt bittiyse 'KISIT KAPAT: <kısıt>' satırı yaz.
- SÖZ SEÇİMİ: Aşağıdaki SÖZ HAVUZU'ndan günün odağına/durumuna en uygun sözün indeksini 'SÖZ ID: n' olarak belirt. Metni kendin yazma, sadece ID ver.

ZORUNLU ÇIKTI TEKNİK SATIRLARI (Çıktının başında veya sonunda yer almalıdır):
SÖZ ID: [0-37 arası bir tam sayı]
UYKU_SAAT: [Eğer kullanıcı kaç saat uyuduğunu belirttiyse sayı, örn: 7.5; belirtmediyse None]
PUANLAR: BESLENME=X; SPOR=X; UYKU=X; KİŞİSEL GELİŞİM=X; FİNANS=X; SOSYAL İLİŞKİLER=X; YAZILIM=X (Puanı olmayan alanlara N/A yaz)
(Gerekirse KISIT EKLE: ... veya KISIT KAPAT: ... veya İSTİSNA MODU: AKTİF)

RAPOR ŞABLONU:
### 📋 GÜNLÜK FEEDBACK & MENTOR ANALİZİ
[Davranış, karar ve sonuçlara yönelik net değerlendirme]

### 📊 BUGÜNÜN KARNE PUANLARI
* 🍎 Beslenme: X/10 (veya N/A) -> [Gerekçe] [🟢 MAINTENANCE veya 🔥 GROWTH MODE]
* 🏋️ Spor: X/10 (veya N/A) -> [Gerekçe] [🟢 MAINTENANCE veya 🔥 GROWTH MODE]
* 😴 Uyku: X/10 (veya N/A) -> [Gerekçe] [🟢 MAINTENANCE veya 🔥 GROWTH MODE]
* 📚 Kişisel Gelişim: X/10 (veya N/A) -> [Gerekçe] [🟢 MAINTENANCE veya 🔥 GROWTH MODE]
* 💰 Finans: X/10 (veya N/A) -> [Gerekçe] [🟢 MAINTENANCE veya 🔥 GROWTH MODE]
* 🤝 Sosyal İlişkiler: X/10 (veya N/A) -> [Gerekçe] [🟢 MAINTENANCE veya 🔥 GROWTH MODE]
* 💻 Yazılım: X/10 (veya N/A) -> [Gerekçe] [🟢 MAINTENANCE veya 🔥 GROWTH MODE]

### 🧮 PERFORMANS SKORU
🔢 TOTAL GÜN PUANI: [Hesaplama Python tarafından yapılacaktır]

### 🚀 YARIN İÇİN STRATEJİK EMİRLER
1. [Günün tamamı için en önemli düzeltme]
2. [En düşük alan için somut aksiyon]
3. [İkinci en düşük alan için somut aksiyon]

### 🎯 BUGÜNÜN ÇIKTISI
[Ana Odak için çıktı odaklı soru. Örn: Yarın ... odağın için gün sonunda hangi somut özelliği/çıktıyı tamamlamış olacaksın?]
"""

def build_system_instruction(ana_odak=None, odak_hedef=None,
                             onceki_odak=None, onceki_hedef=None, onceki_puan=None,
                             aktif_kisitlar=None, en_dusuk_alanlar=None) -> str:
    ekler = []
    
    soz_listesi_metni = "\n".join(f"[{i}] {s}" for i, s in enumerate(SOZ_HAVUZU))
    ekler.append(f"--- SÖZ HAVUZU (Sadece ID seç: 'SÖZ ID: n') ---\n{soz_listesi_metni}")
    
    if ana_odak:
        ekler.append(f"--- GÜNÜN ANA ODAĞI ---\nAlan: {ana_odak} (🔥 GROWTH MODE, 1.6x ağırlık)\nHedef: {odak_hedef or 'Somut çıktı üretimi'}\n(Diğer alanlar 🟢 MAINTENANCE)")
    else:
        ekler.append("--- GÜNÜN ANA ODAĞI ---\nKullanıcı bugün için Ana Odak beyan etmedi. KENDİ KAFANA GÖRE ODAK UYDURMA! Tüm alanlar düz 1.0x değerlendirilecek. Raporun sonuna /odak hatırlatması ekle.")

    if onceki_odak:
        kapanis_notu = f"Önceki odak: {onceki_odak} (Hedef: {onceki_hedef}, Puan: {onceki_puan})"
        if onceki_puan is not None and onceki_puan < 7.0:
            kapanis_notu += "\n🚨 BU HEDEF KAPANMADI! Analizde açıkça hatırlat ve yarının hedefini yine aynı alanda ver."
        ekler.append(f"--- ÖNCEKİ GÜNÜN KAPANIŞ DURUMU ---\n{kapanis_notu}")

    if aktif_kisitlar:
        kisit_str = "\n".join(f"- {k}" for k in aktif_kisitlar)
        ekler.append(f"--- AKTİF KISITLAR (HARD CONSTRAINT - ASLA ÇELİŞME!) ---\n{kisit_str}\n(Hiçbir öneri bu kısıtlarla çelişemez; doktor tavsiyesini esas al.)")

    if en_dusuk_alanlar:
        alan_str = ", ".join(en_dusuk_alanlar)
        ekler.append(f"--- EN DÜŞÜK ALANLAR ---\nStratejik emirlerde özellikle şu alanlara somut aksiyon ver: {alan_str}")

    return f"{TEMEL_TALIMAT}\n\n" + "\n\n".join(ekler)

async def call_gemini_with_fallback(contents, system_instruction=None):
    primary_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    models_to_try = [
        primary_model,
        "gemini-2.5-flash",
        "gemini-3.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest"
    ]
    seen = set()
    unique_models = []
    for m in models_to_try:
        if m and m not in seen:
            seen.add(m)
            unique_models.append(m)
            
    last_error = None
    for model_name in unique_models:
        for attempt in range(3):
            try:
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7
                )
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                return response
            except Exception as e:
                last_error = e
                err_msg = str(e).lower()
                print(f"[Gemini Deneme]: Model {model_name} (Deneme {attempt+1}/3) basarisiz oldu: {e}", file=sys.stderr)
                if any(x in err_msg for x in ["404", "not found", "deprecated"]):
                    break
                if any(x in err_msg for x in ["429", "resource_exhausted", "quota", "overloaded", "503"]):
                    await asyncio.sleep(2 ** attempt)
                    continue
                break
    raise last_error

# --- 6. TELEGRAM MESAJ YÖNETİCİLERİ ---

async def send_long_message(update: Update, text: str):
    MAX_LENGTH = 4000
    for i in range(0, len(text), MAX_LENGTH):
        await update.message.reply_text(text[i:i+MAX_LENGTH])

async def start_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    karşılama = (
        "👑 **Demir İrade Yaşam Mentörüne Hoş Geldiniz!**\n\n"
        "Ben sizin 7/24 gelişiminizi ve disiplininizi takip eden tavizsiz performans analistinizim.\n\n"
        "🎯 **Temel Komutlar:**\n"
        "• `odak <alan>: <hedef>` -> Günlük ana odağınızı ve somut çıktınızı belirler (Örn: `odak yazılım: Sıralama algoritmasını tamamla`)\n"
        "• `odak` -> Bugünün belirlenmiş odağını sorgular\n"
        "• `grafik` -> Tüm takvim sürecinizi gösteren gelişim grafiğinizi çizer\n"
        "• `getir YYYY-MM-DD` -> Belirli bir tarihteki kaydınızı ve mentor analizini getirir\n"
        "• `sil YYYY-MM-DD` -> İlgili tarihteki kaydı veritabanından siler\n\n"
        "🎙️ **Kullanım:** Sesli mesaj veya metin olarak gününüzü raporlayın; analiz, karne ve puanınız anında hesaplansın!"
    )
    await update.message.reply_text(karşılama, parse_mode="Markdown")

async def grafik_gonder_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grafik_yolu = grafik_olustur()
    if grafik_yolu and os.path.exists(grafik_yolu):
        try:
            with open(grafik_yolu, 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, 
                    photo=photo_file, 
                    caption="📊 **Güncel İlerleme ve Performans Trend Grafiğiniz!**"
                )
        except Exception as photo_err:
            print(f"[Grafik Hata]: send_photo hatasi: {photo_err}", file=sys.stderr)
            await update.message.reply_text(f"📊 Grafiğiniz oluşturuldu ancak gönderilirken bir aksaklık oldu: {photo_err}")
    else:
        await update.message.reply_text("ℹ️ Grafiğinizin çizilebilmesi için veritabanında kaydınızın bulunması gerekmektedir.")

async def odak_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE, gelen_metin: str):
    arg = gelen_metin.strip()
    if arg.lower().startswith("/odak"):
        arg = arg[5:].strip()
    elif arg.lower().startswith("odak"):
        arg = arg[4:].strip()
    if arg.startswith(":"):
        arg = arg[1:].strip()

    bugun = datetime.now(TR_TZ).strftime("%Y-%m-%d")

    if not arg:
        alan, hedef = odak_getir(bugun)
        if alan:
            await update.message.reply_text(
                f"🎯 **Bugünün Ana Odağı:** {_tr_baslik(alan)}\n"
                f"🔥 **Mod:** GROWTH MODE (1.6x Ağırlık)\n"
                f"🎯 **Hedef:** {hedef}\n\n"
                f"Değiştirmek için: `odak <alan>: <somut hedef>`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "ℹ️ Bugün için henüz bir Ana Odak belirlenmedi.\n\n"
                "Belirlemek için: `odak <alan>: <somut hedef>`\n"
                "Örn: `odak yazılım: Company Discovery Engine flow'unu tamamla`",
                parse_mode="Markdown"
            )
        return

    parcalar = re.split(r"[:\-–]", arg, maxsplit=1)
    if len(parcalar) < 2 or not parcalar[1].strip():
        await update.message.reply_text(
            "⚠️ **Hedefsiz odak kabul edilmez!** Lütfen somut bir çıktı hedefi belirtin.\n\n"
            "Örnek: `odak yazılım: Company Discovery Engine flow'unu tamamla`",
            parse_mode="Markdown"
        )
        return

    alan_raw = parcalar[0].strip()
    hedef_raw = parcalar[1].strip()
    alan_norm = _alan_normalize(alan_raw)

    if not alan_norm:
        await update.message.reply_text(
            f"⚠️ **Geçersiz odak alanı ('{alan_raw}')!**\n\n"
            "Lütfen geçerli 7 alandan birini seçin:\n"
            "• Beslenme\n• Spor\n• Uyku\n• Kişisel Gelişim\n• Finans\n• Sosyal İlişkiler\n• Yazılım",
            parse_mode="Markdown"
        )
        return

    if odak_kaydet(bugun, alan_norm, hedef_raw):
        await update.message.reply_text(
            f"🎯 **Ana Odak Kaydedildi!**\n"
            f"📅 **Tarih:** {bugun}\n"
            f"🔥 **Alan:** {_tr_baslik(alan_norm)} (GROWTH MODE - 1.6x Ağırlık)\n"
            f"🎯 **Hedef:** {hedef_raw}\n\n"
            f"*Günün sonunda bu hedefin somut çıktısı sorgulanacaktır.*",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("⚠️ Ana odak veritabanına kaydedilirken bir hata oluştu.")

async def odak_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await odak_yoneticisi(update, context, update.message.text)

async def mesaj_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gelen_mesaj = update.message.text
    msg_clean = gelen_mesaj.strip().lower().replace('i̇', 'i').replace('ı', 'i')
    
    # 1. ANA ODAK KONTROLÜ (En başta, grafik kontrolünden önce!)
    if msg_clean == "odak" or msg_clean.startswith("odak ") or msg_clean.startswith("odak:"):
        await odak_yoneticisi(update, context, gelen_mesaj)
        return

    # 2. İSTEK ÜZERİNE GRAFİK ÇAĞIRMA
    if msg_clean.startswith("grafik") or "grafik" in msg_clean:
        await grafik_gonder_komutu(update, context)
        return

    # 3. GEÇMİŞ TARİH SORGULAMA (getir YYYY-MM-DD / getir bugün / getir dün)
    if msg_clean.startswith("getir"):
        tarih_bul = re.search(r"\d{4}-\d{2}-\d{2}", gelen_mesaj)
        if tarih_bul:
            istenen_tarih = tarih_bul.group(0)
        elif "bugun" in msg_clean:
            istenen_tarih = datetime.now(TR_TZ).strftime("%Y-%m-%d")
        elif "dun" in msg_clean:
            istenen_tarih = (datetime.now(TR_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            istenen_tarih = None

        if istenen_tarih:
            kayit = spesifik_tarih_getir(istenen_tarih)
            if kayit:
                girdi, analiz, total_puan = kayit
                puan_gosterim = f"{total_puan}/10" if total_puan is not None else "N/A"
                yanit = f"📅 **TARİH:** {istenen_tarih}\n**Sizin Notunuz:** '{girdi}'\n\n{analiz}\n\n🔢 **NET SKOR:** {puan_gosterim}"
                await send_long_message(update, yanit)
            else:
                await update.message.reply_text(f"❌ Hafızamda {istenen_tarih} tarihli bir kayıt bulamadım.")
        else:
            await update.message.reply_text("💡 Doğru format: `getir YYYY-MM-DD` veya `getir bugün` / `getir dün`")
        return

    # 4. VERİ SİLME KOMUTU (sil YYYY-MM-DD / sil DD.MM / sil bugün / sil dün / sil son)
    if msg_clean.startswith("sil"):
        sil_arg = gelen_mesaj[3:].strip()
        sil_clean = sil_arg.lower().replace('i̇', 'i').replace('ı', 'i')
        
        silinecek_tarih = None
        if "son" in sil_clean:
            try:
                conn, _ = db_manager.get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT tarih FROM gunluk_hafiza ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                conn.close()
                silinecek_tarih = row[0] if row else None
            except Exception:
                silinecek_tarih = None
        elif "bugun" in sil_clean:
            silinecek_tarih = datetime.now(TR_TZ).strftime("%Y-%m-%d")
        elif "dun" in sil_clean:
            silinecek_tarih = (datetime.now(TR_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        elif sil_arg:
            parsed_date, _ = tarih_ayıkla(sil_arg)
            silinecek_tarih = parsed_date

        if silinecek_tarih:
            try:
                conn, p = db_manager.get_connection()
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM gunluk_hafiza WHERE tarih = {p}", (silinecek_tarih,))
                cursor.execute(f"DELETE FROM gun_odak WHERE tarih = {p}", (silinecek_tarih,))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[Veritabani Hata]: sil komutu basarisiz: {e}", file=sys.stderr)
            
            grafik_yolu = grafik_olustur()
            if grafik_yolu and os.path.exists(grafik_yolu):
                try:
                    with open(grafik_yolu, 'rb') as photo_file:
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id, 
                            photo=photo_file, 
                            caption=f"🗑️ **{silinecek_tarih}** tarihli kayıtlar silindi ve grafiğiniz güncellendi!"
                        )
                except Exception:
                    await update.message.reply_text(f"🗑️ **{silinecek_tarih}** tarihli tüm kayıtlar veritabanından silindi!")
            else:
                await update.message.reply_text(f"🗑️ **{silinecek_tarih}** tarihli tüm kayıtlar veritabanından silindi!")
        else:
            await update.message.reply_text("💡 **Doğru Silme Formatları:**\n• `sil YYYY-MM-DD`\n• `sil bugün` veya `sil dün`\n• `sil son`")
        return

    # 5. NORMAL GÜNLÜK RAPOR GİRİŞİ
    await update.message.reply_text("⚡ Verileriniz işleniyor, Demir İrade analizi başlatıldı...")
    
    try:
        hedef_tarih, temiz_girdi = tarih_ayıkla(gelen_mesaj)

        # Madde 4: Satır içi "Ana Odak: X" kontrolü
        m_inline = re.search(r"Ana Odak:\s*([^\n\-–:]+)(?:[\-–:]\s*([^\n]+))?", temiz_girdi, re.IGNORECASE)
        if m_inline:
            inline_alan = _alan_normalize(m_inline.group(1).strip())
            if inline_alan:
                inline_hedef = (m_inline.group(2) or "Günlük Odak Hedefi").strip()
                odak_kaydet(hedef_tarih, inline_alan, inline_hedef)

        ana_odak, odak_hedef = odak_getir(hedef_tarih)
        onceki_odak, onceki_hedef, onceki_puan = onceki_odak_getir(hedef_tarih)
        aktif_kisitlar = aktif_kisitlari_getir()

        dinamik_instruction = build_system_instruction(
            ana_odak=ana_odak,
            odak_hedef=odak_hedef,
            onceki_odak=onceki_odak,
            onceki_hedef=onceki_hedef,
            onceki_puan=onceki_puan,
            aktif_kisitlar=aktif_kisitlar
        )

        gecmis_konsept = son_kayitlari_getir(limit=5)
        tarih_bugun = datetime.now(TR_TZ).strftime("%Y-%m-%d")
        
        prompt = (
            f"🚨 GÜNCEL TARİH VE HEDEF BİLGİSİ:\n"
            f"Bugünün güncel Türkiye tarihi: {tarih_bugun}.\n"
            f"Hedeflenen Kayıt Tarihi KESİNLİKLE: {hedef_tarih}\n\n"
            f"Kullanıcının Yeni Girdisi:\n{temiz_girdi}\n\n"
            f"Geçmiş Performanslar (Gelişim kıyası referansı):\n{gecmis_konsept}\n\n"
            f"Talimatlara uygun olarak analizi, teknik satırları (SÖZ ID, PUANLAR vb.) ve karne formatını eksiksiz üret."
        )

        response = await call_gemini_with_fallback(contents=prompt, system_instruction=dinamik_instruction)
        raw_analiz = response.text

        # Kısıt yönetimi ayrıştırma
        m_kisit_ekle = re.search(r"KISIT EKLE:\s*([^\n]+)", raw_analiz, re.IGNORECASE)
        if m_kisit_ekle:
            kisit_ekle(m_kisit_ekle.group(1).strip(), hedef_tarih)

        m_kisit_kapat = re.search(r"KISIT KAPAT:\s*([^\n]+)", raw_analiz, re.IGNORECASE)
        if m_kisit_kapat:
            kisit_kapat(m_kisit_kapat.group(1).strip(), hedef_tarih)

        # İstisna modu kontrolü
        istisna = bool(re.search(r"İSTİSNA MODU:\s*AKTİF|ISTISNA MODU:\s*AKTIF", raw_analiz, re.IGNORECASE))

        # Uyku süresi ayrıştırma
        uyku_saat = ayikla_uyku_saat(temiz_girdi, raw_analiz)

        # Puan ayrıştırma ve skor hesabı
        puanlar = puanlari_ayristir(raw_analiz)
        total_puan = None if istisna else agirlikli_skor(puanlar, ana_odak)

        # Günün sözü ve trend
        soz_blogu = gunun_sozu(raw_analiz, hedef_tarih)
        trend_metni = trend_ozeti(hedef_tarih)

        # Raporu birleştir (Madde 6: Rapor temizliği dahil)
        son_rapor = raporu_birlestir(raw_analiz, soz_blogu, total_puan, trend_metni, istisna)
        await send_long_message(update, son_rapor)

        # Veritabanına kaydet
        detay = {
            "uyku_saat": uyku_saat,
            "ana_odak": ana_odak,
            "odak_hedef": odak_hedef,
            "puanlar": puanlar,
            "istisna_modu": 1 if istisna else 0
        }
        kayit_ok = hafizaya_kaydet(hedef_tarih, temiz_girdi, son_rapor, total_puan, detay=detay)
        if not kayit_ok:
            await update.message.reply_text("⚠️ Analiz üretildi ancak veritabanına kaydedilemedi.")

        # Grafik oluştur ve gönder
        grafik_yolu = grafik_olustur()
        if grafik_yolu and os.path.exists(grafik_yolu):
            try:
                with open(grafik_yolu, 'rb') as photo_file:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=photo_file, 
                        caption=f"📊 **{format_date_tr(hedef_tarih)}** verisi grafiğe işlendi!"
                    )
            except Exception as photo_err:
                print(f"[Grafik Hata]: send_photo hatasi: {photo_err}", file=sys.stderr)
                await update.message.reply_text(f"📊 Grafiğiniz oluşturuldu ancak gönderilirken aksaklık oluştu: {photo_err}")
        else:
            await update.message.reply_text("ℹ️ Grafiğinizin çizilebilmesi için veritabanında kaydınızın bulunması gerekmektedir.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Analiz sırasında bir hata oluştu: {str(e)}")

async def ses_mesaj_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ses = update.message.voice or update.message.audio
    if not ses:
        return
        
    if not client:
        await update.message.reply_text("❌ Gemini API Key tanımlı değil, ses analizi yapılamaz!")
        return
        
    await update.message.reply_text("🎙️ Ses kaydınız alındı. Transkripsiyon ve Demir İrade analizi başlatılıyor...")
    
    detected_mime = getattr(ses, "mime_type", None) or "audio/ogg"
    ext = "ogg"
    if "mp3" in detected_mime: ext = "mp3"
    elif "wav" in detected_mime: ext = "wav"
    elif "m4a" in detected_mime: ext = "m4a"
    
    audio_path = f"ses_kaydi_{update.message.message_id}.{ext}"
    
    try:
        file_obj = await ses.get_file(read_timeout=120, write_timeout=120, connect_timeout=60)
        await file_obj.download_to_drive(audio_path)
        
        print(f"[Sistem]: Ses dosyası Gemini Files API'ye yükleniyor: {audio_path} (MIME: {detected_mime})")
        media_file = await client.aio.files.upload(
            file=audio_path, 
            config=types.UploadFileConfig(
                mime_type=detected_mime, 
                http_options=types.HttpOptions(timeout=180000)
            )
        )
        
        tarih_bugun = datetime.now(TR_TZ).strftime("%Y-%m-%d")
        tarih_dun = (datetime.now(TR_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        gecmis_konsept = son_kayitlari_getir(limit=5)
        aktif_kisitlar = aktif_kisitlari_getir()
        
        dinamik_instruction = build_system_instruction(
            aktif_kisitlar=aktif_kisitlar
        )
        
        prompt = (
            f"🚨 KRİTİK TARİH VE ZAMAN DİREKTİFİ:\n"
            f"Bugünün güncel Türkiye tarihi = {tarih_bugun}, dün = {tarih_dun}.\n\n"
            f"HEDEF TARİH SEÇİMİ:\n"
            f"1. Eğer kullanıcı ses kaydında açıkça bir tarih söylediyse (Örn: '16 Eylül', '16/09', 'dün') hedef tarihi ona göre belirle.\n"
            f"2. Belirtilmediyse {tarih_bugun} kabul et.\n\n"
            f"Geçmiş Performanslar:\n{gecmis_konsept}\n\n"
            f"Görevlerin:\n"
            f"1. Ses kaydının tam Türkçe transkripsiyonunu (dökümünü) yap.\n"
            f"2. Dökümü analiz edip karne, teknik satırlar ve değerlendirme üret.\n\n"
            f"YANIT FORMATIN KESİNLİKLE ŞÖYLE OLMALIDIR:\n"
            f"TARİH: [YYYY-MM-DD formatında hedef tarih]\n"
            f"DÖKÜM:\n[Ses kaydının tam Türkçe dökümü]\n\n"
            f"ANALİZ:\n[Standart günlük mentor analiziniz ve karneniz]\n"
        )
        
        response = await call_gemini_with_fallback(contents=[media_file, prompt], system_instruction=dinamik_instruction)
        full_text = response.text
        
        try:
            await client.aio.files.delete(name=media_file.name)
        except Exception as file_del_err:
            print(f"[Uyari]: Gemini Files silinemedi: {file_del_err}", file=sys.stderr)
            
        hedef_tarih = tarih_bugun
        döküm_bolumu = ""
        analiz_bolumu = ""
        
        if "DÖKÜM:" in full_text and "ANALİZ:" in full_text:
            parts = full_text.split("ANALİZ:", 1)
            döküm_bolumu = parts[0].replace("DÖKÜM:", "").strip()
            analiz_bolumu = parts[1].strip()
        else:
            döküm_bolumu = "Döküm ayıklanamadı."
            analiz_bolumu = full_text

        döküm_bolumu = re.sub(r"TARİH:\s*[^\n]+\n?", "", döküm_bolumu).strip()

        sozlu_tarih, _ = tarih_ayıkla(döküm_bolumu)
        tarih_bulucu = re.search(r"TARİH:\s*([^\n]+)", full_text)
        
        if sozlu_tarih and sozlu_tarih != tarih_bugun:
            hedef_tarih = sozlu_tarih
        elif tarih_bulucu:
            raw_tarih = tarih_bulucu.group(1).strip()
            parsed_date, _ = tarih_ayıkla(raw_tarih)
            if parsed_date:
                hedef_tarih = parsed_date

        m_inline = re.search(r"Ana Odak:\s*([^\n\-–:]+)(?:[\-–:]\s*([^\n]+))?", döküm_bolumu, re.IGNORECASE)
        if m_inline:
            inline_alan = _alan_normalize(m_inline.group(1).strip())
            if inline_alan:
                inline_hedef = (m_inline.group(2) or "Günlük Odak Hedefi").strip()
                odak_kaydet(hedef_tarih, inline_alan, inline_hedef)

        ana_odak, odak_hedef = odak_getir(hedef_tarih)

        m_kisit_ekle = re.search(r"KISIT EKLE:\s*([^\n]+)", analiz_bolumu, re.IGNORECASE)
        if m_kisit_ekle:
            kisit_ekle(m_kisit_ekle.group(1).strip(), hedef_tarih)

        m_kisit_kapat = re.search(r"KISIT KAPAT:\s*([^\n]+)", analiz_bolumu, re.IGNORECASE)
        if m_kisit_kapat:
            kisit_kapat(m_kisit_kapat.group(1).strip(), hedef_tarih)

        istisna = bool(re.search(r"İSTİSNA MODU:\s*AKTİF|ISTISNA MODU:\s*AKTIF", analiz_bolumu, re.IGNORECASE))
        uyku_saat = ayikla_uyku_saat(döküm_bolumu, analiz_bolumu)
        puanlar = puanlari_ayristir(analiz_bolumu)
        total_puan = None if istisna else agirlikli_skor(puanlar, ana_odak)

        soz_blogu = gunun_sozu(analiz_bolumu, hedef_tarih)
        trend_metni = trend_ozeti(hedef_tarih)
        son_analiz = raporu_birlestir(analiz_bolumu, soz_blogu, total_puan, trend_metni, istisna)

        if döküm_bolumu and döküm_bolumu != "Döküm ayıklanamadı.":
            await send_long_message(update, f"✍️ **SES DÖKÜMÜ ({format_date_tr(hedef_tarih)}):**\n\"{döküm_bolumu}\"")
        await send_long_message(update, f"🎯 **MENTÖR ANALİZİ ({format_date_tr(hedef_tarih)}):**\n{son_analiz}")

        detay = {
            "uyku_saat": uyku_saat,
            "ana_odak": ana_odak,
            "odak_hedef": odak_hedef,
            "puanlar": puanlar,
            "istisna_modu": 1 if istisna else 0
        }
        kayit_ok = hafizaya_kaydet(hedef_tarih, f"[Ses Kaydı] {döküm_bolumu}", son_analiz, total_puan, detay=detay)
        if not kayit_ok:
            await update.message.reply_text("⚠️ Analiz üretildi ancak veritabanına kaydedilemedi.")
        
        grafik_yolu = grafik_olustur()
        if grafik_yolu and os.path.exists(grafik_yolu):
            try:
                with open(grafik_yolu, 'rb') as photo_file:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=photo_file, 
                        caption=f"📊 **{format_date_tr(hedef_tarih)}** verisi grafiğe işlendi!"
                    )
            except Exception as photo_err:
                print(f"[Grafik Hata]: send_photo hatasi: {photo_err}", file=sys.stderr)
                await update.message.reply_text(f"📊 Grafiğiniz oluşturuldu ancak gönderilirken aksaklık oluştu: {photo_err}")
        else:
            await update.message.reply_text("ℹ️ Grafiğinizin çizilebilmesi için veritabanında kaydınızın bulunması gerekmektedir.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ses analizi sırasında bir hata oluştu: {str(e)}")
        
    finally:
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception as file_err:
                print(f"[Uyari]: Geçici ses dosyası silinemedi: {file_err}", file=sys.stderr)

# --- 7. ANA ÇALIŞTIRICI SİSTEM ---
if __name__ == "__main__":
    veritabanini_hazirla()
    port = int(os.environ.get("PORT", 0))
    
    app = Application.builder().token(TELEGRAM_TOKEN).read_timeout(120).write_timeout(120).connect_timeout(60).get_updates_read_timeout(120).build()
    
    app.add_handler(CommandHandler("start", start_komutu))
    app.add_handler(CommandHandler("grafik", grafik_gonder_komutu))
    app.add_handler(CommandHandler("odak", odak_komutu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mesaj_yoneticisi))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, ses_mesaj_yoneticisi))
    
    telegram_app = app
    
    if port:
        print("=======================================================")
        print(f"  🚀 BULUT ORTAMI: Webhook Modu Aktif (Port {port})     ")
        print("=======================================================")
        
        main_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_event_loop)
        main_event_loop.run_until_complete(app.initialize())
        main_event_loop.run_until_complete(app.start())
        
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "https://mentor-bot-vpgw.onrender.com")
        webhook_target = f"{render_url.rstrip('/')}/"
        main_event_loop.run_until_complete(app.bot.set_webhook(url=webhook_target, drop_pending_updates=False))
        print(f"[Sistem]: Telegram Webhook kuruldu: {webhook_target}")
        
        server = HTTPServer(("0.0.0.0", port), CloudServerHandler)
        print(f"[Sistem]: HTTP Server port {port} üzerinde dinliyor...")
        
        loop_thread = threading.Thread(target=main_event_loop.run_forever, daemon=True)
        loop_thread.start()
        
        try:
            server.serve_forever()
        except (KeyboardInterrupt, SystemExit):
            pass
    else:
        print("=======================================================")
        print("  🚀 YEREL ORTAM: Polling Modu Başlatılıyor...          ")
        print("=======================================================")
        app.run_polling()
