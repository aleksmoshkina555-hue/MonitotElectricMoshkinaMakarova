import io
import os
import re
import sys
import json
import time
import base64
import zipfile
import hashlib
import sqlite3
from lxml import etree as ET
from collections import defaultdict

# папка, где лежит сам скрипт, чтобы относительные пути к словарям не отваливались
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# таблица транслитерации, нужна для генерации email и режима en
CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"
}


def translit(text: str) -> str:
    chars = []
    for ch in text:
        low = ch.lower()
        if low in CYR_TO_LAT:
            mapped = CYR_TO_LAT[low]
            # сохраняем регистр первой буквы, если слово было с заглавной
            chars.append(mapped.capitalize() if ch.isupper() else mapped)
        else:
            chars.append(ch)
    return "".join(chars)


def load_tsv(rel_path: str) -> list:
    # читаем словари из папки dict, пропускаем пустые строки и комменты
    full_path = os.path.join(SCRIPT_DIR, rel_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"Не найден файл справочника: {full_path}")
    items = []
    with open(full_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                cols = [p.strip() for p in line.split("\t") if p.strip()]
                items.append(cols if len(cols) > 1 else cols[0])
    return items


# подгружаем словари в память один раз при старте
FRUITS_LIST = load_tsv(os.path.join("dict", "fruits.tsv"))
MALE_NAMES = load_tsv(os.path.join("dict", "names.tsv"))
FEMALE_NAMES = load_tsv(os.path.join("dict", "female_names.tsv"))

# глобальные настройки и кэши для ускорения обработки больших xml
SECRET_KEY = ""
OUTPUT_LANG = "ru"
DB_VAULT = None

UUID_CACHE = {}
NUM_CACHE = {}
TAG_CACHE = {}
ATTR_CACHE = {}
PERSON_CACHE = {}
ID_FIELD_CACHE = {}
FRUIT_ADJECTIVES = []


def compute_hash(text: str) -> int:
    # детерминированный хеш от секрета + строки, чтобы при повторном запуске замены совпадали
    digest = hashlib.sha256((SECRET_KEY + "|" + text).encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


def pick(items: list, seed: str):
    return items[compute_hash(seed) % len(items)]


def vault_key(kind: str, value: str) -> str:
    # в базе храним не сами исходные слова, а их sha256-хеши с солью (безопасность)
    return hashlib.sha256((SECRET_KEY + "|vault|" + kind + "|" + value).encode("utf-8")).hexdigest()


class Vault:
    """Локальная база SQLite для запоминания соответствий между разными выгрузками."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        # включаем WAL для быстрой записи
        self.conn.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;
            PRAGMA temp_store = MEMORY;
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS words (key TEXT PRIMARY KEY, idx INTEGER);
            CREATE TABLE IF NOT EXISTS numbers (key TEXT PRIMARY KEY, new TEXT);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                filename TEXT,
                new_words INTEGER,
                new_numbers INTEGER
            );
        """)
        # проверяем отпечаток секрета, чтобы не смешать данные от разных паролей
        fp = vault_key("fingerprint", "")[:16]
        row = self.conn.execute("SELECT v FROM meta WHERE k = 'fingerprint'").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fp,))
            self.conn.commit()
        elif row[0] != fp:
            self.close()
            raise SystemExit("ОШИБКА: хранилище создано с другим секретным словом. Укажите тот же секрет или новое хранилище.")

        # выгружаем всё в словари в ОЗУ, чтобы не дергать базу на каждый тег
        self.words = dict(self.conn.execute("SELECT key, idx FROM words"))
        self.numbers = dict(self.conn.execute("SELECT key, new FROM numbers"))
        self.new_words = []
        self.new_numbers = []

    def save(self, filename: str):
        # сохраняем новые замены только в самом конце, если статистика модели не сломалась
        if self.new_words:
            self.conn.executemany("INSERT OR IGNORE INTO words VALUES (?, ?)", self.new_words)
        if self.new_numbers:
            self.conn.executemany("INSERT OR IGNORE INTO numbers VALUES (?, ?)", self.new_numbers)
        self.conn.execute(
            "INSERT INTO runs (ts, filename, new_words, new_numbers) VALUES (?, ?, ?, ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), filename, len(self.new_words), len(self.new_numbers))
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                self.conn.close()
            except Exception:
                pass
            self.conn = None


# классы географии и измерений — их значения не трогаем, иначе сломается физика/топология модели
REGION_CLASSES = {"Country", "GeographicalRegion", "SubGeographicalRegion"}

TECHNICAL_CLASSES = {
    "DiagramObjectPoint", "PositionPoint", "CoordinateSystem",
    "AnalogValue", "DiscreteValue", "AccumulatorValue",
    "BaseVoltage", "UnitMultiplier", "UnitSymbol", "PhaseCode"
}

# поля, где точно лежат идентификаторы, номера договоров, паспортов и т.д.
ID_FIELDS = {
    "IdentifiedObject.mRID", "IdentifiedObject.aliasName",
    "Asset.serialNumber", "Asset.lotNumber", "Asset.utcNumber",
    "Meter.serialNumber", "Organisation.taxCode", "Organisation.inn",
    "Organisation.kpp", "Organisation.ogrn", "CustomerAccount.accountNumber",
    "Agreement.signDate", "Document.docStatus", "Person.personnelNumber",
    "Person.passportNumber", "TelephoneNumber.localNumber",
    "TelephoneNumber.areaCode", "StreetDetail.number"
}

ID_FIELD_HINTS = (
    "serial", "number", "inn", "kpp", "ogrn", "account",
    "code", "passport", "mrid", "alias", "phone", "zip", "postal"
)

NAME_FIELDS = {"IdentifiedObject.name"}
PERSON_FIELDS = {"Person.firstName", "Person.lastName", "Person.mName"}

# технические сокращения и единицы измерения, которые нельзя заменять на фрукты
STOP_WORDS = {
    "Sub", "OHL", "OHCL", "Line", "Plant", "Substation", "LLC", "UK", "DOM",
    "Street", "St", "Ave", "kV", "kW", "MW", "MVA", "kVA", "Hz", "A", "V",
    "pol", "meter", "UP", "abs", "Scheme", "CIM", "RDF", "XML", "HTTP", "HTTPS",
    "ПС", "ВЛ", "КЛ", "КВЛ", "ТП", "РП", "КТП", "БКТП", "ЗТП", "ГРЭС", "ТЭЦ", "ГЭС",
    "кВ", "кВт", "МВт", "МВА", "кВА", "Гц", "ООО", "ПАО", "АО", "ЗАО", "ОАО", "ИП",
    "ул", "пр", "пер", "д", "корп", "стр", "кв", "обл", "р-н", "г", "с", "пос"
}

# регулярки для поиска uuid, почты, телефонов и отдельных слов
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?:\+7|8|7)[\s\-()]*(?:\d[\s\-()]*){10}")
WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
B64_PATTERN = re.compile(r"^[A-Za-z0-9+/=\s]+$")


def is_id_field(field: str) -> bool:
    cached = ID_FIELD_CACHE.get(field)
    if cached is not None:
        return cached
    if field in ID_FIELDS:
        res = True
    else:
        low = field.lower()
        res = any(h in low for h in ID_FIELD_HINTS)
    ID_FIELD_CACHE[field] = res
    return res


def is_pure_float(s: str) -> bool:
    # проверяем, является ли строка просто числом (например, напряжением или координатой)
    try:
        float(s.replace(",", "."))
        return True
    except ValueError:
        return False


def format_lang(text: str) -> str:
    return translit(text) if OUTPUT_LANG == "en" else text


def to_adjective(noun: str) -> str:
    # костыльный генератор прилагательных из названий фруктов (Яблоко -> Яблочная и т.д.)
    word = noun.strip()
    low = word.lower()
    if any("а" <= c <= "я" or c == "ё" for c in low):
        if low.endswith("око"):
            res = word[:-2] + "чная"
        elif low.endswith("ша"):
            res = word[:-1] + "евая"
        elif low.endswith("ня"):
            res = word[:-1] + "ная"
        elif low.endswith("ва"):
            res = word[:-1] + "овая"
        elif low.endswith("ка"):
            res = word[:-2] + "ная"
        elif low.endswith("та"):
            res = word[:-1] + "ная"
        elif low.endswith("а"):
            res = word[:-1] + "овая"
        elif low.endswith("о"):
            res = word + "вая"
        elif low.endswith("ь"):
            res = word[:-1] + "евая"
        elif low.endswith(("ж", "ш", "щ", "ч", "ц")):
            res = word + "евая"
        else:
            res = word + "овая"
        return format_lang(res.capitalize())
    res = (word[:-1] if low.endswith(("e", "a", "o")) else word) + "ovaya"
    return res.capitalize()


def make_person_profile(seed: str, female: bool = False) -> dict:
    # собираем полный фейковый профиль человека (ФИО, почта, улица) из одного сида
    cache_key = (seed, female, OUTPUT_LANG)
    cached = PERSON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    names_pool = FEMALE_NAMES if female else MALE_NAMES
    entry = pick(names_pool, "person_base:" + seed)
    base_str = entry[0] if isinstance(entry, list) else entry

    parts = base_str.split()
    if len(parts) >= 3:
        last_name, first_name, middle_name = parts[0], parts[1], parts[2]
    else:
        # если в словаре только имя — догенерируем фамилию и отчество из другого случайного имени
        first_name = base_str
        pat_entry = pick(MALE_NAMES, "patronymic:" + seed)
        pat_base = (pat_entry[0] if isinstance(pat_entry, list) else pat_entry).split()[0]

        if any("а" <= c <= "я" or c == "ё" for c in pat_base.lower()):
            stem = pat_base[:-1] if pat_base.lower().endswith(("а", "я", "й", "ь")) else pat_base
            last_name = stem + ("ова" if female else "ов")
            middle_name = stem + ("овна" if female else "ович")
        else:
            last_name = pat_base + ("ova" if female else "ov")
            middle_name = pat_base + ("ovna" if female else "ovich")

    tr_first = translit(first_name).lower()
    tr_last = translit(last_name).lower()
    street_stem = last_name[:-1] if last_name.lower().endswith(("а", "я")) else last_name

    profile = {
        "fio": format_lang(f"{last_name} {first_name} {middle_name}"),
        "first": format_lang(first_name),
        "last": format_lang(last_name),
        "middle": format_lang(middle_name),
        "email": f"{tr_first[:1]}.{tr_last}@example.ru",
        "street": format_lang(f"{street_stem}ская")
    }
    PERSON_CACHE[cache_key] = profile
    return profile


def new_uuid(old_uuid: str) -> str:
    # генерируем валидный по формату UUIDv4 на основе старого
    low = old_uuid.lower()
    cached = UUID_CACHE.get(low)
    if cached is not None:
        return cached
    hx = hashlib.sha256((SECRET_KEY + "|uuid|" + low).encode("utf-8")).hexdigest()
    res = f"{hx[:8]}-{hx[8:12]}-4{hx[13:16]}-a{hx[17:20]}-{hx[20:32]}"
    UUID_CACHE[low] = res
    return res


def new_number(old_num: str, used_numbers: set) -> str:
    # подбираем новое число ровно такой же длины, что и исходное
    cached = NUM_CACHE.get(old_num)
    if cached is not None:
        return cached
    key = vault_key("num", old_num)
    if DB_VAULT and key in DB_VAULT.numbers:
        res = DB_VAULT.numbers[key]
        NUM_CACHE[old_num] = res
        return res

    length = len(old_num)
    mod = 10 ** length
    cand = old_num
    # крутим соль, пока не найдем уникальное число без ведущего нуля (если его не было)
    for salt in range(128):
        val = compute_hash(f"num:{old_num}:{salt}") % mod
        cand = str(val).zfill(length)
        if length > 1 and cand[0] == "0" and old_num[0] != "0":
            cand = str((val % 9) + 1) + cand[1:]
        if cand != old_num and (length <= 3 or cand not in used_numbers):
            break

    if length > 3:
        used_numbers.add(cand)
    NUM_CACHE[old_num] = cand
    if DB_VAULT:
        DB_VAULT.numbers[key] = cand
        DB_VAULT.new_numbers.append((key, cand))
    return cand


def index_to_fruit(idx: int) -> str:
    # переводим порядковый индекс в название фрукта (если фруктов не хватило — склеиваем через дефис)
    total = len(FRUIT_ADJECTIVES)
    parts = []
    while True:
        parts.append(FRUIT_ADJECTIVES[idx % total])
        idx = (idx // total) - 1
        if idx < 0:
            break
    parts.reverse()
    return "-".join(parts)


def build_word_map(words: set) -> dict:
    # назначаем каждому уникальному слову свой индекс в базе
    ordered = sorted(words, key=lambda w: (compute_hash("word:" + w), w))
    used_idx = set(DB_VAULT.words.values()) if DB_VAULT else set()
    next_idx = 0
    mapping = {}
    for word in ordered:
        key = vault_key("word", word)
        if DB_VAULT and key in DB_VAULT.words:
            idx = DB_VAULT.words[key]
        else:
            while next_idx in used_idx:
                next_idx += 1
            idx = next_idx
            used_idx.add(idx)
            if DB_VAULT:
                DB_VAULT.words[key] = idx
                DB_VAULT.new_words.append((key, idx))
        mapping[word] = index_to_fruit(idx)
    return mapping


def build_street_map(streets: set) -> dict:
    return {st: make_person_profile("street:" + st)["street"] for st in sorted(streets)}


def process_text(text: str, word_map: dict, street_map: dict, used_numbers: set, counters: dict, allow_pure_number: bool = False) -> str:
    if not text:
        return text
    stripped = text.strip()
    # голые цифры меняем только если это ID/серийник, а не физический параметр
    if stripped.isdigit():
        if allow_pure_number:
            counters["numbers"].add(stripped)
            return new_number(stripped, used_numbers)
        return text
    if is_pure_float(stripped):
        return text

    # по очереди вычищаем UUID, почту и телефоны внутри произвольного текста
    if len(text) >= 36 and "-" in text:
        text = UUID_PATTERN.sub(lambda m: new_uuid(m.group(0)), text)
    if "@" in text:
        def repl_email(m):
            counters["persons"] += 1
            return make_person_profile(m.group(0))["email"]
        text = EMAIL_PATTERN.sub(repl_email, text)
    if len(text) >= 11 and any(c.isdigit() for c in text):
        def repl_phone(m):
            digits = "".join(c for c in m.group(0) if c.isdigit())
            if len(digits) == 11:
                nb = new_number(digits[1:], used_numbers)
                return f"+7 ({nb[:3]}) {nb[3:6]}-{nb[6:8]}-{nb[8:]}"
            return new_number(digits, used_numbers)
        text = PHONE_PATTERN.sub(repl_phone, text)

    # заменяем оставшиеся слова (названия подстанций, улиц) и длинные числа по словарям
    def repl_tok(match):
        tok = match.group(0)
        w_rep = word_map.get(tok)
        if w_rep is not None:
            return w_rep
        s_rep = street_map.get(tok)
        if s_rep is not None:
            return s_rep
        if tok.isdigit() and len(tok) >= 3:
            counters["numbers"].add(tok)
            return new_number(tok, used_numbers)
        return tok

    return WORD_PATTERN.sub(repl_tok, text)


def decode_base64(val: str):
    # пытаемся понять, не зашит ли внутри тега base64-документ или архив
    if len(val) < 64 or not B64_PATTERN.fullmatch(val):
        return None
    try:
        clean = "".join(val.split())
        if len(clean) < 64 or len(clean) % 4 != 0:
            return None
        return base64.b64decode(clean, validate=True)
    except Exception:
        return None


def base64_texts(raw: bytes) -> list:
    # если внутри base64 лежит zip-архив (начинается с PK), достаем из него все текстовые файлы
    if raw.startswith(b"PK\x03\x04"):
        texts = []
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            for name in zf.namelist():
                try:
                    texts.append(zf.read(name).decode("utf-8"))
                except UnicodeDecodeError:
                    pass
        return texts
    try:
        return [raw.decode("utf-8")]
    except UnicodeDecodeError:
        return []


def process_base64_raw(raw: bytes, orig_val: str, word_map: dict, street_map: dict, used_numbers: set, counters: dict) -> str:
    # обезличиваем содержимое base64 (включая вложенные zip-архивы) и пакуем обратно
    if raw.startswith(b"PK\x03\x04"):
        out_buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zin, zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                try:
                    txt = process_text(data.decode("utf-8"), word_map, street_map, used_numbers, counters, False)
                    data = txt.encode("utf-8")
                except UnicodeDecodeError:
                    pass
                zout.writestr(item, data)
        counters["base64"] += 1
        return base64.b64encode(out_buf.getvalue()).decode("ascii")
    try:
        txt = process_text(raw.decode("utf-8"), word_map, street_map, used_numbers, counters, False)
        counters["base64"] += 1
        return base64.b64encode(txt.encode("utf-8")).decode("ascii")
    except UnicodeDecodeError:
        return orig_val


def local_name(tag: str) -> str:
    # отрезаем длинный XML namespace вида {http://...}TagName -> TagName
    cached = TAG_CACHE.get(tag)
    if cached is not None:
        return cached
    res = tag.split("}", 1)[-1] if "}" in tag else tag
    TAG_CACHE[tag] = res
    return res


def format_attr_name(k: str) -> str:
    cached = ATTR_CACHE.get(k)
    if cached is not None:
        return cached
    res = "rdf:" + local_name(k) if "1999/02/22-rdf-syntax-ns#" in k else local_name(k)
    ATTR_CACHE[k] = res
    return res


def read_objects(xml_path: str):
    # потоковый парсинг XML через iterparse + очистка памяти, чтобы гигабайтные файлы не роняли ОЗУ
    context = ET.iterparse(xml_path, events=("start", "end"), huge_tree=True)
    root = None
    for event, elem in context:
        if root is None:
            root = elem
            continue
        if event == "end" and elem.getparent() is root:
            yield elem
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]


def leaf_values(elem):
    cls = local_name(elem.tag)
    for child in elem:
        field = local_name(child.tag)
        res = None
        for k, v in child.attrib.items():
            if k.endswith("resource"):
                res = v
                break
        yield cls, field, child, child.text or "", res


class ModelStats:
    """Считает контрольные суммы и количество объектов/ссылок для самопроверки после обезличивания."""

    def __init__(self):
        self.classes = defaultdict(int)
        self.refs = defaultdict(int)
        self.numbers = defaultdict(lambda: [0, 0.0])

    def add_object(self, elem):
        cls = local_name(elem.tag)
        self.classes[cls] += 1
        for _, field, _, value, res in leaf_values(elem):
            if res is not None:
                self.refs[(cls, field, "" if res.startswith("#") else res)] += 1
                continue
            if is_id_field(field) or field in NAME_FIELDS or field in PERSON_FIELDS:
                continue
            val_s = value.strip()
            if val_s and is_pure_float(val_s):
                item = self.numbers[(cls, field)]
                item[0] += 1
                item[1] += float(val_s.replace(",", "."))

    def diff(self, other: "ModelStats") -> list:
        diffs = []
        if dict(self.classes) != dict(other.classes):
            diffs.append("Изменилось количество объектов по классам.")
        if dict(self.refs) != dict(other.refs):
            diffs.append("Изменилось количество или типы ссылок.")
        for k in set(self.numbers.keys()) | set(other.numbers.keys()):
            c1, s1 = self.numbers[k]
            c2, s2 = other.numbers[k]
            if c1 != c2 or abs(s1 - s2) > 1e-4:
                diffs.append(f"Числовое поле {k}: было ({c1}, {s1}), стало ({c2}, {s2})")
        return diffs


def detect_person_string(cls: str, field: str, val: str):
    # эвристика для поиска ФИО, логинов и ИП (плюс грубое определение пола по окончанию)
    if field in PERSON_FIELDS:
        return True, val.lower().endswith(("а", "я"))
    if val.startswith("ck11user"):
        return True, False
    if val.startswith("ИП "):
        parts = val[3:].split()
        return True, (len(parts) > 0 and parts[0].lower().endswith(("ова", "ева", "ина", "ая")))
    if cls in ("Customer", "Person", "ErpPerson") and "name" in field.lower():
        parts = val.split()
        if len(parts) in (2, 3) and all(p[0].isupper() for p in parts if p):
            return True, parts[0].lower().endswith(("ова", "ева", "ина", "ая"))
    return False, False


def collect(xml_path: str):
    # Первый проход: собираем статистику исходника и все уникальные слова/улицы для словаря замен
    stats = ModelStats()
    words = set()
    streets = set()
    obj_count = 0
    t_last = time.time()
    for elem in read_objects(xml_path):
        obj_count += 1
        if obj_count % 250000 == 0:
            print(f"  [Проход 1] обработано {obj_count} объектов ({time.time() - t_last:.1f} с)...", flush=True)
            t_last = time.time()
        stats.add_object(elem)
        cls = local_name(elem.tag)
        if cls in TECHNICAL_CLASSES or cls in REGION_CLASSES:
            continue
        for _, field, _, val, res in leaf_values(elem):
            if res is not None:
                continue
            stripped = val.strip()
            if not stripped or is_pure_float(stripped):
                continue
            if detect_person_string(cls, field, stripped)[0]:
                continue
            raw_b64 = decode_base64(val)
            texts = base64_texts(raw_b64) if raw_b64 is not None else [val]
            for txt in texts:
                clean_txt = EMAIL_PATTERN.sub(" ", txt) if "@" in txt else txt
                tokens = WORD_PATTERN.findall(clean_txt)
                is_street = ("Street" in field or "Street" in txt or "ул." in txt or "улица" in txt.lower())
                for tok in tokens:
                    if tok in STOP_WORDS or tok.isdigit() or len(tok) < 3:
                        continue
                    if tok.startswith(("meter", "ck11", "http", "www")):
                        continue
                    if is_street:
                        streets.add(tok)
                    else:
                        words.add(tok)
    return obj_count, words, streets, stats


def process_value(cls: str, field: str, val: str, word_map: dict, street_map: dict, used_numbers: set, counters: dict) -> str:
    if not val.strip() or cls in REGION_CLASSES or cls in TECHNICAL_CLASSES:
        return val
    stripped = val.strip()
    is_pers, is_fem = detect_person_string(cls, field, stripped)
    if is_pers:
        counters["persons"] += 1
        prof = make_person_profile(stripped, female=is_fem)
        if field == "Person.firstName":
            return prof["first"]
        if field == "Person.lastName":
            return prof["last"]
        if field == "Person.mName":
            return prof["middle"]
        return f"ИП {prof['fio']}" if stripped.startswith("ИП ") else prof["fio"]
    raw_b64 = decode_base64(val)
    if raw_b64 is not None:
        return process_base64_raw(raw_b64, val, word_map, street_map, used_numbers, counters)
    return process_text(val, word_map, street_map, used_numbers, counters, allow_pure_number=is_id_field(field))


def process_object(elem, word_map: dict, street_map: dict, used_numbers: set, counters: dict):
    # подменяем UUID в атрибутах самого объекта (rdf:ID / rdf:about) и в ссылках (rdf:resource)
    for k, v in elem.attrib.items():
        if len(v) >= 36 and "-" in v:
            elem.attrib[k] = UUID_PATTERN.sub(lambda m: new_uuid(m.group(0)), v)
    for cls, field, child, val, res in leaf_values(elem):
        for k, v in child.attrib.items():
            if len(v) >= 36 and "-" in v:
                child.attrib[k] = UUID_PATTERN.sub(lambda m: new_uuid(m.group(0)), v)
            elif v.startswith(("http://", "https://")):
                child.attrib[k] = process_text(v, word_map, street_map, used_numbers, counters, False)
        if res is None and val:
            child.text = process_value(cls, field, val, word_map, street_map, used_numbers, counters)


XML_ESCAPE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


def serialize_element(elem) -> str:
    # вручную собираем XML-строку вместо etree.tostring — так работает в разы быстрее
    cls = local_name(elem.tag)
    attrs = "".join(f' {format_attr_name(k)}="{v.translate(XML_ESCAPE)}"' for k, v in elem.attrib.items())
    parts = [f"  <cim:{cls}{attrs}>\n"]
    for child in elem:
        field = local_name(child.tag)
        c_attrs = "".join(f' {format_attr_name(k)}="{v.translate(XML_ESCAPE)}"' for k, v in child.attrib.items())
        txt = child.text
        if txt:
            parts.append(f"    <cim:{field}{c_attrs}>{txt.translate(XML_ESCAPE)}</cim:{field}>\n")
        else:
            parts.append(f"    <cim:{field}{c_attrs}/>\n")
    parts.append(f"  </cim:{cls}>\n\n")
    return "".join(parts)


def anonymize(src_path: str, dst_path: str, word_map: dict, street_map: dict):
    # Второй проход: читаем объекты, подменяем данные по готовым словарям и пишем в выходной файл
    stats = ModelStats()
    used_numbers = set(DB_VAULT.numbers.values()) if DB_VAULT else set()
    counters = {"persons": 0, "base64": 0, "numbers": set()}
    obj_count = 0
    t_last = time.time()
    out_dir = os.path.dirname(os.path.abspath(dst_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    # ставим большой буфер записи (64 МБ), чтобы не тормозить об диск
    with open(dst_path, "w", encoding="utf-8", buffering=64 * 1024 * 1024) as out_f:
        out_f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        out_f.write('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/2013/CIM-schema-cim16#">\n')
        for elem in read_objects(src_path):
            obj_count += 1
            if obj_count % 250000 == 0:
                print(f"  [Проход 2] записано {obj_count} объектов ({time.time() - t_last:.1f} с)...", flush=True)
                t_last = time.time()
            process_object(elem, word_map, street_map, used_numbers, counters)
            stats.add_object(elem)
            out_f.write(serialize_element(elem))
        out_f.write("</rdf:RDF>\n")
    return obj_count, counters, stats


def main():
    global SECRET_KEY, DB_VAULT, OUTPUT_LANG, FRUIT_ADJECTIVES
    if len(sys.argv) < 5:
        print("Использование: python anonymize.py <вход.xml> <выход.xml> <секрет> <хранилище.sqlite> [ru|en]")
        sys.exit(1)
    src, dst, SECRET_KEY, vault_path = sys.argv[1:5]
    if len(sys.argv) >= 6 and sys.argv[5].lower() in ("ru", "en"):
        OUTPUT_LANG = sys.argv[5].lower()
    if not FRUITS_LIST:
        raise ValueError("Справочник fruits.tsv пуст!")
    FRUIT_ADJECTIVES = [to_adjective(f[0] if isinstance(f, list) else f) for f in FRUITS_LIST]
    if os.path.abspath(src) == os.path.abspath(dst):
        print("ОШИБКА: выходной файл совпадает с входным.")
        sys.exit(1)
    vault_dir = os.path.dirname(os.path.abspath(vault_path))
    if vault_dir and not os.path.exists(vault_dir):
        os.makedirs(vault_dir, exist_ok=True)
    DB_VAULT = Vault(vault_path)
    try:
        words_before = len(DB_VAULT.words)
        nums_before = len(DB_VAULT.numbers)
        print(f"Хранилище {vault_path} (язык: {OUTPUT_LANG}): из прошлых выгрузок имён {words_before}, номеров {nums_before}", flush=True)
        t0 = time.time()
        obj_in, words, streets, stats_in = collect(src)
        word_map = build_word_map(words)
        street_map = build_street_map(streets)
        t1 = time.time()
        print(f"Проход 1: объектов {obj_in}, имён собственных {len(words)} (новых {len(DB_VAULT.new_words)}), "
              f"улиц {len(streets)} — {t1 - t0:.3f} с", flush=True)
        obj_out, counters, stats_out = anonymize(src, dst, word_map, street_map)
        t2 = time.time()
        print(f"Проход 2: объектов {obj_out}, ФИО/Email {counters['persons']}, base64-вложений {counters['base64']}, "
              f"номеров {len(counters['numbers'])} (новых {len(DB_VAULT.new_numbers)}) — {t2 - t1:.3f} с", flush=True)

        # сверяем статистику до и после — если где-то потерялся тег или сбилась сумма, откатываемся
        differences = stats_in.diff(stats_out)
        stat_ok = len(differences) == 0
        report = {
            "input": os.path.abspath(src),
            "output": os.path.abspath(dst),
            "vault": os.path.abspath(vault_path),
            "lang": OUTPUT_LANG,
            "seconds": round(t2 - t0, 3),
            "objects": obj_out,
            "persons": counters["persons"],
            "base64": counters["base64"],
            "words": {"total": len(words), "new": len(DB_VAULT.new_words), "in_vault_before": words_before},
            "numbers": {"total": len(counters["numbers"]), "new": len(DB_VAULT.new_numbers), "in_vault_before": nums_before},
            "statistics_equal": stat_ok,
            "differences": differences,
            "classes": dict(stats_out.classes)
        }
        report_path = dst + ".report.json"
        with open(report_path, "w", encoding="utf-8") as rf:
            json.dump(report, rf, ensure_ascii=False, indent=2)
        if not stat_ok:
            for diff_item in differences[:5]:
                print("  Расхождение:", diff_item)
            print("ОШИБКА: статистика изменилась — хранилище НЕ обновлено, результат использовать нельзя.")
            sys.exit(2)
        print(f"Контроль статистики: классов {len(stats_out.classes)}, видов ссылок {len(stats_out.refs)}, "
              f"числовых полей {len(stats_out.numbers)} — совпадает", flush=True)
        DB_VAULT.save(os.path.basename(src))
        size_mb = int(os.path.getsize(dst) / (1024 * 1024))
        print(f"Готово: {dst} ({size_mb} МБ) за {t2 - t0:.3f} с; отчёт {report_path}; хранилище обновлено", flush=True)
    finally:
        DB_VAULT.close()


if __name__ == "__main__":
    main()
