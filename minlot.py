"""Минимальный свободный лот прямо с шахматок застройщиков.

Работает без входа в кабинеты: берём только открытые шахматки.
  Карлуу, УМУН  — md.rclick.ru (каталог «Мой Дом», сортировка по цене)
  Город Гор     — selfstroit.ru (поиск квартир, без перепродаж)
  Ризалта       — client.rizaltaservice.ru/api/lots (status = available)
  Cosmos Stay   — xlsx-шахматка 2 очереди, когда её присылают в чат (cosmos_xlsx)
Результат пишется в карточку: поле price + autoFields.price + minlot, старая версия — в историю.
"""
import html, io, json, re, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; OazisSyncBot)"}


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
        except OSError:
            if attempt == 2:
                raise
            time.sleep(5)


def _rub(s):
    return int(re.sub(r"\D", "", s))


def fmt(v):
    return f"{v:,}".replace(",", " ") + " ₽"


def mydom(catalog_id):
    url = f"https://md.rclick.ru/catalog/{catalog_id}/?sorting=price_asc&view=view-catalog"
    t = re.sub(r"<[^>]+>", "\n", html.unescape(_get(url)))
    t = re.sub("[ 	 ]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    lots = []
    for m in re.finditer(r"(?:(\d) корпус, |Очередь \d, )?кв\. №(\d+)\n([^\n]*?), ([\d.]+) м\n?2\n(\d{1,3}(?: \d{3}){2}) ₽", t):
        lots.append((_rub(m.group(5)), f"{m.group(3).strip()} {m.group(4).replace('.', ',')} м², кв. {m.group(2)}"))
    if not lots:
        return None
    price, desc = min(lots)
    return {"price": price, "desc": desc, "url": url}


def selfstroit():
    url = "https://selfstroit.ru/search/?view=tile&kind=flats&project%5B%5D=10&sort=ASC&ajaxSearchRequest=1"
    raw = _get(url, {"X-Requested-With": "XMLHttpRequest"})
    try:
        raw = json.loads(raw)
    except ValueError:
        pass
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(raw)))
    lots = []
    for block in t.split("ГОРОД ГОР")[1:]:
        head = block.split("просмотр")[0]
        if "перепродаж" in head:
            continue
        p = re.search(r"(\d{1,3}(?: \d{3}){2}) ₽", head)
        a = re.search(r"Площадь м² ([\d.]+)", head)
        r = re.search(r"Комнат (\S+)", head)
        if p:
            lots.append((_rub(p.group(1)), f"{r.group(1) if r else '?'}-комн. {a.group(1).replace('.', ',') if a else '?'} м²"))
    if not lots:
        return None
    price, desc = min(lots)
    return {"price": price, "desc": desc, "url": "https://selfstroit.ru/search/?view=tile&kind=flats&project%5B%5D=10&sort=ASC"}


def rizalta():
    d = json.loads(_get("https://client.rizaltaservice.ru/api/lots"))
    lots = [l for l in d.get("lots", []) if l.get("status") == "available" and l.get("price")]
    if not lots:
        return None
    l = min(lots, key=lambda x: x["price"])
    return {"price": int(l["price"]), "url": "https://client.rizaltaservice.ru/",
            "desc": f"{str(l['area']).replace('.', ',')} м², {l.get('buildingName', '')} (корп. {l.get('building')}), {l.get('floor')} этаж, лот {l.get('code')}"}


def cosmos_xlsx(data):
    """xlsx-шахматка Cosmos Stay: строки «номер / S общая / … / цена»; проданные вместо цены — «Продано»/«Бронь»."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    free = []
    for ws in wb:
        for r in range(6, ws.max_row + 1):
            if "цена" not in [str(ws.cell(r, c).value or "").strip() for c in (1, 2)]:
                continue
            for c in range(2, ws.max_column + 1):
                v = ws.cell(r, c).value
                if isinstance(v, (int, float)) and 5e6 < v < 3e8:
                    num, area = ws.cell(r - 5, c).value, ws.cell(r - 4, c).value
                    free.append((int(v), f"№{int(num) if isinstance(num, float) else num}, {str(area).replace('.', ',')} м²"))
    if not free:
        return None
    price, desc = min(free)
    return {"price": price, "desc": desc + " (шахматка 2 очереди)"}


SOURCES = {"karluu": lambda: mydom(284), "umun": lambda: mydom(289), "gorod-gor": selfstroit, "rizalta": rizalta}


def write(base, req, site_id, res, source="шахматка"):
    """Записать минимальный лот в карточку, если он изменился. Возвращает True при изменении."""
    o = req(base, f"/objects/{site_id}")
    if not o or not res:
        return False
    text = f"мин. лот {fmt(res['price'])}"
    old = (o.get("minlot") or {}).get("price")
    if old == res["price"] and o.get("price") == text:
        req(base, f"/objects/{site_id}/minlot/checked", "PUT", int(time.time() * 1000))
        return False
    now = int(time.time() * 1000)
    req(base, f"/history/{site_id}", "POST", {"at": now, "who": f"до обновления мин. лота ({source})", "prev": o})
    patch = {"price": text,
             "minlot": {"price": res["price"], "desc": res.get("desc", ""), "url": res.get("url", ""), "at": now, "checked": now, "source": source},
             "autoFields/price": {"t": now, "chat": source, "link": res.get("url", ""), "old": o.get("price", "")},
             "upd": {"at": now, "who": f"бот: мин. лот с шахматки"}}
    req(base, f"/objects/{site_id}", "PATCH", patch)
    return True


def run_all(base, req, log=print):
    for site_id, fn in SOURCES.items():
        try:
            res = fn()
            if res and write(base, req, site_id, res):
                log(f"сайт: мин. лот «{site_id}» → {fmt(res['price'])}")
        except Exception as e:
            log(f"сайт: шахматка «{site_id}» не прочиталась ({type(e).__name__})")
