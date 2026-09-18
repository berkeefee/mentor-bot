import os
import sys
import sqlite3
import re
from datetime import datetime, timedelta, timezone

TR_TZ = timezone(timedelta(hours=3))
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
import asyncio
import json
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
# Yerel çalıştırmalar için .env dosyası varsa yükle
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


# --- 1. VERİTABANI VE GRAFİK FONKSİYONLARI ---
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
                print(f"[Veritabani Uyari]: Supabase/PostgreSQL baglantisi kurulamadi ({e}). Yerel SQLite'a geciliyor...", file=sys.stderr)
        
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
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Veritabani Hata]: veritabanini_hazirla basarisiz: {e}", file=sys.stderr)

db_manager = DatabaseManager()

def veritabanini_hazirla():
    db_manager.veritabanini_hazirla()

def hafizaya_kaydet(belirlenen_tarih: str, metin: str, analiz_sonucu: str, total_puan: float):
    try:
        conn, p = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"INSERT INTO gunluk_hafiza (tarih, girdi, analiz, total_puan) VALUES ({p}, {p}, {p}, {p})",
            (belirlenen_tarih, metin, analiz_sonucu, total_puan)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Veritabani Hata]: hafizaya_kaydet basarisiz: {e}", file=sys.stderr)

def son_kayitlari_getir(limit=5) -> str:
    try:
        conn, _ = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT tarih, girdi, analiz FROM gunluk_hafiza ORDER BY tarih DESC LIMIT {int(limit)}")
        rows = cursor.fetchall()
        conn.close()
        if not rows: return "Henüz geçmiş kayıt bulunmuyor."
        
        # En güncel kayıtları kronolojik sıraya sok (eskiden yeniye)
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
        cursor.execute(f"SELECT girdi, analiz, total_puan FROM gunluk_hafiza WHERE tarih = {p}", (hedef_tarih,))
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(f"[Veritabani Hata]: spesifik_tarih_getir basarisiz: {e}", file=sys.stderr)
        return None

def grafik_olustur():
    try:
        conn, _ = db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tarih, AVG(total_puan) 
            FROM gunluk_hafiza 
            WHERE total_puan IS NOT NULL 
            GROUP BY tarih 
            ORDER BY tarih ASC
        """)
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"[Veritabani Hata]: grafik_olustur DB hatasi: {e}", file=sys.stderr)
        return False
    
    if len(rows) < 1:
        return False
        
    tarih_str_list = []
    puanlar = []
    for row in rows:
        try:
            date_obj = datetime.strptime(row[0].strip(), "%Y-%m-%d")
            tarih_str_list.append(date_obj.strftime("%d.%m"))
            puanlar.append(round(float(row[1]), 2) if row[1] is not None else 0.0)
        except (ValueError, TypeError, AttributeError):
            continue
            
    if not puanlar:
        return False
    
    x_indices = list(range(len(puanlar)))
    
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(11, 5.5), facecolor='#121214')
    ax.set_facecolor('#18181c')
    
    # 1. Tüm geçmiş çizgisi (Yeşil zemin & çizgi)
    ax.plot(x_indices, puanlar, marker='o', markersize=5, markerfacecolor='#ffffff', 
            markeredgecolor='#10b981', markeredgewidth=1.5, color='#10b981', 
            linewidth=2.2, label='Geçmiş Performans Trendi')
            
    # 2. Son 10 güncel veriyi vurgula (Vurgulu Turkuaz Çizgi & Büyük Noktalar)
    recent_count = min(10, len(puanlar))
    ax.plot(x_indices[-recent_count:], puanlar[-recent_count:], marker='o', markersize=8.5, 
            markerfacecolor='#06b6d4', markeredgecolor='#ffffff', markeredgewidth=2.2, 
            color='#06b6d4', linewidth=3.2, label='Son 10 Günlük Güncel Veriler')
            
    # Arka plan alan dolgusu
    ax.fill_between(x_indices, puanlar, color='#10b981', alpha=0.10)
    ax.grid(True, linestyle=':', color='#27272a', alpha=0.7)
    ax.tick_params(colors='#a1a1aa', labelsize=9)
    
    # YALNIZCA SON 10 GÜNCEL NOKTANIN ÜZERİNE SAYISAL MAVİ PUAN ETİKETLERİNİ YAZ
    for i in range(len(puanlar) - recent_count, len(puanlar)):
        ax.annotate(f'{puanlar[i]}', (x_indices[i], puanlar[i]), textcoords='offset points', 
                    xytext=(0, 9), ha='center', fontsize=9.5, fontweight='bold', color='#38bdf8')
                    
    # X ekseni tarih etiketlerini akıllı yerleştir
    step = max(1, len(x_indices) // 14)
    tick_positions = x_indices[::step]
    if x_indices[-1] not in tick_positions:
        tick_positions.append(x_indices[-1])
        
    tick_labels = [tarih_str_list[i] for i in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=30, color='#e4e4e7')
    
    # Başlıklar ve Sınırlar
    ax.set_title('Gelişim ve Performans Trend Grafiği (Son 10 Gün Detaylı Görünüm)', color='#f4f4f5', fontsize=13, fontweight='bold', pad=18)
    ax.set_ylabel('Puan (10 Üzerinden)', color='#a1a1aa', fontsize=11, labelpad=10)
    ax.set_ylim(0, 11)
    
    for spine in ['top', 'right', 'left', 'bottom']:
        ax.spines[spine].set_visible(False)
        
    legend = ax.legend(facecolor='#18181c', edgecolor='#27272a', labelcolor='#e4e4e7', loc='upper left')
    legend.get_frame().set_linewidth(1.0)
    
    plt.tight_layout()
    
    grafik_yolu = "ilerleme_grafigi.png"
    plt.savefig(grafik_yolu, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    return grafik_yolu

def tarih_ayıkla(metin: str):
    temiz_metin = metin.strip()
    
    # 1. Format: YYYY-MM-DD veya [YYYY-MM-DD]
    m1 = re.match(r"^\[?(\d{4}-\d{2}-\d{2})\]?", temiz_metin)
    if m1:
        return m1.group(1), temiz_metin[m1.end():].strip()
        
    # 2. Format: DD.MM, DD.MM.YYYY, DD/MM, DD/MM/YYYY veya [DD.MM.YYYY]
    m2 = re.match(r"^\[?(\d{1,2})[\./](\d{1,2})(?:[\./](\d{4}))?\]?", temiz_metin)
    if m2:
        gun = int(m2.group(1))
        ay = int(m2.group(2))
        yil = int(m2.group(3)) if m2.group(3) else datetime.now(TR_TZ).year
        return f"{yil:04d}-{ay:02d}-{gun:02d}", temiz_metin[m2.end():].strip()

    # 3. Format: 16 eylul, 16 eylül 2026 vb.
    aylar = {
        "ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, 
        "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12
    }
    m3 = re.match(r"^\[?(\d{1,2})\s+([a-zA-ZğüşıöçĞÜŞİÖÇ]+)(?:\s+(\d{4}))?\]?", temiz_metin, re.IGNORECASE)
    if m3:
        gun = int(m3.group(1))
        ay_str = m3.group(2).lower().replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
        if ay_str in aylar:
            ay = aylar[ay_str]
            yil = int(m3.group(3)) if m3.group(3) else datetime.now(TR_TZ).year
            return f"{yil:04d}-{ay:02d}-{gun:02d}", temiz_metin[m3.end():].strip()

    return datetime.now(TR_TZ).strftime("%Y-%m-%d"), temiz_metin


# --- 2. SİSTEM TALİMATI ---
system_instruction = """Sen kullanıcının 7/24 gelişimini takip eden, tavizsiz, profesyonel bir Yaşam Mentörü ve Performans Analistisin.
Görevin, kullanıcının belirli bir tarihe ait aktivitelerini analiz etmek, 6 kategoride puanlamak ve bu puanların matematiksel ortalamasını çıkarmaktır.

Kategoriler:
1. BESLENME | 2. SPOR | 3. KİŞİSEL GELİŞİM | 4. FİNANS | 5. SOSYAL İLİŞKİLER | 6. YAZILIM

KRİTİK TALİMATLAR:
- Kullanıcı o gün tembellik yaptıysa, az çalıştıysa, kötü bir puan getirdiyse (Ortalama puan 6.5'in altındaysa) ASLA yumuşak konuşma! Gerçekleri yüzüne vur, konfor alanını darmadağın et, sert, acımasız ve disiplinli bir dille onu sarsarak motive et. Potansiyelini çöpe attığını hatırlat.
- Eğer harika çalıştıysa ve yüksek puan aldıysa hakkını ver, disiplinini öv ve çıtayı daha da yukarı koy.
- Puan formatında "N/A" verdiğin (girdi olmayan) alanları ortalama hesabına dahil etme. Sadece sayısal puan verdiğin alanların aritmetik ortalamasını al.

Çıktı formatın KESİNLİKLE birebir şu şablonda olmalıdır:

### 🎯 GÜNLÜK FEEDBACK VE MENTÖR ANALİZİ
[Buraya performans durumuna göre akıcı değerlendirmeni yaz.]

### 📊 BUGÜNÜN KARNE PUANLARI (X / 10)
* 🍎 **BESLENME:** X/10 -> [Neden bu puan?]
* 🏋️ **SPOR:** X/10 -> [Neden bu puan?]
* 📚 **KİŞİSEL GELİŞİM:** X/10 -> [Neden bu puan?]
* 💰 **FİNANS:** X/10 -> [Neden bu puan?]
* 🤝 **SOSYAL İLİŞKİLER:** X/10 -> [Neden bu puan?]
* 💻 **YAZILIM:** X/10 -> [Neden bu puan?]

### 🧮 PERFORMANS SKORU
* 🔢 **TOTAL GÜN PUANI:** [Hesaplanan net ortalama puan, Örn: 7.2]

### 🚀 YARIN İÇIN STRATEJİK EMİRLER
* [Kritik 1-2 madde]
"""

async def call_gemini_with_fallback(contents, system_instruction=None):
    primary_model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    models_to_try = [
        primary_model,
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro"
    ]
    seen = set()
    unique_models = []
    for m in models_to_try:
        if m and m not in seen:
            seen.add(m)
            unique_models.append(m)

    last_error = None
    for model_name in unique_models:
        for attempt in range(2):
            try:
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    http_options=types.HttpOptions(timeout=180000)
                ) if system_instruction else types.GenerateContentConfig(
                    temperature=0.2,
                    http_options=types.HttpOptions(timeout=180000)
                )
                
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                if response and response.text:
                    return response
            except Exception as err:
                last_error = err
                err_str = str(err).lower()
                print(f"[Gemini Uyari]: Model '{model_name}' (deneme {attempt+1}) hata: {err}", file=sys.stderr)
                if "503" in err_str or "unavailable" in err_str or "429" in err_str or "high demand" in err_str:
                    await asyncio.sleep(2 * (attempt + 1))
                else:
                    break

    raise Exception(f"Gemini sunucularındaki geçici yoğunluk (503 High Demand) nedeniyle yanıt alınamadı. Lütfen birkaç saniye sonra tekrar deneyin.")


async def send_long_message(update: Update, text: str, max_length: int = 4000):
    # Splits long text into multiple Telegram messages if it exceeds max_length

    if not text:
        return
    if len(text) <= max_length:
        await update.message.reply_text(text)
        return

    chunks = []
    current_chunk = ""
    for line in text.splitlines(keepends=True):
        if len(current_chunk) + len(line) <= max_length:
            current_chunk += line
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        if chunk.strip():
            await update.message.reply_text(chunk)


# --- 3. TELEGRAM MESAJ YÖNETİMİ ---
async def start_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /start komutu verildiginde calisir
    karşılama = (
        "🎯 **Demir İrade Performans Ajanına Hoş Geldin!**\n\n"
        "Gelişimini 6 alanda (Beslenme, Spor, Kişisel Gelişim, Finans, Sosyal, Yazılım) takip ediyorum.\n\n"
        "📥 **Veri Girişi İçin:** Doğrudan bugün ne yaptığını yazıp gönder.\n"
        "📅 **Geçmiş Gün İçin:** Metnin başına tarih koy. Örn: `[2026-06-01] Bugün yulaf yedim...`\n"
        "📊 **Grafiğinizi İstediğiniz An Çağırmak İçin:** `grafik` veya `/grafik` yazıp gönderin.\n"
        "🔍 **Eski Raporu Çağırmak İçin:** `getir YYYY-MM-DD` yazıp gönder."
    )
    await update.message.reply_text(karşılama, parse_mode="Markdown")

async def grafik_gonder_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Grafigi dogrudan talep edildiginde olusturup gonderir

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

async def mesaj_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Telegram'dan gelen her normal mesaji isler

    gelen_mesaj = update.message.text
    msg_clean = gelen_mesaj.strip().lower().replace('i̇', 'i').replace('ı', 'i')
    
    # --- İSTEK ÜZERİNE GRAFİK ÇAĞIRMA (grafik / grafiği göster / grafik getir) ---
    if msg_clean.startswith("grafik") or "grafik" in msg_clean:
        await grafik_gonder_komutu(update, context)
        return

    # --- GEÇMİŞ TARİH SORGULAMA (getir YYYY-MM-DD / getir bugün / getir dün) ---
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
                yanit = f"📅 **TARİH:** {istenen_tarih}\n**Sizin Notunuz:** '{girdi}'\n\n{analiz}\n\n🔢 **NET SKOR:** {total_puan}/10"
                await send_long_message(update, yanit)
            else:
                await update.message.reply_text(f"❌ Hafızamda {istenen_tarih} tarihli bir kayıt bulamadım.")
        else:
            await update.message.reply_text("💡 Doğru format: `getir YYYY-MM-DD` veya `getir bugün` / `getir dün`")
        return

    # --- VERİ SİLME KOMUTU (sil YYYY-MM-DD / sil DD.MM / sil bugün / sil dün / sil son) ---
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
            except Exception as e:
                silinecek_tarih = None
        elif "bugun" in sil_clean:
            silinecek_tarih = datetime.now(TR_TZ).strftime("%Y-%m-%d")
        elif "dun" in sil_clean:
            silinecek_tarih = (datetime.now(TR_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        elif sil_arg:
            parsed_date, _ = tarih_ayıkla(sil_arg)
            silinecek_tarih = parsed_date
        else:
            silinecek_tarih = None

        if silinecek_tarih:
            try:
                conn, p = db_manager.get_connection()
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM gunluk_hafiza WHERE tarih = {p}", (silinecek_tarih,))
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
                except Exception as photo_err:
                    print(f"[Grafik Hata]: sil photo hatasi: {photo_err}", file=sys.stderr)
                    await update.message.reply_text(f"🗑️ **{silinecek_tarih}** tarihli tüm kayıtlar veritabanından silindi!")
            else:
                await update.message.reply_text(f"🗑️ **{silinecek_tarih}** tarihli tüm kayıtlar veritabanından başarıyla silindi!")
        else:
            await update.message.reply_text("💡 **Doğru Silme Formatları:**\n• `sil YYYY-MM-DD` (Örn: sil 2026-07-23)\n• `sil bugün` veya `sil dün`\n• `sil son` (En son eklenen kaydı siler)")
        return

    # --- NORMAL GÜNLÜK RAPOR GİRİŞİ ---
    await update.message.reply_text("⚡ Verileriniz işleniyor, Gemini analizi başlatıldı...")
    
    try:
        hedef_tarih, temiz_girdi = tarih_ayıkla(gelen_mesaj)
        gecmis_konsept = son_kayitlari_getir(limit=5)
        tarih_bugun = datetime.now(TR_TZ).strftime("%Y-%m-%d")
        
        prompt = (
            f"🚨 KRİTİK TARİH BİLGİSİ: Şu an EYLÜL ayındayız! Bugüne ait güncel Türkiye tarihi = {tarih_bugun}.\n"
            f"Hedeflenen Kayıt Tarihi KESİNLİKLE: {hedef_tarih}\n"
            f"Geçmiş performanslar eski aylara (Haziran/Temmuz 06/07 vb.) ait olabilir. "
            f"Geçmiş kayıtlardaki eski tarihlere bakarak {hedef_tarih} tarihini KESİNLİKLE değiştirme!\n\n"
            f"Kullanıcının Bugünkü Yeni Girdisi: {temiz_girdi}\n\n"
            f"Geçmiş Performanslar (Sadece referans gelişim kıyası içindir):\n{gecmis_konsept}\n\n"
            f"Analiz et, karne üret."
        )

        response = await call_gemini_with_fallback(contents=prompt, system_instruction=system_instruction)

        
        analiz_sonucu = response.text
        await send_long_message(update, analiz_sonucu)
        
        # Puan ayıklama ve veritabanı kaydı
        puan_bulucu = re.search(r"TOTAL GÜN PUANI:\s*\*?([0-9]*\.?[0-9]+)", analiz_sonucu)
        
        total_puan = None
        if puan_bulucu:
            try:
                total_puan = float(puan_bulucu.group(1))
            except ValueError:
                total_puan = 5.0
        else:
            puanlar = [float(x) for x in re.findall(r"([0-9\.]+)\s*/\s*10", analiz_sonucu) if x != '10']
            if puanlar:
                total_puan = sum(puanlar) / len(puanlar)
        
        if total_puan is None:
            total_puan = 5.0
            
        hafizaya_kaydet(hedef_tarih, temiz_girdi, analiz_sonucu, total_puan)
        
        # Grafik oluştur ve gönder
        grafik_yolu = grafik_olustur()
        if grafik_yolu and os.path.exists(grafik_yolu):
            try:
                with open(grafik_yolu, 'rb') as photo_file:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=photo_file, 
                        caption=f"📊 {hedef_tarih} verisi grafiğe işlendi!"
                    )
            except Exception as photo_err:
                print(f"[Grafik Hata]: send_photo hatasi: {photo_err}", file=sys.stderr)
                await update.message.reply_text(f"📊 Grafiğiniz oluşturuldu ancak gönderilirken aksaklık oluştu: {photo_err}")
        else:
            await update.message.reply_text("ℹ️ Grafiğinizin çizilebilmesi için veritabanında kaydınızın bulunması gerekmektedir.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Analiz sırasında bir hata oluştu: {str(e)}")


async def ses_mesaj_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sesli mesajlari veya ses dosyalarini indirir, transkribe eder ve Gemini ile analiz eder

    ses = update.message.voice or update.message.audio
    if not ses:
        return
        
    if not client:
        await update.message.reply_text("❌ Gemini API Key tanımlı değil, ses analizi yapılamaz!")
        return
        
    await update.message.reply_text("🎙️ Ses kaydınız alındı. Transkripsiyon ve Gemini analizi başlatılıyor...")
    
    # MIME türünü dinamik belirle
    detected_mime = getattr(ses, "mime_type", None) or "audio/ogg"
    ext = "ogg"
    if "mp3" in detected_mime: ext = "mp3"
    elif "wav" in detected_mime: ext = "wav"
    elif "m4a" in detected_mime: ext = "m4a"
    
    audio_path = f"ses_kaydi_{update.message.message_id}.{ext}"
    
    try:
        # Ses dosyasını indir
        file_obj = await ses.get_file(read_timeout=120, write_timeout=120, connect_timeout=60)
        await file_obj.download_to_drive(audio_path)
        
        # Dosyayı Gemini Files API'ye yükle (asenkron non-blocking)
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
        
        prompt = (
            f"🚨 KRİTİK TARİH VE ZAMAN DİREKTİFİ:\n"
            f"Şu an EYLÜL ayındayız! Bugüne ait güncel Türkiye tarihi = {tarih_bugun}, dün = {tarih_dun}.\n\n"
            f"HEDEF TARİH SEÇİM HİYERARŞİSİ (ÇOK ÖNEMLİ!):\n"
            f"1. **BİRİNCİL ÖNCELİK (Kullanıcının Sözlü Tarih İfadesi):** Eğer kullanıcı ses kaydında açıkça bir tarih veya gün söylediyse (Örn: '16 Eylül', '16.09', '14 Eylül', 'dün' vb.), hedef tarihi KESİNLİKLE kullanıcının kaydında söylediği o tarihe göre ayarla! (Örn: '16 Eylül' veya '16.09' dediyse KESİNLİKLE 'TARİH: 2026-09-16' yaz).\n"
            f"2. **İKİNCİL ÖNCELİK (Göreceli İfadeler veya Tarih Belirtilmeme):** Kullanıcı 'dün' dediyse {tarih_dun}, 'bugün' dediyse veya hiç tarih söylemediyse {tarih_bugun} olarak belirle.\n"
            f"3. **YASAK:** Aşağıdaki geçmiş kayıtlarda eski aylar (Haziran/Temmuz 06/07) var diye kullanıcının söylediği tarihi değiştirme veya eski ayları hedef tarih yapma!\n\n"
            f"Geçmiş Performanslar (Sadece referans gelişim kıyası içindir):\n{gecmis_konsept}\n\n"
            f"Görevlerin:\n"
            f"1. Ekteki ses kaydını dinle ve kelimesi kelimesine TÜRKÇE transkripsiyonunu (dökümünü) yap.\n"
            f"2. Ses kaydındaki tarihi analiz et. Kullanıcı açıkça bir tarih söylediyse (Örn: '16 Eylül', '16.09') hedef tarihi o tarihe çevir! (Örn: 2026-09-16).\n"
            f"3. Bu dökümü analiz edip karne üret.\n\n"
            f"YANIT FORMATIN KESİNLİKLE ŞÖYLE OLMALIDIR:\n"
            f"TARİH: [Belirlenen hedef tarih, format: YYYY-MM-DD]\n"
            f"DÖKÜM:\n[Ses kaydının tam Türkçe dökümü]\n\n"
            f"ANALİZ:\n[Standart günlük mentor analiziniz ve karneniz]\n"
        )
        
        response = await call_gemini_with_fallback(contents=[media_file, prompt], system_instruction=system_instruction)

            
        full_text = response.text
        
        # Gemini Files API'den dosyayı temizle
        try:
            await client.aio.files.delete(name=media_file.name)
        except Exception as file_del_err:
            print(f"[Uyari]: Gemini Files silinemedi: {file_del_err}", file=sys.stderr)
            
        # Yanıtı parçala
        hedef_tarih = tarih_bugun
        döküm_bolumu = ""
        analiz_bolumu = ""
        
        tarih_bulucu = re.search(r"TARİH:\s*(\d{4}-\d{2}-\d{2})", full_text)
        if tarih_bulucu:
            extracted_date = tarih_bulucu.group(1)
            # Eğer Gemini eski bir ayı (06 veya 07) çıkardıysa VE dökümde açıkça haziran/temmuz geçmiyorsa güncel Eylül tarihiyle düzelt:
            if (extracted_date.startswith("2026-07") or extracted_date.startswith("2026-06")) and ("temmuz" not in full_text.lower() and "haziran" not in full_text.lower()):
                print(f"[Sistem Uyarı]: Gemini eski ay ({extracted_date}) çıkardı, Türkiye tarihi ({tarih_bugun}) ile düzeltiliyor.", file=sys.stderr)
                hedef_tarih = tarih_bugun
            else:
                hedef_tarih = extracted_date


            
        if "DÖKÜM:" in full_text and "ANALİZ:" in full_text:
            parts = full_text.split("ANALİZ:")
            döküm_bolumu = parts[0].replace("DÖKÜM:", "").replace(f"TARİH: {hedef_tarih}", "").strip()
            analiz_bolumu = parts[1].strip()
        else:
            döküm_bolumu = "Döküm ayıklanamadı."
            analiz_bolumu = full_text
            
        # Kullanıcıya yanıtı gönder (Döküm ve Analizi ayrı ayrı güvenle parçala)
        if döküm_bolumu and döküm_bolumu != "Döküm ayıklanamadı.":
            await send_long_message(update, f"✍️ **SES DÖKÜMÜ ({hedef_tarih}):**\n\"{döküm_bolumu}\"")
            await send_long_message(update, f"🎯 **MENTÖR ANALİZİ:**\n{analiz_bolumu}")
        else:
            await send_long_message(update, f"🎯 **MENTÖR ANALİZİ ({hedef_tarih}):**\n{analiz_bolumu}")
        
        # Puan ayıkla
        puan_bulucu = re.search(r"TOTAL GÜN PUANI:\s*\*?([0-9]*\.?[0-9]+)", analiz_bolumu)
        total_puan = None
        if puan_bulucu:
            try:
                total_puan = float(puan_bulucu.group(1))
            except ValueError:
                total_puan = 5.0
        else:
            puanlar = [float(x) for x in re.findall(r"([0-9\.]+)\s*/\s*10", analiz_bolumu) if x != '10']
            if puanlar:
                total_puan = sum(puanlar) / len(puanlar)
        
        if total_puan is None:
            total_puan = 5.0
            
        # Veritabanına kaydet
        hafizaya_kaydet(hedef_tarih, f"[Ses Kaydı] {döküm_bolumu}", analiz_bolumu, total_puan)
        
        # Grafik oluştur ve gönder
        grafik_yolu = grafik_olustur()
        if grafik_yolu and os.path.exists(grafik_yolu):
            try:
                with open(grafik_yolu, 'rb') as photo_file:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=photo_file, 
                        caption=f"📊 {hedef_tarih} verisi grafiğe işlendi!"
                    )
            except Exception as photo_err:
                print(f"[Grafik Hata]: send_photo hatasi: {photo_err}", file=sys.stderr)
                await update.message.reply_text(f"📊 Grafiğiniz oluşturuldu ancak gönderilirken aksaklık oluştu: {photo_err}")
        else:
            await update.message.reply_text("ℹ️ Grafiğinizin çizilebilmesi için veritabanında kaydınızın bulunması gerekmektedir.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ses analizi sırasında bir hata oluştu: {str(e)}")
        
    finally:
        # Geçici ses dosyasını temizle
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception as file_err:
                print(f"[Uyari]: Geçici ses dosyası silinemedi: {file_err}", file=sys.stderr)


# --- 4. ANA ÇALIŞTIRICI SİSTEM ---
if __name__ == "__main__":
    veritabanini_hazirla()
    port = int(os.environ.get("PORT", 0))
    
    app = Application.builder().token(TELEGRAM_TOKEN).read_timeout(120).write_timeout(120).connect_timeout(60).get_updates_read_timeout(120).build()
    app.add_handler(CommandHandler("start", start_komutu))
    app.add_handler(CommandHandler("grafik", grafik_gonder_komutu))
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
