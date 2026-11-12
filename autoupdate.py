"""Автообновление карточек сайта методички по сообщениям из чатов застройщиков.

Каждое сообщение раскладывается по разделам карточки:
  файл          -> «Шахматка и материалы» (ссылка на сообщение)
  комиссия, %   -> поле «Комиссия» + строка в «Комиссия подробно»
  мин. лот / цена -> поле «Цена»
  телефон ОП    -> «Отдел продаж» (новый номер)
  оплата, акции -> раздел «Цены и оплата» (блок «Из чатов»)
  фиксация      -> раздел «Как зафиксировать» (блок «Из чатов»)
  стройка, сроки, старт продаж -> «Об объекте» (блок «Из чатов»)
Перед каждым изменением прежняя версия карточки уходит в history/<id> (откат на сайте).
Сообщения брокеров Oazis (в подписи «оазис», «oazis», «алтазис», «брокер») карточку не меняют.
"""
import json, re, time, urllib.request

BROKER = re.compile(r"оазис|oazis|алтаз|брокер|broker", re.I)
PCT = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s?%")
COMM = re.compile(r"комисси|вознагражд|\bкв\b", re.I)
MONEY = re.compile(r"(\d{1,3}(?:[   ]\d{3}){2,}(?:[.,]\d+)?)\s*(?:₽|руб|р\.)|(\d{1,3}(?:[.,]\d{1,3})?)\s*млн", re.I)
MINLOT = re.compile(r"мин\w*\.?\s*(?:лот|стоимост|цен|предложен)|стоимость\s+минимальн", re.I)
PHONE = re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
SECTIONS = [
    ("pay", re.compile(r"рассроч|ипотек|скидк|акци|первоначальн|\bпв\b|бронир|бронь|транш|лизинг|субсид|прайс|повышени\w* цен|цены выр|стоимост", re.I)),
    ("fix", re.compile(r"фиксац|зафиксир|уникальн|личн\w+ кабинет|\bлк\b", re.I)),
    ("about", re.compile(r"сдач|ввод в эксплуат|ход строительств|\bрнс\b|разрешени\w+ на строит|старт продаж|очеред|корпус", re.I)),
]
FILE_TYPES = [("Шахматка", r"шахмат"), ("Прайс", r"прайс|price"), ("КП", r"\bкп\b|kp_|коммерческ|апартамент №|лот|номер"),
              ("Презентация", r"презент|буклет|brochure|web-"), ("Договор", r"договор|ддуб?|оферт|агентск"),
              ("Расчёт", r"расч[её]т|доходн|финмодел|инвест"), ("Генплан", r"генплан|мастерплан|схема"),
              ("Акция", r"акци|спецпредлож|специальн"), ("Документы", r"разрешени|декларац|рнс|egrn|егрн")]
MAX_AUTO = 4  # сколько последних сообщений хранить в блоке «Из чатов» каждого раздела


def _req(base, path, method="GET", data=None):
    r = urllib.request.Request(base + path + ".json", method=method,
                               data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
    for attempt in range(4):  # база иногда не отвечает с первого раза
        try:
            return json.loads(urllib.request.urlopen(r, timeout=25).read().decode("utf-8") or "null")
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 + attempt * 3)


def file_type(name):
    n = name.lower()
    for t, rx in FILE_TYPES:
        if re.search(rx, n):
            return t
    return "Файл"


def fmt_money(m):
    if m.group(1):
        return m.group(1).replace(" ", " ").replace(" ", " ") + " ₽"
    return m.group(2) + " млн ₽"


def _mentions(line, kws):
    l = line.lower().replace("ё", "е")
    return any(k in l for k in kws)


COND = re.compile(r"\bпри\b|\bесли\b|\bот \d|ключев|до конца|только|лот\w* от|за лот|по акци|в рамках акци", re.I)


def analyse(text, file_name, sender, from_target, own_kw=(), other_kw=(), require_own=False):
    """Что из сообщения можно занести в карточку. from_target=True: пост в «Алтай обьекты» (там пишет Oazis).
    own_kw — ключевые слова этого объекта, other_kw — других объектов: если строка про другой объект,
    цену и комиссию из неё не берём."""
    if not from_target and BROKER.search(sender or ""):
        return {}
    out = {}
    t = text or ""
    if file_name:
        out["file"] = file_type(file_name)
    if t:
        question = t.strip().endswith("?")
        lines = [l for l in t.split("\n") if l.strip()]
        multi = any(_mentions(l, other_kw) for l in lines)

        def usable(line):
            if question:
                return False
            if multi:  # в сообщении есть другие объекты: берём только строки про наш
                return _mentions(line, own_kw) and not _mentions(line, other_kw)
            return True

        if require_own and not _mentions(t, own_kw):
            usable = lambda line: False  # чат с несколькими ЖК, а наш объект не назван — цифры не берём
        nums = set()
        for l in lines:
            for m in COMM.finditer(l):
                if not usable(l):
                    continue
                if COND.search(l):  # «за лоты от 23 млн — 8%», «если сделка до конца месяца» — только в заметку
                    out["kv_note_only"] = True
                    continue
                for x in PCT.findall(l[m.start():m.start() + 90]):
                    v = float(x.replace(",", "."))
                    if 2 <= v <= 12:
                        nums.add(v)
        if nums:
            lo, hi = min(nums), max(nums)
            f = lambda v: (f"{v:g}").replace(".", ",")
            out["kv"] = f"{f(lo)}%" if lo == hi else f"{f(lo)}–{f(hi)}%"
        for i, l in enumerate(lines):
            if MINLOT.search(l) and usable(l):
                win = l[MINLOT.search(l).start():] + " " + (lines[i + 1] if i + 1 < len(lines) else "")
                m = MONEY.search(win[:160])
                if m:
                    val = float((m.group(1) or "0").replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")) \
                        if m.group(1) else float(m.group(2).replace(",", ".")) * 1e6
                    if 1e6 <= val <= 5e8:
                        out["price"] = "мин. лот " + fmt_money(m)
                        break
        phones = PHONE.findall(t)
        if phones and len(phones) <= 8 and re.search(r"отдел продаж|\bоп\b|менеджер|звоните|по всем вопросам|на связи", t, re.I):
            out["phones"] = phones
        if len(t) >= 80:
            out["sections"] = [s for s, rx in SECTIONS if rx.search(t)]
    return out


def short(t, n=600):
    t = re.sub(r"\n{2,}", "\n", (t or "").strip())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def apply(base, site_id, msg, where, link, from_target=False, own_kw=(), other_kw=(), require_own=False):
    """Применить сообщение к карточке. Возвращает список изменённых разделов."""
    doc = msg.get("document") or {}
    text = msg.get("text") or msg.get("caption") or ""
    sender = " ".join(x for x in ((msg.get("from") or {}).get("first_name"), (msg.get("from") or {}).get("last_name"),
                                    (msg.get("from") or {}).get("username")) if x)
    a = analyse(text, doc.get("file_name", ""), sender, from_target, own_kw, other_kw, require_own)
    if not a:
        return []
    o = _req(base, f"/objects/{site_id}")
    if not o:
        return []
    prev = json.loads(json.dumps(o))
    now = int(time.time() * 1000)
    date = time.strftime("%d.%m", time.localtime(msg["date"]))
    src = {"t": msg["date"] * 1000, "chat": where, "link": link}
    changed = []

    if "file" in a:
        links = o.get("links") or []
        if not any(link and link == (l[1] if len(l) > 1 else "") for l in links):
            links.insert(0, [a["file"], link, f"{doc['file_name']} · из чата {date}"])
            o["links"] = links
            changed.append("материалы")
    if "kv" in a and a["kv"] != o.get("kv"):
        o.setdefault("autoFields", {})["kv"] = {**src, "old": o.get("kv", "")}
        o["kv"] = a["kv"]
        changed.append("комиссия")
    if "price" in a and a["price"] != o.get("price"):
        o.setdefault("autoFields", {})["price"] = {**src, "old": o.get("price", "")}
        o["price"] = a["price"]
        changed.append("цена")
    if "phones" in a:
        ops = o.get("op") or []
        have = {re.sub(r"\D", "", (c[2] if len(c) > 2 else ""))[-10:] for c in ops}
        for p in a["phones"]:
            if re.sub(r"\D", "", p)[-10:] not in have:
                ops.append([sender or "Отдел продаж", f"из чата {date}", p.strip(), ""])
                have.add(re.sub(r"\D", "", p)[-10:])
                changed.append("отдел продаж")
        o["op"] = ops
    secs = set(a.get("sections", []))
    if "kv" in a or a.get("kv_note_only"):
        secs.add("kv")
    for s in secs:
        auto = o.setdefault("auto", {})
        items = [i for i in (auto.get(s) or []) if i.get("link") != link]
        items.insert(0, {**src, "text": short(text)})
        auto[s] = items[:MAX_AUTO]
        changed.append({"pay": "оплата", "fix": "фиксация", "about": "об объекте", "kv": "комиссия подробно"}[s])
    if not changed:
        return []
    o["upd"] = {"at": now, "who": f"бот: {where}"}
    _req(base, f"/history/{site_id}", "POST", {"at": now, "who": f"до автообновления из «{where}»", "prev": prev})
    _req(base, f"/objects/{site_id}", "PUT", o)
    return sorted(set(changed))
