import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "PDF Snipper Bot — বড় PDF থেকে দরকারি পৃষ্ঠা কাটুন" },
      {
        name: "description",
        content:
          "টেলিগ্রাম বট যা ৫০০+ পৃষ্ঠার PDF থেকে বাছাই করা পৃষ্ঠা কেটে হাই কোয়ালিটি ও কম সাইজের PDF বানায়। Render-এ ফ্রি ডিপ্লয়যোগ্য।",
      },
      { property: "og:title", content: "PDF Snipper Bot — পেজ কেটে ছোট PDF" },
      {
        property: "og:description",
        content: "বড় PDF থেকে ৩০-৫০ পৃষ্ঠা কেটে ঝকঝকে, হালকা PDF — টেলিগ্রামেই।",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

const steps = [
  { n: "১", t: "PDF পাঠান", d: "ফাইল (≤২০MB) অথবা ডাউনলোড লিংক (≤৩০০MB)" },
  { n: "২", t: "পেজ রেঞ্জ দিন", d: "যেমন 12-40, 55, 90-97 — সর্বোচ্চ ২৫০ পৃষ্ঠা" },
  { n: "৩", t: "PDF বুঝে নিন", d: "হাই কোয়ালিটি, ~৪০% ছোট ফাইল সাইজ" },
];

const features = [
  ["⭐", "তিন কোয়ালিটি মোড", "অরিজিনাল / স্মার্ট / ম্যাক্স কম্প্রেস"],
  ["🛠️", "ওউনার প্যানেল", "স্ট্যাটস, ইউজার, ব্রডকাস্ট, ব্যান, অ্যাক্সেস মোড"],
  ["🟢", "অলওয়েজ অন", "/ এন্ডপয়েন্টে JSON true — ping দিলেই জেগে থাকে"],
  ["📦", "অটো স্প্লিট", "আউটপুট ৪৯MB ছাড়ালে কয়েক ভাগে পাঠায়"],
];

function Index() {
  return (
    <main className="min-h-screen bg-background px-5 py-14 text-foreground">
      <div className="mx-auto max-w-3xl">
        <span className="inline-block rounded-full border border-border px-3 py-1 text-xs text-muted-foreground">
          Telegram Bot · Render free deploy
        </span>
        <h1 className="mt-5 text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
          PDF Snipper Bot
        </h1>
        <p className="mt-4 text-base text-muted-foreground">
          ৫০০ পৃষ্ঠার PDF থেকে আপনার দরকারি ৩০–৫০ পৃষ্ঠা কেটে ঝকঝকে অথচ হালকা PDF — সরাসরি
          টেলিগ্রামে।
        </p>

        <div className="mt-10 grid gap-3 sm:grid-cols-3">
          {steps.map((s) => (
            <div key={s.n} className="rounded-xl border border-border bg-card p-4">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-sm font-semibold text-primary-foreground">
                {s.n}
              </div>
              <h2 className="mt-3 font-semibold">{s.t}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{s.d}</p>
            </div>
          ))}
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {features.map(([icon, title, desc]) => (
            <div key={title} className="rounded-xl border border-border bg-card p-4">
              <p className="text-lg">{icon}</p>
              <h3 className="mt-1 font-medium">{title}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{desc}</p>
            </div>
          ))}
        </div>

        <div className="mt-8 rounded-xl border border-border bg-muted/40 p-5">
          <h2 className="font-semibold">হেলথ এন্ডপয়েন্ট</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            ডিপ্লয়ের পর <code className="rounded bg-background px-1">/</code> বা{" "}
            <code className="rounded bg-background px-1">/healthz</code> এ ক্লিক করলেই:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-lg bg-background p-3 text-xs">
            {`{ "ok": true, "alive": true, "status": "true" }`}
          </pre>
          <p className="mt-3 text-sm text-muted-foreground">
            সোর্স কোড ও ধাপে ধাপে গাইড রিপোর <code className="rounded bg-background px-1">bot/</code>{" "}
            ফোল্ডারে।
          </p>
        </div>
      </div>
    </main>
  );
}
