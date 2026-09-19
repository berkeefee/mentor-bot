import sqlite3
import sys
import os

def migrate():
    args = sys.argv[1:]
    do_write = "--yaz" in args
    
    db_url = os.environ.get("DATABASE_URL")
    for arg in args:
        if arg.startswith("postgres"):
            db_url = arg

    sqlite_path = "ajan_hafiza.db"
    if not os.path.exists(sqlite_path):
        print(f"[Hata]: Yerel veritabanı {sqlite_path} bulunamadı!")
        return

    # SQLite oku
    conn_sq = sqlite3.connect(sqlite_path)
    cur_sq = conn_sq.cursor()
    cur_sq.execute("SELECT tarih, girdi, analiz, total_puan FROM gunluk_hafiza ORDER BY id ASC")
    rows = cur_sq.fetchall()
    conn_sq.close()

    print("==========================================================")
    print(f"[BILGI] YEREL SQLITE VERITABANI ({sqlite_path}): {len(rows)} ADET KAYIT BULUNDU")
    print("==========================================================")
    for i, r in enumerate(rows[:5], 1):
        girdi_str = (r[1] or "")[:40].replace("\n", " ")
        print(f"  [{i}] Tarih: {r[0]} | Puan: {r[3]} | Girdi: {girdi_str}...")
    if len(rows) > 5:
        print(f"  ... ve {len(rows)-5} kayit daha.")
    print("----------------------------------------------------------")

    if not do_write:
        print("\n[DRY-RUN MODU - KONTROL]: '--yaz' parametresi verilmedigi icin veriler PostgreSQL'e HENUZ YAZILMADI.")
        print("[IPUCU] Ciktiyi kontrol ettikten sonra gercek tasima icin komutu soyle calistirin:")
        print("   python tasi_postgres.py --yaz")
        return

    if not db_url or not db_url.startswith("postgres"):
        print("[Hata]: DATABASE_URL veya postgresql:// baglanti adresi bulunamadi!")
        print("Kullanim: python tasi_postgres.py --yaz  (DATABASE_URL cevresel degiskeni tanimliysa)")
        print("veya:     python tasi_postgres.py \"postgresql://user:pass@host:5432/dbname\" --yaz")
        return

    try:
        import psycopg2
        conn_pg = psycopg2.connect(db_url)
        cur_pg = conn_pg.cursor()

        # Tablo oluştur
        cur_pg.execute("""
            CREATE TABLE IF NOT EXISTS gunluk_hafiza (
                id SERIAL PRIMARY KEY,
                tarih VARCHAR(50),
                girdi TEXT,
                analiz TEXT,
                total_puan REAL
            )
        """)

        # Kayıtları aktar
        inserted = 0
        for r in rows:
            cur_pg.execute("SELECT id FROM gunluk_hafiza WHERE tarih = %s AND girdi = %s", (r[0], r[1]))
            if not cur_pg.fetchone():
                cur_pg.execute(
                    "INSERT INTO gunluk_hafiza (tarih, girdi, analiz, total_puan) VALUES (%s, %s, %s, %s)",
                    (r[0], r[1], r[2], r[3])
                )
                inserted += 1

        conn_pg.commit()
        conn_pg.close()
        print(f"[BASARILI]: {inserted} adet kayit PostgreSQL (Supabase/Neon) veritabanina aktarildi!")
    except Exception as e:
        print(f"[HATA]: PostgreSQL'e baglanirken/yazarken hata olustu: {e}")

if __name__ == "__main__":
    migrate()
