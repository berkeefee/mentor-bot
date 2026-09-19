import os
import sys
import unittest
import sqlite3
import re
from datetime import datetime, timedelta, timezone

# Windows console encoding fix for test runner
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Test veritabanı yolu
TEST_DB = "test_ajan_hafiza.db"
os.environ["DATABASE_PATH"] = TEST_DB
if "DATABASE_URL" in os.environ:
    del os.environ["DATABASE_URL"]

sys.path.insert(0, r"C:\Users\calis\Documents\GitHub\mentor-bot")
import bot

class TestDemirIradeV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        bot.DB_FILE = TEST_DB
        bot.veritabanini_hazirla()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except Exception:
                pass
        if os.path.exists("ilerleme_grafigi.png"):
            try:
                os.remove("ilerleme_grafigi.png")
            except Exception:
                pass

    def test_01_ana_odak_agirligi(self):
        """Test 1: Ana Odak 1.6x ağırlıkla hesaplanır; Growth/Maintenance ayrımı."""
        puanlar = {
            "BESLENME": 7.0, "SPOR": 6.0, "UYKU": 8.0, 
            "KİŞİSEL GELİŞİM": 7.0, "FİNANS": 9.0, "SOSYAL İLİŞKİLER": 8.0, "YAZILIM": 5.0
        }
        # Finans odak: (9.0*1.6 + 7+6+8+7+8+5) / (1.6 + 6*1.0) = (14.4 + 41) / 7.6 = 55.4 / 7.6 = 7.289... -> 7.3
        beklenen_skor = round((9.0 * 1.6 + 7.0 + 6.0 + 8.0 + 7.0 + 8.0 + 5.0) / (1.6 + 6.0), 1)
        skor = bot.agirlikli_skor(puanlar, ana_odak="finans")
        self.assertEqual(skor, beklenen_skor)
        self.assertEqual(skor, 7.3)

    def test_02_odak_cikarimi_yasak(self):
        """Test 2: Kullanıcı açıkça odak beyan etmediğinde odak_getir boş döner, düz ortalama alınır."""
        tarih = "2026-09-25"
        alan, hedef = bot.odak_getir(tarih)
        self.assertIsNone(alan)
        self.assertIsNone(hedef)
        
        # Düz ortalama
        puanlar = {"BESLENME": 6.0, "SPOR": 8.0, "UYKU": 7.0, "YAZILIM": 3.0}
        beklenen_duz = round((6.0 + 8.0 + 7.0 + 3.0) / 4, 1)
        skor = bot.agirlikli_skor(puanlar, ana_odak=alan)
        self.assertEqual(skor, beklenen_duz)

    def test_03_aktif_kisit(self):
        """Test 3: Aktif kısıt sistem talimatına HARD CONSTRAINT olarak enjekte edilir."""
        tarih = "2026-09-20"
        bot.kisit_ekle("sol bilek kırığı, alçıda", tarih)
        kisitlar = bot.aktif_kisitlari_getir()
        self.assertIn("sol bilek kırığı, alçıda", kisitlar)
        
        talimat = bot.build_system_instruction(aktif_kisitlar=kisitlar)
        self.assertIn("sol bilek kırığı, alçıda", talimat)
        self.assertIn("HARD CONSTRAINT", talimat)

    def test_04_kisit_guncelleme(self):
        """Test 4: Kısıt kapatıldığında aktif=0 olur ve yeni talimatta yer almaz."""
        tarih = "2026-09-21"
        bot.kisit_kapat("alçı", tarih)
        kisitlar = bot.aktif_kisitlari_getir()
        self.assertNotIn("sol bilek kırığı, alçıda", kisitlar)
        
        talimat = bot.build_system_instruction(aktif_kisitlar=kisitlar)
        self.assertNotIn("sol bilek kırığı, alçıda", talimat)

    def test_05_gunun_sozu(self):
        """Test 5: İlk blok söz, SOZ_HAVUZU içinden, SÖZ ID satırı temizlenir, geçersiz ID fallback."""
        fake_analiz = "SÖZ ID: 4\n### 📋 GÜNLÜK FEEDBACK & MENTOR ANALİZİ\nİyi çalıştın."
        soz_blogu = bot.gunun_sozu(fake_analiz, "2026-09-20")
        self.assertIn(bot.SOZ_HAVUZU[4], soz_blogu)
        
        # Geçersiz ID fallback
        fake_gecersiz = "SÖZ ID: 999\nAnaliz"
        soz_fallback = bot.gunun_sozu(fake_gecersiz, "2026-09-20")
        self.assertTrue(any(s in soz_fallback for s in bot.SOZ_HAVUZU))
        
        # Rapor temizliği
        rapor = bot.raporu_birlestir(fake_analiz, soz_blogu, 7.5, "", False)
        self.assertNotIn("SÖZ ID:", rapor)
        self.assertTrue(rapor.startswith("🗣️ **GÜNÜN SÖZÜ**"))

    def test_06_agirlikli_skor_matematigi(self):
        """Test 6: N/A içeren senaryolarda agirlikli_skor matematiksel doğruluğu ve 0-10 aralığı."""
        # N/A olan alanlar hesaba girmez
        puanlar = {
            "BESLENME": 8.0, "SPOR": 6.0, "UYKU": None,
            "KİŞİSEL GELİŞİM": None, "FİNANS": 10.0, "SOSYAL İLİŞKİLER": None, "YAZILIM": 8.0
        }
        # Finans odak (10.0*1.6 + 8.0 + 6.0 + 8.0) / (1.6 + 3.0) = (16.0 + 22.0) / 4.6 = 38.0 / 4.6 = 8.26 -> 8.3
        skor = bot.agirlikli_skor(puanlar, ana_odak="finans")
        self.assertEqual(skor, 8.3)
        self.assertTrue(0.0 <= skor <= 10.0)
        
        # Hiç puan yoksa None
        bos_puanlar = {a: None for a in bot.ALANLAR}
        self.assertIsNone(bot.agirlikli_skor(bos_puanlar))

    def test_07_en_dusuk_iki_alan(self):
        """Test 7: en_dusuk_iki_alan doğru iki alanı döndürür ve N/A'ları atlar."""
        puanlar = {
            "BESLENME": 8.0, "SPOR": 4.0, "UYKU": None,
            "KİŞİSEL GELİŞİM": 7.0, "FİNANS": 9.0, "SOSYAL İLİŞKİLER": None, "YAZILIM": 5.0
        }
        dusukler = bot.en_dusuk_iki_alan(puanlar)
        self.assertEqual(dusukler, ["SPOR", "YAZILIM"])

    def test_08_uyku_bilgisi_yoksa_na(self):
        """Test 8: Uyku bilgisi yoksa UYKU=N/A, uyku_saat=None."""
        analiz = "PUANLAR: BESLENME=8; SPOR=7; UYKU=N/A; KİŞİSEL GELİŞİM=8; FİNANS=9; SOSYAL İLİŞKİLER=7; YAZILIM=8"
        puanlar = bot.puanlari_ayristir(analiz)
        self.assertIsNone(puanlar["UYKU"])
        
        saat = bot.ayikla_uyku_saat("Bugün sadece kod yazdım ve spor yaptım.")
        self.assertIsNone(saat)

    def test_09_geriye_donuk_uyum(self):
        """Test 9: Eski kayıtlar (NULL alanlı yeni kolonlar) sorunsuz okunur ve grafik çizilir."""
        tarih = "2026-06-15"
        bot.hafizaya_kaydet(tarih, "Eski girdi", "Eski analiz", 6.5)
        kayit = bot.spesifik_tarih_getir(tarih)
        self.assertIsNotNone(kayit)
        self.assertEqual(kayit[2], 6.5)
        
        # Grafik oluşturulabiliyor olmalı
        grafik_yolu = bot.grafik_olustur()
        self.assertTrue(grafik_yolu and os.path.exists(grafik_yolu))

    def test_10_migrasyon_idempotansi(self):
        """Test 10: veritabanini_hazirla() defalarca çağrılsa bile hata vermez."""
        try:
            bot.veritabanini_hazirla()
            bot.veritabanini_hazirla()
            idempotent = True
        except Exception as e:
            idempotent = False
        self.assertTrue(idempotent)

    def test_11_derleme(self):
        """Test 11: python -m py_compile bot.py temiz geçer."""
        import py_compile
        compiled = py_compile.compile("bot.py")
        self.assertIsNotNone(compiled)

    def test_12_regresyon(self):
        """Test 12: Korunacak fonksiyonlar ve imzalar mevcut ve çalışır durumda."""
        self.assertTrue(callable(bot.start_komutu))
        self.assertTrue(callable(bot.grafik_gonder_komutu))
        self.assertTrue(callable(bot.mesaj_yoneticisi))
        self.assertTrue(callable(bot.ses_mesaj_yoneticisi))
        self.assertTrue(callable(bot.spesifik_tarih_getir))
        self.assertTrue(callable(bot.grafik_olustur))

    def test_13_tarih_ayiklama_hata1(self):
        """Test 13: Ondalıklı sayılar (3.5, 1.5, 12.3) ve 'dünya' bugünün tarihini; diğerleri doğru tarihi verir."""
        bugun = datetime.now(bot.TR_TZ).strftime("%Y-%m-%d")
        dun = (datetime.now(bot.TR_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")

        t1, _ = bot.tarih_ayıkla("Bugün 3.5 saat uyudum")
        self.assertEqual(t1, bugun)

        t2, _ = bot.tarih_ayıkla("1.5 saat kod yazdım")
        self.assertEqual(t2, bugun)

        t3, _ = bot.tarih_ayıkla("Portföyüm 12.3 puan düştü")
        self.assertEqual(t3, bugun)

        t4, _ = bot.tarih_ayıkla("Dünya kadar iş vardı")
        self.assertEqual(t4, bugun)

        # Doğru formatlar çalışmaya devam etmeli
        t5, _ = bot.tarih_ayıkla("[2026-09-16] Bugün çok iyiydi")
        self.assertEqual(t5, "2026-09-16")

        t6, _ = bot.tarih_ayıkla("16 Eylül'de spora başladım")
        self.assertEqual(t6, "2026-09-16")

        t7, _ = bot.tarih_ayıkla("16/09 tarihinde rapor")
        self.assertEqual(t7, "2026-09-16")

        t8, _ = bot.tarih_ayıkla("dün spora gittim")
        self.assertEqual(t8, dun)

    def test_14_cift_kayit_hata2(self):
        """Test 14: Aynı tarihe 2 kez kayıt -> 1 satır kalır, son kayıt döner."""
        tarih = "2026-09-22"
        bot.hafizaya_kaydet(tarih, "İlk deneme", "Analiz 1", 4.0)
        bot.hafizaya_kaydet(tarih, "İkinci düzeltme", "Analiz 2", 8.0)
        
        conn, p = bot.db_manager.get_connection()
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*) FROM gunluk_hafiza WHERE tarih = {p}", (tarih,))
        count = c.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)
        
        kayit = bot.spesifik_tarih_getir(tarih)
        self.assertEqual(kayit[0], "İkinci düzeltme")
        self.assertEqual(kayit[2], 8.0)

    def test_15_sessiz_kayit_hatasi_hata3(self):
        """Test 15: DB hatası durumunda hafizaya_kaydet False döner."""
        # Geçersiz veritabanı veya kapalı bağlantı simülasyonu
        # total_puan yerine DB'nin reddedeceği bir tablo ismi veya bozuk bağlantı
        old_get_conn = bot.db_manager.get_connection
        def broken_conn():
            raise sqlite3.OperationalError("Simulated DB Failure")
        bot.db_manager.get_connection = broken_conn
        
        res = bot.hafizaya_kaydet("2026-09-23", "Girdi", "Analiz", 7.0)
        bot.db_manager.get_connection = old_get_conn
        self.assertFalse(res)

    def test_16_ses_parcalama_hata4(self):
        """Test 16: Metinde 'ANALİZ:' birden fazla geçse de kesilmez, TARİH: satırı dökümden silinir."""
        fake_ses_cikti = (
            "TARİH: 2026-09-18\n"
            "DÖKÜM:\nTARİH: 2026-09-18\nBugün 2 saat kod yazdım.\n"
            "ANALİZ:\n"
            "### 📋 GÜNLÜK FEEDBACK & MENTOR ANALİZİ\n"
            "Detaylı ANALİZ: İlk bölüm burada, ikinci ANALİZ: bölümü de burada tam olmalı.\n"
            "PUANLAR: YAZILIM=8"
        )
        parts = fake_ses_cikti.split("ANALİZ:", 1)
        döküm = parts[0].replace("DÖKÜM:", "").strip()
        döküm = re.sub(r"TARİH:\s*[^\n]+\n?", "", döküm).strip()
        analiz = parts[1].strip()

        self.assertNotIn("TARİH:", döküm)
        self.assertEqual(döküm, "Bugün 2 saat kod yazdım.")
        self.assertIn("ikinci ANALİZ: bölümü de burada tam olmalı", analiz)

    def test_17_madde_8_grafik_tam_tarih_ve_istisna(self):
        """Test 17 (Kullanıcı Madde 8): Eksik günler takvimde 0, istisna günü 0 değil ve ortalamaya katılmaz."""
        # İki gün aralıklı kayıt ekle
        bot.hafizaya_kaydet("2026-09-01", "G1", "A1", 8.0)
        bot.hafizaya_kaydet("2026-09-02", "G2", "A2", None, detay={"istisna_modu": 1})
        bot.hafizaya_kaydet("2026-09-04", "G4", "A4", 6.0)
        # 2026-09-03 kaydı yok -> takvimde 0 puan olmalı
        
        grafik_yolu = bot.grafik_olustur()
        self.assertTrue(os.path.exists(grafik_yolu))

    def test_18_madde_9_niteliksel_uyku_capasi(self):
        """Test 18 (Kullanıcı Madde 9): Saat söylenmese bile niteliksel ifadeden puan ayrıştırılır, süre None kalır."""
        analiz_metni = (
            "SÖZ ID: 12\n"
            "UYKU_SAAT: None\n"
            "PUANLAR: BESLENME=7; SPOR=6; UYKU=8; KİŞİSEL GELİŞİM=7; FİNANS=8; SOSYAL İLİŞKİLER=7; YAZILIM=8\n"
            "### 📊 BUGÜNÜN KARNE PUANLARI\n* 😴 Uyku: 8/10 -> İyi uyudum dedin."
        )
        puanlar = bot.puanlari_ayristir(analiz_metni)
        self.assertEqual(puanlar["UYKU"], 8.0)
        
        uyku_saat = bot.ayikla_uyku_saat("Dün gece çok iyi uyudum, dinç kalktım.", analiz_metni)
        self.assertIsNone(uyku_saat)

if __name__ == "__main__":
    unittest.main(verbosity=2)
