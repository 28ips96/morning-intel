# Morning Intel — AI-Assisted Personal Market Briefing

An automated daily digest that pulls fresh articles from RSS feeds across Finance, Geopolitics, Tech, Venture, and AI Commerce — filters them for the last 24 hours, deduplicates across days, and sends them to Google Gemini for analysis. Gemini returns a structured briefing written like a sharp friend explaining the news over coffee: what happened, why it matters, and what it means for anyone working in AI, enterprise software, or retail. The digest is pushed to a Notion database and delivered as a formatted HTML email every morning.

---

## Screenshot

> _Add a screenshot of the email digest here once you have one._

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/morning-intel.git
cd morning-intel
```

### 2. Create a `.env` file

Create a file named `.env` in the project root with the following five variables:

```
GEMINI_API_KEY=your_gemini_api_key
NOTION_API_KEY=your_notion_integration_token
NOTION_DATABASE_ID=your_notion_database_id
GMAIL_ADDRESS=your_gmail_address
GMAIL_APP_PASSWORD=your_16_char_app_password
```

**Where to get each:**

| Variable | Where to get it |
|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `NOTION_API_KEY` | [Notion Integrations](https://www.notion.so/my-integrations) → New integration |
| `NOTION_DATABASE_ID` | Open your Notion database → copy the ID from the URL |
| `GMAIL_ADDRESS` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Google Account → Security → 2-Step Verification → App passwords |

> **Note:** The `.env` file is listed in `.gitignore` and will never be committed.

### 3. Set up your Notion database

Your Notion database needs these five properties:

| Property name | Type |
|---|---|
| `Headline` | Title |
| `Category` | Select |
| `Insight` | Rich text |
| `Source` | URL |
| `Date 1` | Date |

Make sure your Notion integration has been connected to the database (open the database → `...` menu → Connections → add your integration).

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run

```bash
python3 market_intel.py
```

The script will print a live summary as it runs:

```
Fetching articles from RSS feeds...
[Finance]   10/30 passed 24h filter → kept 10
[Geopolitics] 10/10 passed 24h filter → kept 10
...
Freshness: 30/102 articles passed the 24h filter
Dedup: removed 4 already-seen URLs → 26 new articles

Sending articles to Gemini for analysis...
Digest generated.

Pushing sections to Notion...
  [ok] 💰 Money Talk
  [ok] 🌍 World Lore
  ...

Sending email...
  [ok] Email sent to you@gmail.com

--- Summary ---
Articles seen     : 102
Passed 24h filter : 30
New (not dupes)   : 26
Sections in digest: 5
Pushed to Notion  : 5
```

---

## Automating with GitHub Actions

To run the digest automatically every morning, add this workflow file to your repo at `.github/workflows/daily_digest.yml`:

```yaml
name: Daily Morning Intel

on:
  schedule:
    - cron: '0 7 * * *'   # runs at 7:00 AM UTC every day
  workflow_dispatch:        # allows manual trigger from GitHub UI

jobs:
  run-digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run market intel script
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          NOTION_API_KEY: ${{ secrets.NOTION_API_KEY }}
          NOTION_DATABASE_ID: ${{ secrets.NOTION_DATABASE_ID }}
          GMAIL_ADDRESS: ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
        run: python3 market_intel.py

      - name: Commit updated seen_urls.json
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add seen_urls.json
          git diff --cached --quiet || git commit -m "chore: update seen_urls.json [skip ci]"
          git push
```

**Then add your secrets to GitHub:**
Go to your repo → **Settings → Secrets and variables → Actions → New repository secret** and add each of the five variables from your `.env` file.

> The workflow commits the updated `seen_urls.json` back to the repo after each run so deduplication persists across days.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Language | Python 3 |
| News sources | RSS via `feedparser` |
| AI analysis | Google Gemini API (`gemini-flash-latest`) |
| Knowledge base | Notion API v1 |
| Delivery | Gmail SMTP |
| Automation | GitHub Actions |

---

## Notes

- Articles older than 24 hours are filtered out before being sent to Gemini.
- URLs are tracked in `seen_urls.json` so the same story never appears twice across days.
- If a feed fails, the script skips it and continues rather than crashing.
- If Gemini returns invalid JSON, the raw response is printed and the script exits gracefully.

---

_Built as a personal AI workflow tool — part of my ongoing exploration of LLM-powered automation._
