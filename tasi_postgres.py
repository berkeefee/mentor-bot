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
    cur_sq.execute("""
        SELECT tarih, girdi, analiz, total_puan, uyku_saat, ana_odak, odak_hedef, puanlar_json, istisna_modu 
        FROM gunluk_hafiza ORDER BY id ASC
    """)
    rows = cur_sq.fetchall()

    cur_sq.execute("SELECT tarih, alan, hedef FROM gun_odak")
    odak_rows = cur_sq.fetchall()

    try:
        cur_sq.execute("SELECT kisit, baslangic_tarih, bitis_tarih, aktif FROM aktif_kisitlar")
        kisit_rows = cur_sq.fetchall()
    except Exception:
        kisit_rows = []

    conn_sq.close()

    print("==========================================================")
    print(f"[BILGI] YEREL SQLITE VERITABANI ({sqlite_path}): {len(rows)} ADET RAPOR BULUNDU")
    print(f"[BILGI] {len(odak_rows)} odak kaydı, {len(kisit_rows)} aktif kısıt kaydı bulundu.")
    print("==========================================================")
    for i, r in enumerate(rows[-5:], 1):
        girdi_str = (r[1] or "")[:40].replace("\n", " ")
        print(f"  [{i}] Tarih: {r[0]} | Puan: {r[3]} | Girdi: {girdi_str}...")
    print("----------------------------------------------------------")

    if not do_write:
        print("\n[DRY-RUN MODU - KONTROL]: '--yaz' parametresi verilmedigi icin veriler PostgreSQL'e HENUZ YAZILMADI.")
        print("[IPUCU] Gercek tasima icin komutu soyle calistirin:")
        print("   python tasi_postgres.py \"postgresql://...\" --yaz")
        return

    if not db_url or not db_url.startswith("postgres"):
        print("[Hata]: DATABASE_URL veya postgresql:// baglanti adresi bulunamadi!")
        print("Kullanim: python tasi_postgres.py \"postgresql://user:pass@host/dbname?sslmode=require\" --yaz")
        return

    try:
        import psycopg2
        conn_pg = psycopg2.connect(db_url)
        cur_pg = conn_pg.cursor()

        # Tabloları oluştur
        cur_pg.execute("""
            CREATE TABLE IF NOT EXISTS gunluk_hafiza (
                id SERIAL PRIMARY KEY,
                tarih VARCHAR(50),
                girdi TEXT,
                analiz TEXT,
                total_puan REAL,
                uyku_saat REAL,
                ana_odak TEXT,
                odak_hedef TEXT,
                puanlar_json TEXT,
                istisna_modu INTEGER
            )
        """)
        cur_pg.execute("""
            CREATE TABLE IF NOT EXISTS gun_odak (
                tarih VARCHAR(50) PRIMARY KEY,
                alan TEXT,
                hedef TEXT
            )
        """)
        cur_pg.execute("""
            CREATE TABLE IF NOT EXISTS aktif_kisitlar (
                id SERIAL PRIMARY KEY,
                kisit TEXT,
                baslangic_tarih VARCHAR(50),
                bitis_tarih VARCHAR(50),
                aktif INTEGER
            )
        """)
        conn_pg.commit()

        # Raporları aktar (Varsa güncelle, yoksa ekle)
        inserted = 0
        for r in rows:
            cur_pg.execute("DELETE FROM gunluk_hafiza WHERE tarih = %s", (r[0],))
            cur_pg.execute("""
                INSERT INTO gunluk_hafiza 
                (tarih, girdi, analiz, total_puan, uyku_saat, ana_odak, odak_hedef, puanlar_json, istisna_modu) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]))
            inserted += 1

        for o in odak_rows:
            cur_pg.execute("DELETE FROM gun_odak WHERE tarih = %s", (o[0],))
            cur_pg.execute("INSERT INTO gun_odak (tarih, alan, hedef) VALUES (%s, %s, %s)", (o[0], o[1], o[2]))

        for k in kisit_rows:
            cur_pg.execute("""
                INSERT INTO aktif_kisitlar (kisit, baslangic_tarih, bitis_tarih, aktif) 
                VALUES (%s, %s, %s, %s)
            """, (k[0], k[1], k[2], k[3]))

        conn_pg.commit()
        conn_pg.close()
        print(f"[BASARILI]: {inserted} adet günlük rapor, odak ve kısıt verileri PostgreSQL veritabanına aktarıldı!")
    except Exception as e:
        print(f"[HATA]: PostgreSQL'e bağlanırken veya yazarken hata oluştu: {e}")

if __name__ == "__main__":
    migrate()
