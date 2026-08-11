# PDF Snipper Bot — Render ডিপ্লয় গাইড

বিশাল PDF থেকে দরকারি পৃষ্ঠাগুলো কেটে **হাই কোয়ালিটি + কম সাইজ** PDF বানিয়ে দেয়।

## ১. বট তৈরি
টেলিগ্রামে [@BotFather](https://t.me/BotFather) → `/newbot` → টোকেন কপি করুন।
নিজের সংখ্যা-আইডি নিন [@userinfobot](https://t.me/userinfobot) থেকে (এটাই `OWNER_ID`)।

## ২. Render-এ ডিপ্লয়
1. এই `bot/` ফোল্ডারটি একটি GitHub রিপোতে দিন।
2. Render → **New → Web Service** → রিপো সিলেক্ট → Runtime **Python**, Root Directory `bot`।
3. Build: `pip install -r requirements.txt` | Start: `python main.py`
4. Environment variables:

| Key | Value |
|---|---|
| `BOT_TOKEN` | BotFather টোকেন |
| `OWNER_ID` | আপনার টেলিগ্রাম আইডি |
| `BASE_URL` | `https://<your-app>.onrender.com` (প্রথম ডিপ্লয়ের পর সেট করে redeploy) |
| `WEBHOOK_SECRET` | যেকোনো র‍্যান্ডম স্ট্রিং |
| `MAX_PAGES_PER_JOB` | `250` |
| `MAX_SOURCE_MB` | `300` |

## ৩. অলওয়েজ-অন (JSON `true`)
ডিপ্লয়ের পর এই লিংকে ক্লিক করলেই JSON দেখাবে:

```
https://<your-app>.onrender.com/
```
```json
{"ok": true, "alive": true, "status": "true", "service": "pdf-snipper-bot", "uptime_seconds": 123}
```

একই আউটপুট `/healthz` আর `/ping` এও আছে। এই URL টা
[UptimeRobot](https://uptimerobot.com) / cron-job.org-এ ৫–১০ মিনিট ইন্টারভালে মনিটর দিলে
Render free সার্ভিস কখনো ঘুমাবে না। এছাড়া বটের ভেতরেই বিল্ট-ইন self keep-alive চলে
(`KEEPALIVE_MINUTES`)।

## ৪. লিমিট
- সরাসরি ফাইল: **২০ MB** (টেলিগ্রাম বট API-র সীমা)
- ডাউনলোড লিংক: **৩০০ MB** পর্যন্ত
- **এক রিকোয়েস্টে সর্বোচ্চ ২৫০ পৃষ্ঠা** (Render free 512 MB RAM অনুযায়ী নিরাপদ সীমা)
- আউটপুট ৪৯ MB ছাড়ালে অটো কয়েক ভাগে পাঠায়

## ৫. কমান্ড
ইউজার: `/start` `/quality` `/me` `/cancel` `/help`
ওউনার: `/admin` `/approve <id>` `/ban <id>` `/unban <id>`

ওউনার প্যানেলে: স্ট্যাটস, ইউজার লিস্ট, ব্রডকাস্ট, ব্যান/আনব্যান,
পাবলিক↔অ্যাপ্রুভাল অ্যাক্সেস মোড, মেইনটেন্যান্স টগল।