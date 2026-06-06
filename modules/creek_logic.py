import os
import re
import json
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CREEK_DB = os.path.join(os.path.dirname(BASE_DIR), "databases/creek_notes.json")
DUMP_DB = os.path.join(os.path.dirname(BASE_DIR), "databases/dump.json")


# -----------------------------
# SAFE JSON HELPERS
# -----------------------------
def load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


# -----------------------------
# MESSAGE LANGUAGE CHECK
# -----------------------------
def is_english_only(text):
    """
    Returns True if the text contains ONLY English characters, 
    numbers, common punctuation, and emojis.
    """
    # If the text is empty (e.g., pure media with no caption), treat as valid
    if not text:
        return True

    # Regex breakdown:
    # ^[ -~]+$ matches any string consisting entirely of printable ASCII characters
    # (characters from space ' ' to tilde '~'). This covers all English letters, 
    # numbers, standard punctuation, and spaces.
    #
    # To allow common symbols/emojis, we use a broader approach: 
    # checking if it contains non-latin characters.
    
    # This pattern matches characters belonging to non-Latin scripts 
    # (like Arabic, Cyrillic, Devanagari, Han, etc.)
    # Note: It allows standard English, numbers, punctuation, and emojis.
    non_english_pattern = re.compile(r'[\u0600-\u06FF\u0D00-\u0D7F\u0900-\u097F\u4e00-\u9fff]')
    
    # Alternative strict approach: Allow ONLY ASCII + common whitespace
    # if not re.match(r'^[\x00-\x7F]*$', text): return False
    
    # Let's use a robust ASCII-based check that drops non-ASCII text 
    # but gently allows emojis if needed. 
    # A highly reliable way for a Telegram bot is checking the ratio of English text:
    try:
        text.encode('ascii')
        return True # It's purely English/ASCII characters
    except UnicodeEncodeError:
        # If it has non-ASCII, check if those characters are non-English alphabets
        # (This prevents blocking messages just because they contain an emoji)
        for char in text:
            cp = ord(char)
            # Ranges for Arabic (\u0600-\u06FF) and Malayalam (\u0D00-\u0D7F) as examples
            if (0x0600 <= cp <= 0x06FF) or (0x0D00 <= cp <= 0x0D7F) or (0x0900 <= cp <= 0x097F):
                return False # Found non-English script character
        return True


# -----------------------------
# BOT → GROUP MAPPING
# -----------------------------
def get_allowed_group(bot_name):
    dump = load_json(DUMP_DB, {"bots": {}})
    return dump.get("bots", {}).get(bot_name)


# -----------------------------
# ADMIN CHECKS
# -----------------------------
def is_group_admin(chat_id, user_id, token):
    try:
        url = f"https://api.telegram.org/bot{token}/getChatMember"
        r = requests.get(url, params={"chat_id": chat_id, "user_id": user_id})
        status = r.json().get("result", {}).get("status")
        return status in ["administrator", "creator"]
    except:
        return False


def is_authorized(chat_id, user_id, admin_id, token):
    return str(user_id) == str(admin_id) or is_group_admin(chat_id, user_id, token)


# -----------------------------
# NOTES SYSTEM
# -----------------------------
def load_notes():
    if not os.path.exists(CREEK_DB):
        return {}
    try:
        with open(CREEK_DB, "r") as f:
            return json.load(f)
    except:
        return {}


def save_notes(data):
    save_json(CREEK_DB, data)


# -----------------------------
# MAIN LOGIC
# -----------------------------
def process_logic(msg, bot_name, admin_id, token):

    chat = msg.get("chat", {})
    chat_id = str(chat.get("id"))
    chat_type = chat.get("type")  # ✅ REQUIRED for private support

    user = msg.get("from", {})
    user_id = str(user.get("id"))

    text = msg.get("text", "")
    cmd = text.lower().strip()

    # =========================================================
    # 💬 PRIVATE CHAT LOGIC
    # =========================================================
    if chat_type == "private":
        if cmd == "/start":
            return {
                "type": "text",
                "data": f"🚀 *{bot_name} Online*\nUse !help to see usage"
            }

        if cmd == "!help":
            return {
                "type": "text",
                "data": (
                    "📂 Commands:\n\n"
                    "📝 Notes:\n"
                    "• !notes\n"
                    "• !save [name] (reply)\n"
                    "• ?name\n"
                )
            }
        
        return None  # ignore other private messages

    # =========================================================
    # 🔒 GROUP BINDING (dump.json CONTROLLED)
    # =========================================================
    allowed_group = get_allowed_group(bot_name)

    if not allowed_group:
        return None

    if chat_id != str(allowed_group):
        return None

    # =========================================================
    # 🧹 DELETE SERVICE MESSAGES (PRESERVED)
    # =========================================================
    if any(key in msg for key in ["new_chat_members", "left_chat_member", "new_chat_title"]):
        return "DELETE_MESSAGE"

    # =========================================================
    # LOAD NOTES DB
    # =========================================================
    db = load_notes()
    db.setdefault(chat_id, {})

    # =========================================================
    # GROUP COMMANDS
    # =========================================================
    if cmd == "/start":
        return {
            "type": "text",
            "data": f"🚀 {bot_name} active in Creek Assistant mode."
        }

    if cmd == "!help":
        return {
            "type": "text",
            "data": (
                "📌 Creek Assistant Commands:\n\n"
                "📝 Notes System:\n"
                "• !save <name> (reply)\n"
                "• ?name\n"
                "• !notes\n"
                "• !del <name>\n"
            )
        }

    # =========================================================
    # SAVE NOTE
    # =========================================================
    if cmd.startswith("!save "):

        if not is_authorized(chat_id, user_id, admin_id, token):
            return {"type": "text", "data": "⛔ Admin only."}

        note_name = cmd[6:].strip()
        reply = msg.get("reply_to_message")

        if not note_name or not reply:
            return {"type": "text", "data": "Reply to a message + !save name"}

        content_type = "text"
        content = reply.get("text")

        media_map = {
            "photo": lambda m: m["photo"][-1]["file_id"],
            "video": lambda m: m["video"]["file_id"],
            "document": lambda m: m["document"]["file_id"],
            "audio": lambda m: m["audio"]["file_id"]
        }

        for mtype, extractor in media_map.items():
            if mtype in reply:
                content_type = mtype
                content = extractor(reply)
                break

        db[chat_id][note_name] = {
            "type": content_type,
            "id": content,
            "caption": reply.get("caption", ""),
            "created_by": user_id
        }

        save_notes(db)

        return {
            "type": "text",
            "data": f"✅ Saved `{note_name}`"
        }

    # =========================================================
    # GET NOTE
    # =========================================================
    if cmd.startswith("?"):

        note_name = cmd[1:].strip()
        note = db.get(chat_id, {}).get(note_name)

        if not note:
            return {"type": "text", "data": "❌ Not found."}

        return {
            "type": note["type"],
            "data": note["id"],
            "caption": note.get("caption", ""),
            "reply_to": True
        }

    # =========================================================
    # LIST NOTES
    # =========================================================
    if cmd == "!notes":

        notes = db.get(chat_id, {})

        if not notes:
            return {"type": "text", "data": "📂 No notes found."}

        lines = [f" - `?{name}`" for name in notes.keys()]

        return {
            "type": "text",
            "data": "📝 *Creek Notes:*\n" + "\n".join(lines) + "\n\n use `?notename` to see the note"
        }

    # =========================================================
    # DELETE NOTE
    # =========================================================
    if cmd.startswith("!del "):

        if not is_authorized(chat_id, user_id, admin_id, token):
            return {"type": "text", "data": "⛔ Admin only."}

        note_name = cmd[5:].strip()

        if note_name in db.get(chat_id, {}):
            del db[chat_id][note_name]
            save_notes(db)
            return {"type": "text", "data": f"🗑 Deleted `{note_name}`"}

        return {"type": "text", "data": "❌ Not found."}

    # Extract text/caption safely
    text = (msg.get("text") or msg.get("caption") or "").strip()
        
    # --- NON-ENGLISH FILTER ---
    if text and not is_english_only(text):
        # Delete the message if the bot is admin in a group
        return {
            "type": "text",
            "data": "⚠️ Only English is allowed in this group.",
            "delete_original": True
        }

    return None
