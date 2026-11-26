import sys
import sqlite3

def main():
    if len(sys.argv) != 2:
        print("Использование: python tools\\show_vault.py <хранилище.sqlite>")
        sys.exit(1)

    path = sys.argv[1]
    db = sqlite3.connect(path)
    fp = db.execute("SELECT v FROM meta WHERE k = 'fingerprint'").fetchone()[0]
    w_cnt = db.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    n_cnt = db.execute("SELECT COUNT(*) FROM numbers").fetchone()[0]

    print(f"Хранилище: {path}")
    print(f"Отпечаток секрета: {fp}")
    print(f"Имён собственных: {w_cnt}")
    print(f"Номеров: {n_cnt}")
    print("Журнал выгрузок (когда, какой файл, сколько НОВЫХ имён и номеров):")
    for row in db.execute("SELECT id, ts, filename, new_words, new_numbers FROM runs ORDER BY id"):
        print(f"  {row[0]}. {row[1]}  {row[2]:<30} имён +{row[3]}, номеров +{row[4]}")

    print("Пример записей (ключ — хеш настоящего значения, его не расшифровать):")
    for k, idx in db.execute("SELECT key, idx FROM words LIMIT 2"):
        print(f"  слово  {k[:24]}…  -> прилагательное № {idx}")
    for k, new_n in db.execute("SELECT key, new FROM numbers LIMIT 1"):
        print(f"  номер  {k[:24]}…  -> {new_n}")

if __name__ == "__main__":
    main()
