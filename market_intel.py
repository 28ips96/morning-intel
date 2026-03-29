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

load_dotenv()

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
NOTION_API_KEY     = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

SEEN_URLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_urls.json")

FEEDS = {
    "Finance": [
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://feeds.bloomberg.com/markets/news.rss",
        "https://www.ft.com/rss/home",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.cnbc.com/id/10001147/device/rss/rss.html",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
    ],
    "Geopolitics": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.dw.com/rdf/rss-en-all",
        "https://feeds.npr.org/1004/rss.xml",
    ],
    "Tech": [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.wired.com/wired/index",
        "https://www.theverge.com/rss/index.xml",
        "https://hnrss.org/frontpage",
    ],
    "Venture": [
        "https://techcrunch.com/category/venture/feed/",
    ],
    "AI Commerce": [
        "https://retaildive.com/feeds/news/",
        "https://www.modernretail.co/feed/",
    ],
}

ARTICLES_PER_CATEGORY = 10  # fetch more to survive freshness + dedup filters

SYSTEM_PROMPT = (
    "You are a sharp analyst writing for a smart MBA student who wants to understand "
    "what is actually happening in business and why it matters.\n\n"
    "For each story you include, your insight must have three parts — write them as one "
    "flowing paragraph, not as labeled sections:\n\n"
    "First sentence: what actually happened, in plain English. No jargon.\n\n"
    "Second sentence: why it happened or what it signals about a bigger trend.\n\n"
    "Third sentence: the so-what — what this means for someone working in AI, enterprise "
    "software, or retail. Make this concrete and slightly dry-funny if the situation calls for it.\n\n"
    "Rules:\n"
    "- If a story is genuinely irrelevant to business, tech, finance, geopolitics or commerce — "
    "skip it entirely. Do not include blog posts, lifestyle content, or anything from unknown sources.\n"
    "- Never use phrases like: this highlights, it is worth noting, this underscores, "
    "in conclusion, notably.\n"
    "- Write like a smart friend explaining the news over coffee — direct, clear, occasionally wry.\n\n"
    "Return ONLY valid JSON. No markdown. No code blocks. Same structure as before: "
    "keys are money_talk, world_lore, tech_tea, venture_radar, commerce_pulse, speed_round. "
    "Each section key except speed_round must be an array of story objects with exactly these keys:\n"
    "  - text: the three-sentence paragraph described above.\n"
    "  - source_url: the exact URL of the article this story is based on.\n"
    "  - source_name: short publication name (e.g. 'WSJ', 'BBC', 'TechCrunch').\n\n"
    "Include 2 to 3 story objects per section. "
    "speed_round must be an array of 4 to 5 objects, each with:\n"
    "  - text: one punchy sentence.\n"
    "  - source_url: the article URL.\n\n"
    "No markdown. No code blocks. Just the JSON."
)

# Maps digest keys → (Notion category label, email section title, hex color)
SECTION_META = {
    "money_talk":     ("Finance",     "💰 Money Talk",      "#f59e0b"),
    "world_lore":     ("Geopolitics", "🌍 World Lore",       "#0d9488"),
    "tech_tea":       ("Tech",        "⚡ Tech Tea",          "#7c3aed"),
    "venture_radar":  ("Venture",     "🚀 Venture Radar",    "#2563eb"),
    "commerce_pulse": ("AI Commerce", "🛍️ Commerce Pulse",  "#16a34a"),
}


# ── Seen-URL persistence ──────────────────────────────────────────────────────

def load_seen_urls():
    try:
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
    """Return True if the entry was published within the last 24 hours."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return False  # no date → skip
    pub_dt = datetime.datetime(*parsed[:6], tzinfo=timezone.utc)
    cutoff = datetime.datetime.now(timezone.utc) - timedelta(hours=24)
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
def call_gemini(prompt):
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-1.5-flash-001",          # pinned, off volatile -latest
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    return response.text


# ── Notion ────────────────────────────────────────────────────────────────────

def section_to_text(stories):
    """Flatten an array of story objects into a plain string for Notion's rich_text field."""
    if isinstance(stories, str):
        return stories
    parts = []
    for s in stories:
        text = s.get("text", "")
        url  = s.get("source_url", "")
        name = s.get("source_name", "")
        label = f" [{name}]" if name else ""
        parts.append(f"{text}{label} {url}".strip())
    return "\n\n".join(parts)[:2000]  # Notion rich_text cap


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
            text        = story.get("text", "")
            source_url  = story.get("source_url", "")
            source_name = story.get("source_name", "Source")
            is_last = i == len(stories) - 1
            divider = "" if is_last else (
                '<div style="border-top:1px solid #f0f0f0;"></div>'
            )
            link_html = (
                f'<a href="{source_url}" '
                f'style="color:#6b7280;font-size:12px;text-decoration:none;'
                f'font-weight:500;letter-spacing:0.2px;">'
                f'{source_name} →</a>'
            ) if source_url else ""
            html += f"""
            <div style="padding:16px 20px;">
              <p style="margin:0 0 10px 0;font-size:15px;line-height:1.65;color:#1f2937;">{text}</p>
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
                    bullets += f'<li style="margin-bottom:10px;color:#374151;font-size:14px;">{text}</li>'
            else:
                bullets += f'<li style="margin-bottom:10px;color:#374151;font-size:14px;">{item}</li>'
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
  <div style="max-width:600px;margin:32px auto;padding:0 16px 32px;">

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

    # Step 6: Persist seen URLs (add all new article URLs)
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
