import os
import sys
import sqlite3
import re
from datetime import datetime, timedelta
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
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- PORT BINDING FOR CLOUD HEALTH CHECKS (Render/Railway) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running.")

    def log_message(self, format, *args):
        # Mute health check request logging to keep console clean
        return

def start_health_check_server():
    port = int(os.environ.get("PORT", 0))
    if port:
        try:
            server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(f"[Sistem]: Health check server started on port {port}")
        except Exception as e:
            print(f"[Hata]: Health check server baslatilamadi: {e}", file=sys.stderr)

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

client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None
DB_FILE = os.environ.get("DATABASE_PATH", "ajan_hafiza.db")


# --- 1. VERİTABANI VE GRAFİK FONKSİYONLARI ---
class DatabaseManager:
    def __init__(self):
        self.db_url = os.environ.get("DATABASE_URL")
        self.is_postgres = self.db_url is not None and self.db_url.startswith("postgres")

    def get_connection(self):
        if self.is_postgres:
            import psycopg2
            return psycopg2.connect(self.db_url)
        else:
            return sqlite3.connect(DB_FILE)

    def get_placeholder(self):
        return "%s" if self.is_postgres else "?"

    def veritabanini_hazirla(self):
        db_dir = os.path.dirname(DB_FILE)
        if not self.is_postgres and db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        conn = self.get_connection()
        cursor = conn.cursor()
        if self.is_postgres:
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

db_manager = DatabaseManager()

def veritabanini_hazirla():
    db_manager.veritabanini_hazirla()

def hafizaya_kaydet(belirlenen_tarih: str, metin: str, analiz_sonucu: str, total_puan: float):
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    p = db_manager.get_placeholder()
    cursor.execute(
        f"INSERT INTO gunluk_hafiza (tarih, girdi, analiz, total_puan) VALUES ({p}, {p}, {p}, {p})",
        (belirlenen_tarih, metin, analiz_sonucu, total_puan)
    )
    conn.commit()
    conn.close()

def son_kayitlari_getir(limit=5) -> str:
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT tarih, girdi, analiz FROM gunluk_hafiza ORDER BY tarih ASC LIMIT {int(limit)}")
    rows = cursor.fetchall()
    conn.close()
    if not rows: return "Henüz geçmiş kayıt bulunmuyor."
    
    hafiza_metni = ""
    for row in rows:
        hafiza_metni += f"--- Kayıt Tarihi: {row[0]} ---\nGirdi: {row[1]}\nAnaliz: {row[2]}\n\n"
    return hafiza_metni

def spesifik_tarih_getir(hedef_tarih: str):
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    p = db_manager.get_placeholder()
    cursor.execute(f"SELECT girdi, analiz, total_puan FROM gunluk_hafiza WHERE tarih = {p}", (hedef_tarih,))
    row = cursor.fetchone()
    conn.close()
    return row

def grafik_olustur():
    import matplotlib.dates as mdates
    
    conn = db_manager.get_connection()
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
    
    if len(rows) < 1: return False
        
    tarihler = []
    puanlar = []
    for row in rows:
        try:
            date_obj = datetime.strptime(row[0], "%Y-%m-%d")
            tarihler.append(date_obj)
            puanlar.append(round(float(row[1]), 2) if row[1] is not None else 0.0)
        except ValueError:
            continue
            
    if not tarihler: return False
    
    # Set the style to dark background
    plt.style.use('dark_background')
    
    fig, ax = plt.subplots(figsize=(10, 5), facecolor='#121214')
    ax.set_facecolor('#18181c')
    
    # Plot the line with a glowing emerald color and thick lines
    ax.plot(tarihler, puanlar, marker='o', markersize=8, markerfacecolor='#ffffff', 
            markeredgecolor='#10b981', markeredgewidth=2.5, color='#10b981', 
            linewidth=3.5, label='Performans Trendi')
            
    # Fill the area under the curve with a transparent emerald shade
    ax.fill_between(tarihler, puanlar, color='#10b981', alpha=0.12)
    
    # Grid lines configuration
    ax.grid(True, linestyle=':', color='#27272a', alpha=0.7)
    
    # Ticks configuration
    ax.tick_params(colors='#a1a1aa', labelsize=10)
    
    # Format dates on x-axis to be clean and readable ("05.06")
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    
    # Title & Labels
    ax.set_title('Gelişim ve Performans Trend Grafiği', color='#f4f4f5', fontsize=14, fontweight='bold', pad=20)
    ax.set_ylabel('Puan (10 Üzerinden)', color='#a1a1aa', fontsize=11, labelpad=10)
    ax.set_ylim(0, 10.5)
    
    # Remove outer spines for a borderless floating look
    for spine in ['top', 'right', 'left', 'bottom']:
        ax.spines[spine].set_visible(False)
        
    # Rotate date labels
    plt.xticks(rotation=30)
    
    # Custom styled legend
    legend = ax.legend(facecolor='#18181c', edgecolor='#27272a', labelcolor='#e4e4e7')
    legend.get_frame().set_linewidth(1.0)
    
    plt.tight_layout()
    
    grafik_yolu = "ilerleme_grafigi.png"
    plt.savefig(grafik_yolu, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    return grafik_yolu

def tarih_ayıkla(metin: str):
    temiz_metin = metin.strip()
    tarih_deseni = re.match(r"^\[?(\d{4}-\d{2}-\d{2})\]?", temiz_metin)
    if tarih_deseni:
        return tarih_deseni.group(1), temiz_metin[tarih_deseni.end():].strip()
    return datetime.now().strftime("%Y-%m-%d"), temiz_metin


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


# --- 3. TELEGRAM MESAJ YÖNETİMİ ---
async def start_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start komutu verildiğinde çalışır"""
    karşılama = (
        "🎯 **Demir İrade Performans Ajanına Hoş Geldin!**\n\n"
        "Gelişimini 6 alanda (Beslenme, Spor, Kişisel Gelişim, Finans, Sosyal, Yazılım) takip ediyorum.\n\n"
        "📥 **Veri Girişi İçin:** Doğrudan bugün ne yaptığını yazıp gönder.\n"
        "📅 **Geçmiş Gün İçin:** Metnin başına tarih koy. Örn: `[2026-06-01] Bugün yulaf yedim...`\n"
        "🔍 **Eski Raporu Çağırmak İçin:** `getir YYYY-MM-DD` yazıp gönder."
    )
    await update.message.reply_text(karşılama, parse_mode="Markdown")

async def mesaj_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram'dan gelen her normal mesajı işler"""
    gelen_mesaj = update.message.text
    msg_clean = gelen_mesaj.strip().lower().replace('i̇', 'i').replace('ı', 'i')
    
    # --- GEÇMİŞ TARİH SORGULAMA (getir YYYY-MM-DD / getir bugün / getir dün) ---
    if msg_clean.startswith("getir"):
        tarih_bul = re.search(r"\d{4}-\d{2}-\d{2}", gelen_mesaj)
        if tarih_bul:
            istenen_tarih = tarih_bul.group(0)
        elif "bugun" in msg_clean:
            istenen_tarih = datetime.now().strftime("%Y-%m-%d")
        elif "dun" in msg_clean:
            istenen_tarih = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            istenen_tarih = None

        if istenen_tarih:
            kayit = spesifik_tarih_getir(istenen_tarih)
            if kayit:
                girdi, analiz, total_puan = kayit
                yanit = f"📅 **TARİH:** {istenen_tarih}\n**Sizin Notunuz:** '{girdi}'\n\n{analiz}\n\n🔢 **NET SKOR:** {total_puan}/10"
                await update.message.reply_text(yanit)
            else:
                await update.message.reply_text(f"❌ Hafızamda {istenen_tarih} tarihli bir kayıt bulamadım.")
        else:
            await update.message.reply_text("💡 Doğru format: `getir YYYY-MM-DD` veya `getir bugün` / `getir dün`")
        return

    # --- VERİ SİLME KOMUTU (sil YYYY-MM-DD / sil bugün / sil dün / sil son) ---
    if msg_clean.startswith("sil"):
        tarih_bul = re.search(r"\d{4}-\d{2}-\d{2}", gelen_mesaj)
        if tarih_bul:
            silinecek_tarih = tarih_bul.group(0)
        elif "bugun" in msg_clean:
            silinecek_tarih = datetime.now().strftime("%Y-%m-%d")
        elif "dun" in msg_clean:
            silinecek_tarih = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        elif "son" in msg_clean:
            conn = db_manager.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT tarih FROM gunluk_hafiza ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            silinecek_tarih = row[0] if row else None
        else:
            silinecek_tarih = None

        if silinecek_tarih:
            conn = db_manager.get_connection()
            cursor = conn.cursor()
            p = db_manager.get_placeholder()
            cursor.execute(f"DELETE FROM gunluk_hafiza WHERE tarih = {p}", (silinecek_tarih,))
            conn.commit()
            conn.close()
            
            grafik_yolu = grafik_olustur()
            if grafik_yolu and os.path.exists(grafik_yolu):
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, 
                    photo=open(grafik_yolu, 'rb'), 
                    caption=f"🗑️ **{silinecek_tarih}** tarihli kayıtlar silindi ve grafiğiniz güncellendi!"
                )
            else:
                await update.message.reply_text(f"🗑️ **{silinecek_tarih}** tarihli tüm kayıtlar veritabanından başarıyla silindi!")
        else:
            await update.message.reply_text("💡 **Doğru Silme Formatları:**\n• `sil YYYY-MM-DD` (Örn: sil 2026-07-23)\n• `sil bugün` veya `sil dün`\n• `sil son` (En son eklenen kaydı siler)")
        return

    # --- NORMAL GÜNLÜK RAPOR GİRİŞİ ---
    await update.message.reply_text("⚡ Verileriniz işleniyor, Gemini analizi başlatıldı...")
    
    hedef_tarih, temiz_girdi = tarih_ayıkla(gelen_mesaj)
    gecmis_konsept = son_kayitlari_getir(limit=5)
    
    prompt = f"Hedeflenen Kayıt Tarihi: {hedef_tarih}\nKullanıcının Bugünkü Yeni Girdisi: {temiz_girdi}\n\nGeçmiş Performanslar:\n{gecmis_konsept}\n\nAnaliz et, karne üret."
    
    try:
        primary_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        try:
            response = client.models.generate_content(
                model=primary_model,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
            )
        except Exception as primary_error:
            # 503 or rate limit fallback
            fallback_model = "gemini-2.5-flash" if primary_model != "gemini-2.5-flash" else "gemini-1.5-flash"
            print(f"[Model Uyari]: {primary_model} modeli hata verdi ({primary_error}). {fallback_model} modeline geciliyor...", file=sys.stderr)
            response = client.models.generate_content(
                model=fallback_model,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
            )
        
        analiz_sonucu = response.text
        await update.message.reply_text(analiz_sonucu)
        
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
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open(grafik_yolu, 'rb'), caption=f"📊 {hedef_tarih} verisi grafiğe işlendi!")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Analiz sırasında bir hata oluştu: {str(e)}")


async def ses_mesaj_yoneticisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sesli mesajları indirir, transkribe eder ve Gemini ile analiz eder"""
    ses = update.message.voice
    if not ses:
        return
        
    if not client:
        await update.message.reply_text("❌ Gemini API Key tanımlı değil, ses analizi yapılamaz!")
        return
        
    await update.message.reply_text("🎙️ Ses kaydınız alındı. Transkripsiyon ve Gemini analizi başlatılıyor...")
    
    audio_path = f"ses_kaydi_{update.message.message_id}.ogg"
    
    try:
        # Ses dosyasını indir
        file_obj = await ses.get_file()
        await file_obj.download_to_drive(audio_path)
        
        # Dosyayı Gemini Files API'ye yükle
        print(f"[Sistem]: Ses dosyası Gemini Files API'ye yükleniyor: {audio_path}")
        media_file = client.files.upload(file=audio_path, config=types.UploadFileConfig(mime_type="audio/ogg"))
        
        tarih_bugun = datetime.now().strftime("%Y-%m-%d")
        tarih_dun = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        gecmis_konsept = son_kayitlari_getir(limit=5)
        
        prompt = (
            f"Referans Tarihler: Bugün = {tarih_bugun}, Dün = {tarih_dun}\n"
            f"Geçmiş Performanslar:\n{gecmis_konsept}\n\n"
            f"Görevlerin:\n"
            f"1. Ekteki ses kaydını dinle ve kelimesi kelimesine TÜRKÇE transkripsiyonunu (dökümünü) yap.\n"
            f"2. Ses kaydında geçen ifadeleri analiz et. Eğer kullanıcı dün yaptıkları için konuşuyorsa hedef tarihi dünün tarihi ({tarih_dun}) olarak belirle. Aksi halde bugünün tarihi ({tarih_bugun}) olarak kabul et.\n"
            f"3. Bu dökümü sanki kullanıcı metin yazmış gibi analiz edip karne üret.\n\n"
            f"YANIT FORMATIN KESİNLİKLE ŞÖYLE OLMALIDIR:\n"
            f"TARİH: [Belirlenen hedef tarih, format: YYYY-MM-DD]\n"
            f"DÖKÜM:\n[Ses kaydının tam Türkçe dökümü]\n\n"
            f"ANALİZ:\n[Standart günlük mentor analiziniz ve karneniz]\n"
        )
        
        primary_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        try:
            response = client.models.generate_content(
                model=primary_model,
                contents=[media_file, prompt],
                config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
            )
        except Exception as primary_error:
            fallback_model = "gemini-2.5-flash" if primary_model != "gemini-2.5-flash" else "gemini-1.5-flash"
            print(f"[Model Uyari]: {primary_model} ses analizi hatası ({primary_error}). {fallback_model} modeline geçiliyor...", file=sys.stderr)
            response = client.models.generate_content(
                model=fallback_model,
                contents=[media_file, prompt],
                config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
            )
            
        full_text = response.text
        
        # Gemini Files API'den dosyayı temizle
        try:
            client.files.delete(name=media_file.name)
        except Exception as file_del_err:
            print(f"[Uyari]: Gemini Files silinemedi: {file_del_err}", file=sys.stderr)
            
        # Yanıtı parçala
        hedef_tarih = tarih_bugun
        döküm_bolumu = ""
        analiz_bolumu = ""
        
        tarih_bulucu = re.search(r"TARİH:\s*(\d{4}-\d{2}-\d{2})", full_text)
        if tarih_bulucu:
            hedef_tarih = tarih_bulucu.group(1)
            
        if "DÖKÜM:" in full_text and "ANALİZ:" in full_text:
            parts = full_text.split("ANALİZ:")
            döküm_bolumu = parts[0].replace("DÖKÜM:", "").replace(f"TARİH: {hedef_tarih}", "").strip()
            analiz_bolumu = parts[1].strip()
        else:
            döküm_bolumu = "Döküm ayıklanamadı."
            analiz_bolumu = full_text
            
        # Kullanıcıya yanıtı gönder
        await update.message.reply_text(f"✍️ **SES DÖKÜMÜ ({hedef_tarih}):**\n\"{döküm_bolumu}\"\n\n{analiz_bolumu}")
        
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
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open(grafik_yolu, 'rb'), caption=f"📊 {hedef_tarih} verisi grafiğe işlendi!")
            
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
    start_health_check_server()
    veritabanini_hazirla()
    print("=======================================================")
    print("  🚀 Ajan Canlıya Geçiyor, Telegram'a Bağlanıyor!       ")
    print("=======================================================")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_komutu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mesaj_yoneticisi))
    app.add_handler(MessageHandler(filters.VOICE, ses_mesaj_yoneticisi))
    
    print("[Sistem]: Bot şu an canlı! Telegram'a gidip mesaj atabilirsin.")
    app.run_polling()
