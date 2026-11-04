"""
OazisSyncBot: переносит новые материалы из «Объекты Горные кластеры»
в «Алтай обьекты» по совпадению проекта в названии топика.

Запуск: BOT_TOKEN=... python sync.py        (DRY_RUN=1 — только показать, ничего не отправлять)
Работает по расписанию в GitHub Actions; состояние хранится в state.json.
"""
import json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
STATE_PATH = os.path.join(HERE, "state.json")
STATE = json.load(open(STATE_PATH, encoding="utf-8"))
TOKEN = os.environ["BOT_TOKEN"].strip()
DRY = os.environ.get("DRY_RUN") == "1"
PUBLIC = os.environ.get("PUBLIC_LOG") == "1"      # в открытом репозитории не печатаем названия
LOOP_MINUTES = float(os.environ.get("LOOP_MINUTES", "0"))  # >0: работать без перерывов N минут
SAVE_EVERY = 30 * 60
SRC, DST = CFG["source_chat_id"], CFG["target_chat_id"]
MSK = timezone(timedelta(hours=3))
CONTENT_KEYS = ("text", "photo", "video", "document", "audio", "voice",
                "video_note", "animation", "location", "venue", "contact")

sys.stdout.reconfigure(encoding="utf-8")


def api(method, **params):
    data = json.dumps(params).encode("utf-8")
    for attempt in range(5):
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}",
                                     data=data, headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=70).read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8") or "{}")
            wait = body.get("parameters", {}).get("retry_after")
            if e.code == 429 and wait:
                time.sleep(wait + 1)
                continue
            return body
        except Exception as e:  # сеть
            if attempt == 4:
                raise
            time.sleep(3)


def log(text):
    stamp = datetime.now(MSK).strftime("%d.%m %H:%M")
    if not PUBLIC:
        print(f"[{stamp}] {text}")
    STATE["log"] = (STATE["log"] + [f"{stamp} {text}"])[-200:]


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip()


def projects():
    return CFG["projects"] + STATE["extra_projects"]


def project_of(topic_name):
    n = norm(topic_name)
    for p in projects():
        if any(norm(k) in n for k in p["kw"]):
            return p["name"]
    return None


def target_thread(project):
    for tid, name in STATE["target_topics"].items():
        if project_of(name) == project:
            return int(tid)
    return None


def clean_title(name):
    s = re.sub(r"\(.*?\)", " ", name)
    s = re.sub(r"(?i)\b(жк|кп|скоро)\b|алтай\w*", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -–—,.") or name


def learn_topics(msg, store):
    """Запоминаем номер и название топика из любого сообщения."""
    tid = msg.get("message_thread_id")
    if msg.get("forum_topic_created"):
        store[str(msg.get("message_thread_id") or msg["message_id"])] = msg["forum_topic_created"]["name"]
    if msg.get("forum_topic_edited", {}).get("name") and tid:
        store[str(tid)] = msg["forum_topic_edited"]["name"]
    root = msg.get("reply_to_message") or {}
    if tid and root.get("message_id") == tid and root.get("forum_topic_created"):
        store[str(tid)] = root["forum_topic_created"]["name"]


def is_noise(msg, min_len=None):
    if not any(k in msg for k in CONTENT_KEYS):
        return True  # служебные: вступил, закрепил, создан топик, стикер, опрос
    if "text" in msg:
        has_link = any(e["type"] in ("url", "text_link") for e in msg.get("entities", []))
        if not has_link and len(msg["text"].strip()) < (min_len or CFG["min_text_length"]):
            return True  # «@anisi_7», «Это кто-то сделал?» и т.п.
    return False


def resolve(src_thread):
    """-> (project, target_thread | None) или (None, причина)"""
    name = STATE["source_topics"].get(str(src_thread))
    if not name:
        return None, f"топик #{src_thread} ещё неизвестен"
    if norm(name) in [norm(x) for x in CFG["skip_topics"]]:
        return None, f"«{name}» не переносим"
    proj = project_of(name)
    if not proj and CFG["auto_create_if_name_contains"] in norm(name):
        title = clean_title(name)
        proj = title
        STATE["extra_projects"].append({"name": title, "kw": [norm(title)]})
        if not DRY:
            r = api("createForumTopic", chat_id=DST, name=title)
            if r.get("ok"):
                STATE["target_topics"][str(r["result"]["message_thread_id"])] = title
                log(f"создан новый топик «{title}» в Алтай обьекты")
    if not proj:
        return None, f"«{name}» — проект не найден в списке"
    return proj, target_thread(proj)


def copy(ids, thread, project, src_name, from_chat=None):
    ids = sorted(set(ids))
    from_chat = from_chat or SRC
    if DRY:
        log(f"[тест] {len(ids)} сообщ. «{src_name}» → «{project}» (#{thread})")
        return True
    for i in range(0, len(ids), 100):
        r = api("copyMessages", chat_id=DST, message_thread_id=thread,
                from_chat_id=from_chat, message_ids=ids[i:i + 100])
        if not r.get("ok"):
            desc = r.get("description", "")
            log(f"ошибка копирования в «{project}»: {desc}")
            if "thread not found" in desc.lower():
                STATE["target_topics"].pop(str(thread), None)
                return False
            return True  # сообщение удалено и т.п. — не повторяем
        time.sleep(1.5)
    log(f"перенесено {len(ids)} сообщ. «{src_name}» → «{project}»")
    return True


def fetch(poll):
    r = api("getUpdates", offset=STATE["last_update_id"] + 1, timeout=poll,
            allowed_updates=["message", "edited_message"])
    if not r.get("ok"):
        log(f"getUpdates: {r.get('description')}")
        time.sleep(10)
        return []
    ups = r["result"]
    if ups and poll:
        time.sleep(4)  # альбом приходит пачкой — дождёмся остальных частей
        more = api("getUpdates", offset=ups[-1]["update_id"] + 1, timeout=0,
                   allowed_updates=["message", "edited_message"])
        ups += more.get("result", [])
    return ups


def cycle(poll=0):
    updates = fetch(poll)

    batches = []  # [(project, src_name, from_chat, [ids])]
    for u in updates:
        msg = u.get("message")
        if not msg:
            continue
        chat = msg["chat"]["id"]
        if chat == DST:
            learn_topics(msg, STATE["target_topics"])
            continue
        if chat != SRC:
            # отдельный чат с застройщиком: проект — по config.source_chats или по названию чата
            title = msg["chat"].get("title", "")
            proj = CFG.get("source_chats", {}).get(str(chat)) or project_of(title)
            # застройщик ведёт несколько проектов в одном чате (Карлуу + Умун): смотрим на имя файла
            fname = (msg.get("document") or {}).get("file_name", "")
            if fname and project_of(fname):
                proj = project_of(fname)
            if msg["chat"]["type"] == "private" or is_noise(msg, CFG.get("chat_min_text_length", 120)):
                continue
            if not proj:
                if str(chat) not in STATE.setdefault("unmapped_chats", {}):
                    STATE["unmapped_chats"][str(chat)] = title
                    log(f"чат «{title}» ({chat}): не понял, какой это проект — впиши в config.source_chats")
                continue
            src_name = f"чат {title}"
            key = (proj, src_name, chat)
        else:
            learn_topics(msg, STATE["source_topics"])
            tid = msg.get("message_thread_id") if msg.get("is_topic_message") else None
            if not tid or is_noise(msg):
                continue
            proj, _ = resolve(tid)
            if not proj:
                continue
            src_name = STATE["source_topics"][str(tid)]
            key = (proj, src_name, SRC)
        if batches and tuple(batches[-1][:3]) == key:
            batches[-1][3].append(msg["message_id"])
        else:
            batches.append([*key, [msg["message_id"]]])

    # сначала — то, что ждало, пока бот узнает номер топика
    still = []
    for p in STATE["pending"]:
        thread = target_thread(p["project"])
        if thread and copy(p["ids"], thread, p["project"], p["src"], p.get("chat")):
            continue
        if time.time() - p["since"] < 30 * 86400:
            still.append(p)
    for proj, src_name, chat, ids in batches:
        thread = target_thread(proj)
        if thread and copy(ids, thread, proj, src_name, chat):
            continue
        log(f"ждёт: {len(ids)} сообщ. «{src_name}» — в «Алтай обьекты» пока не найден топик «{proj}»")
        still.append({"project": proj, "ids": ids, "src": src_name, "chat": chat, "since": int(time.time())})
    STATE["pending"] = still

    if updates and not DRY:
        STATE["last_update_id"] = updates[-1]["update_id"]
    if not DRY:
        save()
    return len(updates), len(batches)


def save():
    json.dump(STATE, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    if LOOP_MINUTES <= 0:
        n, b = cycle(0)
        if not DRY:
            api("getUpdates", offset=STATE["last_update_id"] + 1, timeout=0)  # подтверждаем
        print(f"обновлений: {n}, пакетов: {b}, ждут: {len(STATE['pending'])}")
        return
    end = time.time() + LOOP_MINUTES * 60
    last_save = time.time()
    total_u = total_b = 0
    while time.time() < end - 60:
        n, b = cycle(poll=min(50, max(1, int(end - time.time() - 60))))
        total_u += n; total_b += b
        if time.time() - last_save > SAVE_EVERY and os.environ.get("SAVE_CMD"):
            os.system(os.environ["SAVE_CMD"])  # шифруем и сохраняем состояние в репозиторий
            last_save = time.time()
    api("getUpdates", offset=STATE["last_update_id"] + 1, timeout=0)
    print(f"цикл завершён: обновлений {total_u}, пакетов {total_b}, ждут {len(STATE['pending'])}")


if __name__ == "__main__":
    main()
