import os
import json
import re
import datetime
import smtplib
import feedparser
import requests
import certifi
from datetime import timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv
import groq as groq_module


load_dotenv()

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
NOTION_API_KEY     = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


SEEN_URLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_urls.json")

FEEDS = {
    "Finance": [
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "https://feeds.a.dj.com/rss/RSSWSJD.xml",
        "https://feeds.a.dj.com/rss/RSSOpinion.xml",
        "https://feeds.bloomberg.com/markets/news.rss",
        "https://www.ft.com/rss/home",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.cnbc.com/id/10001147/device/rss/rss.html",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "Geopolitics": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.dw.com/rdf/rss-en-all",
        "https://feeds.npr.org/1004/rss.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
    ],
    "Tech": [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.wired.com/wired/index",
        "https://hnrss.org/frontpage",
        "https://feeds.arstechnica.com/arstechnica/index",
        "https://www.technologyreview.com/feed/",
    ],
    "Venture": [
        "https://techcrunch.com/category/venture/feed/",
        "https://news.crunchbase.com/feed/",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    ],
    "AI Commerce": [
        "https://retaildive.com/feeds/news/",
        "https://www.modernretail.co/feed/",
        "https://techcrunch.com/feed/",
    ],
    "AI & LLMs": [
        "https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml",
        "https://huggingface.co/blog/feed.xml",
        "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "https://venturebeat.com/category/ai/feed/",
        "https://www.theverge.com/rss/index.xml",
    ],
    "Jobs & Hiring": [
        "https://www.businessinsider.com/rss",
        "https://techcrunch.com/category/startups/feed/",
        "https://feeds.reuters.com/reuters/technologyNews",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],
    "Energy & Climate": [
        "https://www.canarymedia.com/articles.rss",
        "https://electrek.co/feed/",
        "https://feeds.reuters.com/reuters/environment",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    ],
    "US Policy": [
        "https://rss.politico.com/politics-news.xml",
        "https://thehill.com/rss/syndicator/19109",
        "https://feeds.npr.org/1014/rss.xml",
    ],
    "India & Emerging Markets": [
        "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
        "https://www.livemint.com/rss/news",
        "https://www.thehindu.com/feeder/default.rss",
    ],
    "Healthcare & Biotech": [
        "https://www.statnews.com/feed/",
        "https://medcitynews.com/feed/",
        "https://www.healthcaredive.com/feeds/news/",
        "https://feeds.bbci.co.uk/news/health/rss.xml",
    ],
    "Pop Culture & Creator": [
        "https://www.theverge.com/rss/index.xml",
        "https://techcrunch.com/feed/",
        "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
        "https://variety.com/feed/",
    ],
    "Funded & Hiring": [
        "https://techcrunch.com/category/venture/feed/",
        "https://news.crunchbase.com/feed/",
        "https://techcrunch.com/category/startups/feed/",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
}

ARTICLES_PER_CATEGORY = 15

SYSTEM_PROMPT = (
    "You are a sharp analyst writing a daily briefing for Ipshita — a senior MBA candidate "
    "(Kelley School of Business, graduating May 2026) with 5+ years in fintech and enterprise "
    "software (Credit Suisse/UBS, post-trade settlement, senior Product Manager), actively "
    "recruiting for Senior PM and Strategy roles at tech companies on the US West Coast. "
    "She requires F-1 OPT sponsorship. She has deep interest in AI products, productivity, "
    "product management, retail investing, personal finance, jobs and hiring trends for "
    "MBAs/Masters, private equity, India markets, GPU/datacenter infrastructure, F1-OPT visa, "
    "h1b, entrepreneurship, fitness, healthy eating, AI tools, building with AI.\n\n"

    "FORMAT — Every story object MUST have ALL of these exact keys — no exceptions:\n"
    "  {\n"
    "    \"what_happened\": \"one crisp sentence — what occurred, plain English, no jargon\",\n"
    "    \"why_it_matters\": \"one analytical sentence — the trend or structural shift this signals\",\n"
    "    \"so_what\": \"one sharp sentence specific to Ipshita — fintech, AI products, West Coast PM "
    "recruiting, visa/OPT, India markets, or private equity. If you cannot write a genuinely "
    "specific so_what for her, SKIP the story entirely.\",\n"
    "    \"source_url\": \"exact article URL\",\n"
    "    \"source_name\": \"short publication name\"\n"
    "  }\n"
    "NEVER use a 'text' field. NEVER omit what_happened, why_it_matters, or so_what.\n\n"

    "DEDUPLICATION — Critical rule: each story must appear in exactly ONE section only. "
    "If a story fits multiple sections, put it in the most relevant one and skip it elsewhere. "
    "Never repeat the same story or URL across sections.\n\n"

    "STORY COUNTS per section (strict — do not exceed or fall short):\n"
    "  - ai_llms: 4-5 stories. Prioritize: new AI tools to build with, LLM releases, agent "
    "frameworks, GPU demand, AI infrastructure, datacenter buildout, AI for productivity, "
    "hottest AI trends.\n"
    "  - jobs_hiring: 4-5 stories. Prioritize: tech/AI/fintech layoffs, hiring freezes, "
    "role demand signals, skills needed to get hired, MBA jobs, F1-OPT/H1B visa impacts on "
    "hiring, PM/strategy job market on West Coast.\n"
    "  - funded_hiring: 5-7 companies. ONLY Series A/B, Series C+, or PE/Growth Equity deals "
    "in AI, Fintech, Enterprise SaaS, Healthcare, or Climate/Energy. For each: what they build, "
    "amount raised, funding stage, lead investor if known, and whether they are likely hiring "
    "Senior PMs, Strategy, or TPM roles. Skip seed rounds and consumer apps entirely.\n"
    "  - venture_radar: 3 stories. VC rounds AND private equity deals, buyouts, growth equity. "
    "Flag fintech, enterprise SaaS, or AI deals. Include any major deals across sectors.\n"
    "  - money_talk: 3 stories. Macro moves, markets, regulation, banking, major financial moves.\n"
    "  - world_lore: 3 stories. Most geopolitically significant stories only — pick the top 3.\n"
    "  - tech_tea: 3 stories. Silicon Valley news, enterprise software, platform wars, developer "
    "tools, anything major or trending in tech.\n"
    "  - india_emerging: 3 stories. India fintech, startup ecosystem, economic policy, stories "
    "that impact families in India, government schemes for entrepreneurs.\n"
    "  - us_policy: 2 stories. Most impactful for tech/AI/fintech/F1-OPT/H1B visa industries.\n"
    "  - energy_climate: 2 stories. AI datacenter power demand, EV adoption, grid infrastructure. "
    "Skip pure climate policy with no tech angle.\n"
    "  - commerce_pulse: 2 stories. AI in retail or enterprise commerce only.\n"
    "  - health_biotech: 2 stories. Drug approvals, digital health, biotech funding.\n"
    "  - pop_culture: 2 stories. Creator economy, platform business model shifts, social media "
    "trends. Skip movie reviews and celebrity gossip.\n\n"

    "RULES:\n"
    "- Each story appears in exactly ONE section — no duplicates across sections.\n"
    "- Skip any story where you cannot write a genuinely specific so_what for Ipshita.\n"
    "- Never use: this highlights, it is worth noting, this underscores, in conclusion, "
    "notably, it's worth mentioning.\n"
    "- Flag career signals (layoffs, hiring, PM role demand, visa implications) in so_what.\n"
    "- GPU, datacenter, and AI infrastructure stories are highest priority.\n"
    "- Write like a sharp analyst — direct, clear, analytically grounded, occasionally wry.\n\n"

    "Return ONLY valid JSON. No markdown. No code blocks.\n"
    "Top-level keys: money_talk, world_lore, tech_tea, venture_radar, commerce_pulse, "
    "ai_llms, jobs_hiring, energy_climate, us_policy, india_emerging, health_biotech, "
    "pop_culture, funded_hiring, speed_round.\n\n"
    "Each key except speed_round is an array of story objects with the exact format above.\n"
    "speed_round: array of 6-8 objects each with: text, source_url.\n\n"
    "No markdown. No code blocks. Just the JSON."
)

# Maps digest keys → (Notion category label, email section title, hex color)
SECTION_META = {
    "money_talk":      ("Finance",                  "💰 Money Talk",               "#f59e0b"),
    "world_lore":      ("Geopolitics",              "🌍 World Lore",               "#0d9488"),
    "tech_tea":        ("Tech",                     "⚡ Tech Tea",                 "#7c3aed"),
    "venture_radar":   ("Venture",                  "🚀 Venture Radar",            "#2563eb"),
    "funded_hiring":   ("Funded & Hiring",          "💸 Funded & Hiring",          "#059669"),
    "commerce_pulse":  ("AI Commerce",              "🛍️ Commerce Pulse",          "#16a34a"),
    "ai_llms":         ("AI & LLMs",                "🤖 AI & LLMs",               "#dc2626"),
    "jobs_hiring":     ("Jobs & Hiring",            "💼 Jobs & Hiring Intel",      "#0891b2"),
    "energy_climate":  ("Energy & Climate",         "🌱 Energy & Climate",         "#65a30d"),
    "us_policy":       ("US Policy",                "🏛️ US Policy & Regulation",  "#6d28d9"),
    "india_emerging":  ("India & Emerging Markets", "🌏 India & Emerging Markets", "#ea580c"),
    "health_biotech":  ("Healthcare & Biotech",     "🏥 Healthcare & Biotech",     "#be185d"),
    "pop_culture":     ("Pop Culture & Creator",    "🎭 Pop Culture & Creator",    "#8b5cf6"),
}


# ── Seen-URL persistence ──────────────────────────────────────────────────────

def load_seen_urls():
    try:
        # Auto-expire: if file is older than 3 days, start fresh
        if os.path.exists(SEEN_URLS_FILE):
            file_age_days = (
                datetime.datetime.now().timestamp() -
                os.path.getmtime(SEEN_URLS_FILE)
            ) / 86400
            if file_age_days > 3:
                print(f"  [seen_urls] File is {file_age_days:.1f} days old — resetting.")
                return set()
        with open(SEEN_URLS_FILE, "r") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen_urls(seen: set):
    with open(SEEN_URLS_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


# ── RSS fetching ──────────────────────────────────────────────────────────────

def is_fresh(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return False
    pub_dt = datetime.datetime(*parsed[:6], tzinfo=timezone.utc)
    cutoff = datetime.datetime.now(timezone.utc) - timedelta(hours=30)
    return pub_dt >= cutoff


def fetch_articles(category, feed_urls, limit=ARTICLES_PER_CATEGORY):
    collected = []
    total_seen = 0
    fresh_count = 0

    for url in feed_urls:
        if len(collected) >= limit:
            break
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; market-intel-bot/1.0)"},
                timeout=15,
                verify=certifi.where(),
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                print(f"  [skip] No entries in feed: {url}")
                continue
            for entry in feed.entries:
                if len(collected) >= limit:
                    break
                total_seen += 1
                if not is_fresh(entry):
                    continue
                fresh_count += 1
                title   = entry.get("title", "").strip()
                link    = entry.get("link", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                summary = re.sub(r"<[^>]+>", "", summary)[:500]
                if title and link:
                    collected.append({
                        "title": title,
                        "url": link,
                        "summary": summary,
                        "category": category,
                    })
        except Exception as e:
            print(f"  [skip] Error fetching {url}: {e}")

    return collected, total_seen, fresh_count


# ── Gemini ────────────────────────────────────────────────────────────────────

def build_prompt(all_articles):
    lines = []
    for a in all_articles:
        lines.append(
            f"Category: {a['category']}\n"
            f"Title: {a['title']}\n"
            f"URL: {a['url']}\n"
            f"Summary: {a['summary']}\n"
        )
    return "Here are today's articles:\n\n" + "\n---\n".join(lines)


@retry(
    retry=retry_if_exception_type(ServerError),
    wait=wait_exponential(multiplier=1, min=10, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)

GEMINI_MODELS = [
    "models/gemini-2.5-flash",
    "models/gemini-2.0-flash",
    "models/gemini-2.0-flash-lite",
]

def call_gemini(prompt):
    # --- Gemini cascade ---
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    
    for model in GEMINI_MODELS:
        try:
            response = gemini_client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                ),
            )
            if model != GEMINI_MODELS[0]:
                print(f"[Fallback] Gemini used {model}")
            return response.text
        
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                print(f"[Gemini] {model} unavailable, trying next...")
                continue
            else:
                raise  # non-503 → surface immediately

    # --- Groq fallback ---
    print("[Fallback] All Gemini models down. Trying Groq llama-3.3-70b...")
    try:
        groq_client = groq_module.Groq(api_key=GROQ_API_KEY)
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
        )
        print("[Fallback] Groq succeeded.")
        return response.choices[0].message.content
    
    except Exception as e:
        raise RuntimeError(f"All models failed. Last Groq error: {e}")


# ── Notion ────────────────────────────────────────────────────────────────────

def section_to_text(stories):
    if isinstance(stories, str):
        return stories
    parts = []
    for s in stories:
        what    = s.get("what_happened", s.get("text", ""))
        why     = s.get("why_it_matters", "")
        so_what = s.get("so_what", "")
        url     = s.get("source_url", "")
        name    = s.get("source_name", "")
        label   = f" [{name}]" if name else ""
        block   = (
            f"What happened: {what}\n"
            f"Why it matters: {why}\n"
            f"So what for you: {so_what}{label} {url}"
        )
        parts.append(block.strip())
    return "\n\n".join(parts)[:2000]


def push_section_to_notion(section_key, stories, today):
    category_label, section_title, _ = SECTION_META[section_key]
    content = section_to_text(stories)
    first_url = None
    if isinstance(stories, list) and stories:
        first_url = stories[0].get("source_url") or None

    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Headline": {
                "title": [{"text": {"content": section_title}}]
            },
            "Category": {
                "select": {"name": category_label}
            },
            "Insight": {
                "rich_text": [{"text": {"content": content}}]
            },
            "Source": {
                "url": first_url
            },
            "Date 1": {
                "date": {"start": today}
            },
        },
    }
    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


# ── Email ─────────────────────────────────────────────────────────────────────

def build_html_email(digest, today, total_fetched, total_pushed):
    date_display = datetime.date.fromisoformat(today).strftime("%A, %B %-d, %Y")

    def story_cards(stories):
        if not stories or not isinstance(stories, list):
            return '<p style="color:#6b7280;font-size:14px;margin:0;">No stories available.</p>'
        html = ""
        for i, story in enumerate(stories):
            what_happened  = story.get("what_happened", story.get("text", ""))
            why_it_matters = story.get("why_it_matters", "")
            so_what        = story.get("so_what", "")
            source_url     = story.get("source_url", "")
            source_name    = story.get("source_name", "Source")
            is_last = i == len(stories) - 1
            divider = "" if is_last else (
                '<div style="border-top:1px solid #f0f0f0;margin:0 20px;"></div>'
            )
            link_html = (
                f'<a href="{source_url}" '
                f'style="color:#6b7280;font-size:12px;text-decoration:none;'
                f'font-weight:500;letter-spacing:0.2px;">'
                f'{source_name} →</a>'
            ) if source_url else ""

            story_html = ""
            if what_happened:
                story_html += (
                    f'<p style="margin:0 0 6px 0;font-size:14px;line-height:1.65;color:#1f2937;">'
                    f'<strong style="color:#111827;">What happened:</strong> {what_happened}</p>'
                )
            if why_it_matters:
                story_html += (
                    f'<p style="margin:0 0 6px 0;font-size:14px;line-height:1.65;color:#374151;">'
                    f'<strong style="color:#111827;">Why it matters:</strong> {why_it_matters}</p>'
                )
            if so_what:
                story_html += (
                    f'<p style="margin:0 0 10px 0;font-size:14px;line-height:1.65;color:#111827;'
                    f'background:#fefce8;padding:8px 12px;border-radius:4px;'
                    f'border-left:3px solid #f59e0b;">'
                    f'<strong>So what for you:</strong> {so_what}</p>'
                )

            html += f"""
            <div style="padding:16px 20px;">
              {story_html}
              <div style="text-align:right;">{link_html}</div>
            </div>
            {divider}"""
        return html

    def section_card(key):
        _, title, color = SECTION_META[key]
        stories = digest.get(key, [])
        inner = story_cards(stories)
        return f"""
        <div style="margin-bottom:20px;border-radius:8px;overflow:hidden;
                    box-shadow:0 1px 4px rgba(0,0,0,0.08);">
          <div style="background:{color};padding:12px 20px;">
            <span style="color:#fff;font-size:16px;font-weight:700;
                         letter-spacing:0.3px;">{title}</span>
          </div>
          <div style="background:#ffffff;">
            {inner}
          </div>
        </div>"""

    def speed_round_html(items):
        if not items or not isinstance(items, list):
            return ""
        bullets = ""
        for item in items:
            if isinstance(item, dict):
                text = item.get("text", "")
                url  = item.get("source_url", "")
                if url:
                    bullets += (
                        f'<li style="margin-bottom:10px;color:#374151;font-size:14px;">'
                        f'{text} '
                        f'<a href="{url}" style="color:#9ca3af;text-decoration:none;'
                        f'font-size:12px;font-weight:500;">↗</a></li>'
                    )
                else:
                    bullets += (
                        f'<li style="margin-bottom:10px;color:#374151;font-size:14px;">'
                        f'{text}</li>'
                    )
            else:
                bullets += (
                    f'<li style="margin-bottom:10px;color:#374151;font-size:14px;">'
                    f'{item}</li>'
                )
        return bullets

    sections_html = "".join(section_card(k) for k in SECTION_META)
    speed_bullets  = speed_round_html(digest.get("speed_round", []))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Ipshita's Morning Intel</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:620px;margin:32px auto;padding:0 16px 32px;">

    <!-- Header -->
    <div style="background:#1a1a1a;border-radius:10px 10px 0 0;padding:28px 28px 22px;">
      <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:800;
                 letter-spacing:-0.3px;">Ipshita's Morning Intel</h1>
      <p style="margin:6px 0 0;color:#9ca3af;font-size:14px;">{date_display}</p>
    </div>

    <!-- Body -->
    <div style="background:#f3f4f6;padding:20px 0;">
      {sections_html}

      <!-- Speed Round -->
      <div style="background:#ffffff;border-radius:8px;overflow:hidden;
                  box-shadow:0 1px 4px rgba(0,0,0,0.08);margin-bottom:20px;">
        <div style="background:#1a1a1a;padding:12px 20px;">
          <span style="color:#fff;font-size:16px;font-weight:700;">⚡ Speed Round</span>
        </div>
        <div style="padding:16px 20px;">
          <ul style="margin:0;padding-left:20px;line-height:1.65;">
            {speed_bullets}
          </ul>
        </div>
      </div>
    </div>

    <!-- Footer -->
    <div style="text-align:center;padding:12px 0;
                font-size:12px;color:#9ca3af;line-height:1.6;">
      {total_fetched} articles fetched &nbsp;·&nbsp; {total_pushed} sections in today's digest
    </div>

  </div>
</body>
</html>"""


def send_email(html, today, total_fetched, total_pushed):
    date_display = datetime.date.fromisoformat(today).strftime("%B %-d, %Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Morning Intel — {date_display}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = GMAIL_ADDRESS
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = datetime.date.today().isoformat()

    # Step 1: Fetch + freshness filter
    print("Fetching articles from RSS feeds...\n")
    all_articles = []
    grand_total_seen  = 0
    grand_total_fresh = 0

    for category, feed_urls in FEEDS.items():
        print(f"[{category}]")
        articles, total_seen, fresh_count = fetch_articles(category, feed_urls)
        grand_total_seen  += total_seen
        grand_total_fresh += fresh_count
        if not articles:
            print(f"  [warn] No fresh articles in last 24h for {category}")
        else:
            print(f"  {fresh_count}/{total_seen} passed 24h filter → kept {len(articles)}")
        all_articles.extend(articles)

    print(f"\nFreshness: {grand_total_fresh}/{grand_total_seen} articles passed the 24h filter")

    # Step 2: Deduplication against seen_urls.json
    seen_urls = load_seen_urls()
    before_dedup = len(all_articles)
    all_articles = [a for a in all_articles if a["url"] not in seen_urls]
    dupes_removed = before_dedup - len(all_articles)
    total_fetched = len(all_articles)

    print(f"Dedup: removed {dupes_removed} already-seen URLs → {total_fetched} new articles\n")

    if not all_articles:
        print("No new articles to process. Exiting.")
        return

    # Step 3: Send to Gemini
    print("Sending articles to Gemini for analysis...")
    prompt = build_prompt(all_articles)
    raw_response = call_gemini(prompt)

    # Step 4: Parse JSON response
    try:
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])
        digest = json.loads(cleaned)
        if not isinstance(digest, dict):
            raise ValueError("Expected a JSON object at the top level.")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"\n[error] Gemini returned invalid JSON: {e}")
        print("--- Raw Gemini response ---")
        print(raw_response)
        return

    print("Digest generated.\n")

    # Step 5: Push each section to Notion
    print("Pushing sections to Notion...")
    pushed = 0
    for key in SECTION_META:
        stories = digest.get(key, [])
        if not stories:
            print(f"  [skip] No content for {key}")
            continue
        try:
            push_section_to_notion(key, stories, today)
            pushed += 1
            _, title, _ = SECTION_META[key]
            print(f"  [ok] {title}")
        except requests.HTTPError as e:
            print(f"  [error] Notion push failed for {key}: {e.response.text}")
        except Exception as e:
            print(f"  [error] Unexpected error for {key}: {e}")

    # Step 6: Persist seen URLs
    new_urls = {a["url"] for a in all_articles}
    seen_urls.update(new_urls)
    save_seen_urls(seen_urls)
    print(f"\n  Saved {len(new_urls)} URLs to seen_urls.json (total: {len(seen_urls)})")

    # Step 7: Send email
    print("\nSending email...")
    try:
        html = build_html_email(digest, today, total_fetched, pushed)
        send_email(html, today, total_fetched, pushed)
        print(f"  [ok] Email sent to {GMAIL_ADDRESS}")
    except Exception as e:
        print(f"  [error] Email failed: {e}")

    # Step 8: Summary
    print(f"\n--- Summary ---")
    print(f"Articles seen     : {grand_total_seen}")
    print(f"Passed 24h filter : {grand_total_fresh}")
    print(f"New (not dupes)   : {total_fetched}")
    print(f"Sections in digest: {len(SECTION_META)}")
    print(f"Pushed to Notion  : {pushed}")


if __name__ == "__main__":
    main()
