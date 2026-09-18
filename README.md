# 🎯 Demir İrade Performans Mentörü Telegram Botu

Kullanıcının 7/24 kişisel gelişimini, günlük aktivitelerini ve zihinsel/fiziksel performansını 6 kritik alanda takip eden, tavizsiz ve disiplinli bir **Yaşam Mentörü ve Performans Analisti Telegram Botu**.

---

## ✨ Öne Çıkan Özellikler

- 📊 **6 Kategoride Detaylı Puanlama:**
  - 🍎 **Beslenme** | 🏋️ **Spor** | 📚 **Kişisel Gelişim** | 💰 **Finans** | 🤝 **Sosyal İlişkiler** | 💻 **Yazılım**
- 🎙️ **Sesli Mesaj Transkripsiyonu & Analizi:**
  - Ses kayıtlarını otomatik olarak Türkçe metne dökme (Gemini Files API) ve ses içeriğini tarih/puanlandırma süzgecinden geçirerek karneleme.
- 📅 **Evrensel Tarih Ayrıştırma:**
  - Metin veya ses kaydındaki tarih ifadelerini (`16.09`, `16.09.2026`, `16/09`, `16 Eylül`, `dün`, `[YYYY-MM-DD]`) otomatik algılama ve ilgili güne işleme.
- 📈 **Görsel İlerleme Trend Grafiği:**
  - Matplotlib tabanlı karanlık tema tasarım.
  - Tüm geçmiş trendi gösterirken **son 10 günü turkuaz renkte öne çıkarma** ve **sayısal mavi puan etiketleri** ekleme.
- 🛡️ **Yüksek Kesintisizlik ve Model Havuzu:**
  - Anlık 503 / 429 Google sunucu yoğunluklarında otomatik yeniden deneme (retry backoff).
  - `gemini-2.5-flash`, `gemini-3.5-flash`, `gemini-2.5-flash-lite`, `gemini-flash-latest` modelleri arasında otomatik yedekleme (fallback).
- ☁️ **Bulut ve Yerel Uyumlu Mimari:**
  - Render / Railway bulut ortamında **Webhook + HTTP Health Check Server** desteği.
  - Yerel çalıştırmalarda otomatik **Polling** moduna geçiş.
- 🗄️ **SQLite & PostgreSQL (Supabase) Çift Veritabanı Desteği.**

---

## 🛠️ Komutlar ve Kullanım Rehberi

### 📥 Veri Girişi
- **Bugün İçin:** Doğrudan bugün ne yaptığınızı yazın veya ses kaydı atın.
  - *Örn:* `Bugün 5 km koştum, yulaf yedim, 2 saat kod yazdım.`
- **Geçmiş Bir Gün İçin:** Cümlenin başına veya içinde tarihi belirtin.
  - *Örn:* `16.09.2026 Bugün borsa çöktü, çok stresli bir gündü.`
  - *Örn:* `16 Eylül spora gidemedim ama 3 saat kod yazdım.`

### 📊 Grafik Çağırma
- Telegram'da `grafik` yazın veya `/grafik` komutunu gönderin. Bot anında güncel trend grafiğinizi çizer ve görsel olarak sunar.

### 🔍 Geçmiş Rapor Sorgulama
- `getir YYYY-MM-DD` *(Örn: `getir 2026-09-16`)*
- `getir bugün` veya `getir dün`

### 🗑️ Veri Silme
- `sil son` *(En son eklenen kaydı ve grafikteki son noktayı siler)*
- `sil 16.09` veya `sil 16 Eylül` veya `sil 2026-09-16`
- `sil bugün` veya `sil dün`

---

## 🚀 Kurulum ve Çalıştırma

### 1. Gereksinimler
Python 3.10+ sürümü gereklidir. Gerekli kütüphaneleri yükleyin:
```bash
pip install -r requirements.txt
```

### 2. Çevre Değişkenleri (`.env`)
Proje ana dizininde bir `.env` dosyası oluşturun ve aşağıdaki anahtarları ekleyin:
```env
GEMINI_KEY=YOUR_GOOGLE_GEMINI_API_KEY
TELEGRAM_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
# İsteğe bağlı Supabase/Postgres bağlantısı:
# DATABASE_URL=postgresql://user:password@host:port/dbname
```

### 3. Yerel Çalıştırma (Polling Modu)
```bash
python bot.py
```

### 4. Bulut Dağıtımı (Render / Railway Webhook Modu)
Render üzerinde bir **Web Service** oluşturun ve aşağıdaki ortam değişkenlerini tanımlayın:
- `GEMINI_KEY`
- `TELEGRAM_TOKEN`
- `PORT` (Render otomatik atar)
- `RENDER_EXTERNAL_URL` (Örn: `https://mentor-bot-vpgw.onrender.com`)

---

## 📁 Proje Yapısı

```text
├── bot.py                # Ana bot kütüphanesi, webhook/polling sunucusu ve işleyiciler
├── ajan_hafiza.db        # SQLite veritabanı (Performans skorları ve hafıza)
├── Dockerfile            # Bulut dağıtım konteyner ayarları
├── requirements.txt      # Bağımlılık listesi
└── README.md             # Proje dokümantasyonu
```

---

## 📝 Lisans
Bu proje açık kaynaklı olup kişisel gelişim takibi amacıyla geliştirilmiştir.
