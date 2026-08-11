"""PDF Snipper Bot — Telegram bot that cuts selected pages out of huge PDFs
and returns a high-quality, low-size PDF. Runs as an aiohttp webhook service
(Render free tier friendly) with a JSON health endpoint at `/`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlparse
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import pdf_utils as pdf
import storage

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("pdfbot")

# ---------------------------------------------------------------- config
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")  # https://your-app.onrender.com
SECRET = os.environ.get("WEBHOOK_SECRET", "pdfsnipper-secret")
PORT = int(os.environ.get("PORT", "10000"))
WEBHOOK_PATH = f"/tg/{SECRET}"

MAX_PAGES_PER_JOB = int(os.environ.get("MAX_PAGES_PER_JOB", "250"))
MAX_SOURCE_MB = int(os.environ.get("MAX_SOURCE_MB", "300"))
TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024
KEEPALIVE_MINUTES = int(os.environ.get("KEEPALIVE_MINUTES", "10"))
STARTED_AT = time.time()

sessions: Dict[int, Dict[str, Any]] = {}
pending_admin: Dict[int, str] = {}  # owner_id -> awaited action

URL_RE = re.compile(r"https?://\S+", re.I)
DRIVE_FILE_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")


# ---------------------------------------------------------------- helpers
def is_owner(user_id: int) -> bool:
    return OWNER_ID != 0 and user_id == OWNER_ID


def access_mode() -> str:
    return storage.get_setting("access", "public")  # public | approval


def maintenance() -> bool:
    return storage.get_setting("maintenance", "0") == "1"


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 পেজ কাটা শুরু করুন", callback_data="how")],
            [
                InlineKeyboardButton("⚙️ কোয়ালিটি সেটিং", callback_data="quality"),
                InlineKeyboardButton("📊 আমার হিসাব", callback_data="me"),
            ],
            [InlineKeyboardButton("❓ সাহায্য / লিমিট", callback_data="help")],
        ]
    )


def quality_menu(current: str) -> InlineKeyboardMarkup:
    rows = []
    for key, mode in pdf.MODES.items():
        tick = "✅ " if key == current else ""
        rows.append([InlineKeyboardButton(f"{tick}{mode.label}", callback_data=f"mode:{key}")])
    rows.append([InlineKeyboardButton("⬅️ ফিরে যান", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def file_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚙️ কোয়ালিটি বদলান", callback_data="quality"),
                InlineKeyboardButton("🗑️ বাতিল", callback_data="cancel"),
            ]
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    acc = "🌐 পাবলিক" if access_mode() == "public" else "🔒 অ্যাপ্রুভাল"
    mnt = "🔧 মেইনটেন্যান্স: ON" if maintenance() else "🟢 মেইনটেন্যান্স: OFF"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📈 স্ট্যাটস", callback_data="a:stats"),
                InlineKeyboardButton("👥 ইউজার", callback_data="a:users"),
            ],
            [
                InlineKeyboardButton("📢 ব্রডকাস্ট", callback_data="a:bc"),
                InlineKeyboardButton("🚫 ব্যান / ✅ আনব্যান", callback_data="a:ban"),
            ],
            [
                InlineKeyboardButton(f"অ্যাক্সেস: {acc}", callback_data="a:access"),
                InlineKeyboardButton(mnt, callback_data="a:maint"),
            ],
            [InlineKeyboardButton("♻️ রিফ্রেশ", callback_data="a:home")],
        ]
    )


def user_mode(user_id: int) -> str:
    return storage.get_setting(f"mode:{user_id}", pdf.DEFAULT_MODE)


def cleanup(user_id: int) -> None:
    session = sessions.pop(user_id, None)
    if session and session.get("dir"):
        shutil.rmtree(session["dir"], ignore_errors=True)


async def guard(update: Update) -> bool:
    """Register user + enforce ban/approval/maintenance. True = allowed."""
    user = update.effective_user
    if user is None:
        return False
    row = storage.touch_user(user.id, user.username, user.full_name)
    if row["banned"]:
        await update.effective_message.reply_text("⛔ আপনি এই বট ব্যবহার করতে পারবেন না।")
        return False
    if maintenance() and not is_owner(user.id):
        await update.effective_message.reply_text("🔧 বট এখন মেইনটেন্যান্সে আছে, একটু পরে চেষ্টা করুন।")
        return False
    if access_mode() == "approval" and not row["approved"] and not is_owner(user.id):
        await update.effective_message.reply_text(
            "🔒 এই বট এখন অ্যাপ্রুভাল মোডে। ওউনারের অনুমোদনের জন্য অপেক্ষা করুন।\n"
            f"আপনার আইডি: `{user.id}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        if OWNER_ID:
            try:
                await update.get_bot().send_message(
                    OWNER_ID,
                    f"🔔 নতুন অ্যাক্সেস রিকোয়েস্ট: {user.full_name} (`{user.id}`)\n"
                    f"অনুমোদন: `/approve {user.id}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        return False
    return True


# ---------------------------------------------------------------- commands
WELCOME = (
    "👋 *PDF Snipper Bot*-এ স্বাগতম!\n\n"
    "বিশাল PDF থেকে আপনার দরকারি পৃষ্ঠাগুলো কেটে *হাই কোয়ালিটি + কম সাইজ* PDF বানিয়ে দেবে।\n\n"
    "*কীভাবে:*\n"
    "1️⃣ PDF ফাইল পাঠান (≤ ২০ MB) অথবা ডাইরেক্ট ডাউনলোড লিংক দিন (≤ {maxmb} MB)\n"
    "2️⃣ পেজ রেঞ্জ লিখুন — যেমন `12-40, 55, 90-97`\n"
    "3️⃣ কাটা PDF পেয়ে যান ⚡"
)


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(
        WELCOME.format(maxmb=MAX_SOURCE_MB),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )


def help_text() -> str:
    return (
        "*সাহায্য ও লিমিট*\n\n"
        f"• সোর্স PDF (লিংকে): সর্বোচ্চ *{MAX_SOURCE_MB} MB*\n"
        "• সোর্স PDF (সরাসরি ফাইল): সর্বোচ্চ *২০ MB* (টেলিগ্রামের সীমা)\n"
        f"• এক রিকোয়েস্টে কাটা যাবে সর্বোচ্চ *{MAX_PAGES_PER_JOB} পৃষ্ঠা*\n"
        "• আউটপুট ৫০ MB ছাড়ালে বট অটো কয়েক ভাগে পাঠাবে\n\n"
        "*পেজ ফরম্যাট:* `12-40, 55, 90-97`\n\n"
        "*কোয়ালিটি মোড:*\n"
        "• 🅾️ অরিজিনাল — হুবহু, সাইজ কমে না\n"
        "• ⭐ স্মার্ট — ~৪০% ছোট, লেখা ঝকঝকে (ডিফল্ট)\n"
        "• 🗜️ ম্যাক্স — সবচেয়ে ছোট ফাইল\n\n"
        "কমান্ড: /start /quality /me /cancel /help"
    )


async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(help_text(), parse_mode=ParseMode.MARKDOWN)


async def cmd_quality(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(
        "কোয়ালিটি মোড বেছে নিন:", reply_markup=quality_menu(user_mode(update.effective_user.id))
    )


async def cmd_me(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    row = storage.touch_user(
        update.effective_user.id, update.effective_user.username, update.effective_user.full_name
    )
    await update.message.reply_text(
        f"👤 *{row['name']}*\n🆔 `{row['user_id']}`\n"
        f"📄 মোট জব: {row['jobs']}\n📑 মোট পেজ: {row['pages']}\n"
        f"⚙️ কোয়ালিটি: {pdf.MODES[user_mode(row['user_id'])].label}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    cleanup(update.effective_user.id)
    pending_admin.pop(update.effective_user.id, None)
    await update.message.reply_text("🗑️ বাতিল করা হয়েছে।", reply_markup=main_menu())


# ---------------------------------------------------------------- owner
async def cmd_admin(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text(
        admin_panel_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu()
    )


def admin_panel_text() -> str:
    s = storage.stats()
    saved = max(0, s["in_bytes"] - s["out_bytes"])
    uptime = int(time.time() - STARTED_AT)
    return (
        "🛠️ *ওউনার প্যানেল*\n\n"
        f"👥 ইউজার: *{s['users']}* (২৪ঘ. অ্যাক্টিভ {s['active_24h']}, ব্যান {s['banned']})\n"
        f"📄 জব: *{s['jobs']}* (২৪ঘ. {s['jobs_24h']})\n"
        f"📑 প্রসেস করা পেজ: *{s['pages']}*\n"
        f"💾 সাইজ সাশ্রয়: *{pdf.human_size(saved)}*\n"
        f"⏱️ গড় সময়: *{s['avg_seconds']:.1f}s*\n"
        f"🟢 আপটাইম: *{uptime // 3600}ঘ {uptime % 3600 // 60}মি*"
    )


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id) or not context.args:
        return
    uid = int(context.args[0])
    storage.set_flag(uid, "approved", 1)
    await update.message.reply_text(f"✅ `{uid}` অনুমোদিত।", parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(uid, "✅ আপনার অ্যাক্সেস অনুমোদন করা হয়েছে! /start দিন।")
    except Exception:
        pass


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id) or not context.args:
        return
    uid = int(context.args[0])
    banned = update.message.text.startswith("/ban")
    storage.set_flag(uid, "banned", 1 if banned else 0)
    await update.message.reply_text(("🚫 ব্যান" if banned else "✅ আনব্যান") + f" করা হয়েছে: `{uid}`",
                                    parse_mode=ParseMode.MARKDOWN)


async def do_broadcast(context: ContextTypes.DEFAULT_TYPE, text: str) -> str:
    ok = fail = 0
    for uid in storage.all_user_ids():
        try:
            await context.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    return f"📢 ব্রডকাস্ট শেষ — সফল {ok}, ব্যর্থ {fail}"


# ---------------------------------------------------------------- callbacks
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return  # user tapped the same button twice — nothing to update
    log.exception("handler error", exc_info=err)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data or ""

    if data.startswith("a:"):
        if not is_owner(uid):
            return
        action = data[2:]
        if action == "stats" or action == "home":
            await query.edit_message_text(
                admin_panel_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu()
            )
        elif action == "users":
            rows = storage.recent_users(10)
            body = "\n".join(
                f"{'🚫' if r['banned'] else '✅'} `{r['user_id']}` — {r['name'] or '—'} "
                f"({r['jobs']} জব)"
                for r in rows
            ) or "কোনো ইউজার নেই।"
            await query.edit_message_text(
                "👥 *সাম্প্রতিক ইউজার*\n\n" + body,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_menu(),
            )
        elif action == "bc":
            pending_admin[uid] = "broadcast"
            await query.edit_message_text("📢 ব্রডকাস্ট মেসেজ লিখে পাঠান (বাতিল: /cancel)।")
        elif action == "ban":
            await query.edit_message_text(
                "ব্যবহার করুন:\n`/ban <user_id>`\n`/unban <user_id>`\n`/approve <user_id>`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_menu(),
            )
        elif action == "access":
            storage.set_setting("access", "approval" if access_mode() == "public" else "public")
            await query.edit_message_text(
                admin_panel_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu()
            )
        elif action == "maint":
            storage.set_setting("maintenance", "0" if maintenance() else "1")
            await query.edit_message_text(
                admin_panel_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu()
            )
        return

    if data == "home":
        await query.edit_message_text(
            WELCOME.format(maxmb=MAX_SOURCE_MB),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
    elif data == "help":
        await query.edit_message_text(
            help_text(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ ফিরে যান", callback_data="home")]]
            ),
        )
    elif data == "how":
        await query.edit_message_text(
            "📥 এখন আপনার PDF ফাইলটি পাঠান, অথবা ডাইরেক্ট ডাউনলোড লিংক দিন।",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ ফিরে যান", callback_data="home")]]
            ),
        )
    elif data == "me":
        row = storage.touch_user(uid, query.from_user.username, query.from_user.full_name)
        await query.edit_message_text(
            f"📊 জব: {row['jobs']} | পেজ: {row['pages']}\n⚙️ {pdf.MODES[user_mode(uid)].label}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ ফিরে যান", callback_data="home")]]
            ),
        )
    elif data == "quality":
        await query.edit_message_text("কোয়ালিটি মোড বেছে নিন:", reply_markup=quality_menu(user_mode(uid)))
    elif data.startswith("mode:"):
        key = data.split(":", 1)[1]
        if key in pdf.MODES:
            storage.set_setting(f"mode:{uid}", key)
        session = sessions.get(uid)
        extra = "\n\nএখন পেজ রেঞ্জ লিখুন — যেমন `12-40, 55`" if session else ""
        await query.edit_message_text(
            f"✅ সেট হলো: *{pdf.MODES[key].label}*{extra}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=None if session else quality_menu(key),
        )
    elif data == "cancel":
        cleanup(uid)
        await query.edit_message_text("🗑️ বাতিল করা হয়েছে।", reply_markup=main_menu())


# ---------------------------------------------------------------- pdf intake
async def prepare_source(update: Update, path: str, filename: str, workdir: str) -> None:
    uid = update.effective_user.id
    try:
        total = pdf.page_count(path)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        await update.message.reply_text("❌ ফাইলটি পড়া গেল না — এটি কি সত্যিই PDF?")
        return
    sessions[uid] = {
        "path": path,
        "dir": workdir,
        "pages": total,
        "name": filename,
        "size": os.path.getsize(path),
    }
    await update.message.reply_text(
        f"✅ *{filename}*\n📄 মোট পৃষ্ঠা: *{total}* | 💾 {pdf.human_size(os.path.getsize(path))}\n"
        f"⚙️ কোয়ালিটি: {pdf.MODES[user_mode(uid)].label}\n\n"
        f"এখন পেজ রেঞ্জ লিখুন — যেমন `12-40, 55, 90-97`\n"
        f"(সর্বোচ্চ {MAX_PAGES_PER_JOB} পৃষ্ঠা এক রিকোয়েস্টে)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=file_menu(),
    )


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    doc = update.message.document
    name = doc.file_name or "document.pdf"
    if not name.lower().endswith(".pdf") and doc.mime_type != "application/pdf":
        await update.message.reply_text("❌ শুধু PDF ফাইল সাপোর্ট করে।")
        return
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text(
            "⚠️ টেলিগ্রামে বট সর্বোচ্চ *২০ MB* ফাইল ডাউনলোড করতে পারে।\n"
            f"বড় ফাইলের জন্য ডাইরেক্ট ডাউনলোড লিংক পাঠান (≤ {MAX_SOURCE_MB} MB)।",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    cleanup(update.effective_user.id)
    status = await update.message.reply_text("⬇️ ফাইল ডাউনলোড হচ্ছে…")
    workdir = tempfile.mkdtemp(prefix="pdfbot_")
    path = os.path.join(workdir, "source.pdf")
    tg_file = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(path)
    await status.delete()
    await prepare_source(update, path, name, workdir)


class DownloadError(Exception):
    """A download failure that can be safely shown to the user."""


def direct_download_url(url: str) -> tuple[str, str]:
    """Convert a public Google Drive share URL to its file download endpoint."""
    cleaned = url.rstrip(".,;)>]}\"'")
    parsed = urlparse(cleaned)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}:
        return cleaned, "source.pdf"

    match = DRIVE_FILE_RE.search(parsed.path)
    file_id = match.group(1) if match else parse_qs(parsed.query).get("id", [""])[0]
    if not file_id:
        raise DownloadError("এটি Google Drive-এর ফাইল লিংক নয়")
    return (
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
        f"drive_{file_id[:10]}.pdf",
    )


def response_filename(content_disposition: str, fallback: str) -> str:
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.I)
    plain = re.search(r'filename="?([^";]+)', content_disposition, re.I)
    raw = unquote(encoded.group(1)) if encoded else (plain.group(1) if plain else "")
    if raw:
        try:
            raw = raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    name = os.path.basename(raw).strip() or fallback
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


async def download_url(url: str, dest: str, limit_bytes: int) -> str:
    download_url_value, fallback_name = direct_download_url(url)
    timeout = aiohttp.ClientTimeout(total=600)
    headers = {"User-Agent": "Mozilla/5.0 PDF-Snipper-Bot/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(download_url_value, allow_redirects=True) as resp:
            resp.raise_for_status()
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > limit_bytes:
                raise DownloadError("ফাইলটি নির্ধারিত সাইজের চেয়ে বড়")
            got = 0
            signature = b""
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(1 << 16):
                    got += len(chunk)
                    if got > limit_bytes:
                        raise DownloadError("ফাইলটি নির্ধারিত সাইজের চেয়ে বড়")
                    if len(signature) < 5:
                        signature += chunk[: 5 - len(signature)]
                    fh.write(chunk)
            if not signature.startswith(b"%PDF-"):
                raise DownloadError(
                    "PDF পাওয়া যায়নি—Drive ফাইলটির General access ‘Anyone with the link’ করুন"
                )
            return response_filename(
                resp.headers.get("Content-Disposition", ""), fallback_name
            )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text = (update.message.text or "").strip()

    if pending_admin.get(uid) == "broadcast" and is_owner(uid):
        pending_admin.pop(uid, None)
        note = await update.message.reply_text("📢 পাঠানো হচ্ছে…")
        await note.edit_text(await do_broadcast(context, text))
        return

    if not await guard(update):
        return

    match = URL_RE.search(text)
    if match:
        cleanup(uid)
        status = await update.message.reply_text("⬇️ লিংক থেকে ডাউনলোড হচ্ছে…")
        workdir = tempfile.mkdtemp(prefix="pdfbot_")
        path = os.path.join(workdir, "source.pdf")
        try:
            filename = await download_url(
                match.group(0), path, MAX_SOURCE_MB * 1024 * 1024
            )
        except DownloadError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            await status.edit_text(f"❌ {exc}।")
            return
        except Exception as exc:
            log.warning("URL download failed: %s", exc)
            shutil.rmtree(workdir, ignore_errors=True)
            await status.edit_text(
                "❌ লিংক থেকে ডাউনলোড করা গেল না। লিংকটি পাবলিক কিনা যাচাই করুন।"
            )
            return
        await status.delete()
        await prepare_source(update, path, filename, workdir)
        return

    session = sessions.get(uid)
    if not session:
        await update.message.reply_text(
            "📥 প্রথমে একটি PDF ফাইল বা ডাউনলোড লিংক পাঠান।", reply_markup=main_menu()
        )
        return

    try:
        pages = pdf.parse_ranges(text, session["pages"], MAX_PAGES_PER_JOB)
    except pdf.PdfError as exc:
        await update.message.reply_text(f"⚠️ {exc}", parse_mode=ParseMode.MARKDOWN)
        return

    await run_job(update, context, session, pages)


async def run_job(update: Update, context: ContextTypes.DEFAULT_TYPE, session: Dict[str, Any], pages) -> None:
    uid = update.effective_user.id
    label, count = pdf.summarize(pages)
    mode = user_mode(uid)
    started = time.time()
    status = await update.message.reply_text(
        f"⚙️ কাটা হচ্ছে — *{count}* পৃষ্ঠা (`{label}`)\nমোড: {pdf.MODES[mode].label}",
        parse_mode=ParseMode.MARKDOWN,
    )
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)

    try:
        out = await asyncio.to_thread(
            pdf.build, session["path"], pages, mode, session["dir"], "job"
        )
        parts = await asyncio.to_thread(
            pdf.split_by_size, out, count, TELEGRAM_UPLOAD_LIMIT, session["dir"]
        )
    except Exception:
        log.exception("job failed")
        await status.edit_text("❌ প্রসেস করতে সমস্যা হলো। আবার চেষ্টা করুন বা কম পৃষ্ঠা দিন।")
        return

    elapsed = time.time() - started
    total_out = sum(os.path.getsize(p) for p in parts)
    base = os.path.splitext(session["name"])[0][:40]
    await status.edit_text(
        f"📤 আপলোড হচ্ছে… ({pdf.human_size(total_out)}"
        + (f", {len(parts)} ভাগ" if len(parts) > 1 else "")
        + ")"
    )
    for i, part in enumerate(parts, 1):
        suffix = f"_part{i}" if len(parts) > 1 else ""
        with open(part, "rb") as fh:
            await context.bot.send_document(
                update.effective_chat.id,
                InputFile(fh, filename=f"{base}_p{label.replace(' ', '')}{suffix}.pdf"),
                caption=(
                    f"✅ *{count}* পৃষ্ঠা | `{label}`\n"
                    f"💾 {pdf.human_size(os.path.getsize(part))} | ⏱️ {elapsed:.1f}s\n"
                    f"⚙️ {pdf.MODES[mode].label}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
    await status.delete()
    storage.record_job(uid, count, mode, session["size"], total_out, elapsed)
    await update.message.reply_text(
        "আরও পেজ লাগবে? একই ফাইল থেকে নতুন রেঞ্জ লিখুন, বা নতুন PDF পাঠান।",
        reply_markup=file_menu(),
    )


# ---------------------------------------------------------------- web app
def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("quality", cmd_quality))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler(["ban", "unban"], cmd_ban))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def health_payload() -> Dict[str, Any]:
    up = int(time.time() - STARTED_AT)
    return {
        "ok": True,
        "alive": True,
        "status": "true",
        "service": "pdf-snipper-bot",
        "uptime_seconds": up,
        "max_pages_per_job": MAX_PAGES_PER_JOB,
        "max_source_mb": MAX_SOURCE_MB,
    }


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response(health_payload())


async def keep_alive() -> None:
    if not BASE_URL or KEEPALIVE_MINUTES <= 0:
        return
    await asyncio.sleep(60)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"{BASE_URL}/healthz", timeout=aiohttp.ClientTimeout(total=30)):
                    pass
            except Exception:
                pass
            await asyncio.sleep(KEEPALIVE_MINUTES * 60)


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN environment variable is required")
    storage.init()
    application = build_application()

    async def handle_webhook(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400)
        await application.update_queue.put(Update.de_json(data, application.bot))
        return web.json_response({"ok": True})

    web_app = web.Application(client_max_size=64 * 1024 * 1024)
    web_app.add_routes(
        [
            web.get("/", handle_health),
            web.get("/healthz", handle_health),
            web.get("/ping", handle_health),
            web.head("/", lambda _r: web.Response(status=200)),
            web.post(WEBHOOK_PATH, handle_webhook),
        ]
    )

    await application.initialize()
    await application.start()
    if BASE_URL:
        await application.bot.set_webhook(
            url=f"{BASE_URL}{WEBHOOK_PATH}",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        log.info("Webhook set to %s%s", BASE_URL, WEBHOOK_PATH)
    else:
        log.warning("BASE_URL not set — webhook not registered")

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("HTTP server listening on :%s", PORT)
    asyncio.create_task(keep_alive())

    if OWNER_ID:
        try:
            await application.bot.send_message(OWNER_ID, "🟢 বট চালু হয়েছে। /admin")
        except Exception:
            pass

    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        log.info("shutdown: %s", exc)