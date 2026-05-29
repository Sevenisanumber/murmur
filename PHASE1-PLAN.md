# WSB Signal Lab — Phase 1 Detailed Build Plan
**Data Collector, Storage, and Basic Dashboard**
*No trades. No LLM. Just clean, reliable data.*

---

## Goal of Phase 1

Prove that you can reliably collect, store, and visualize WSB sentiment data
alongside real stock price moves. Nothing else. The system should run unattended
on the Pi for 30 days without breaking before you move to Phase 2.

Success condition: you open the dashboard on any given day and see yesterday's
top WSB tickers alongside what those stocks actually did in price. That's it.

---

## Realistic Time Estimate

Each session assumes 2 to 4 hours of focused work with Claude helping write
the code. Sessions do not need to be consecutive days.

| Session | Work | Estimated Hours |
|---|---|---|
| 1 | Project setup and accounts | 2-3 hrs |
| 2 | SQLite schema and Reddit scraper | 3-4 hrs |
| 3 | Kaggle dataset import | 2-3 hrs |
| 4 | Alpaca market data connection | 2-3 hrs |
| 5 | Linking posts to price moves | 2-3 hrs |
| 6 | Basic dashboard | 3-4 hrs |
| 7 | Scheduler and Pi deployment | 2-3 hrs |
| 8 | 30-day live test and bug fixing | Ongoing |

**Total active build time: roughly 18 to 25 hours**
**Elapsed calendar time at hobbyist pace: 4 to 8 weeks**

The 30-day live test at the end is where most surprises happen. Budget at least
two to three extra debugging sessions during that window.

---

## Session 1: Project Setup and Accounts
*Goal: everything installed and connected before writing a single line of real code.*

### What you will do

1. Create a GitHub repo called wsb-signal-lab
2. Set up a basic folder structure on the Pi:

```
wsb-signal-lab/
  data/           # SQLite database lives here
  scrapers/       # Reddit and market data scripts
  dashboard/      # Dashboard files
  logs/           # Error and run logs
  scripts/        # Scheduler and utility scripts
  README.md
```

3. Install required Python packages:
   - praw (Reddit API)
   - alpaca-trade-api
   - requests
   - pandas
   - flask (for dashboard)
   - schedule (for running jobs)
   - sqlite3 (built into Python, no install needed)

4. Create a Reddit app for personal use at reddit.com/prefs/apps
   - Type: script
   - Costs nothing
   - Gives you a client ID and client secret

5. Create an Alpaca account at alpaca.markets
   - Paper trading account is free
   - Get your API key and secret key
   - This covers both paper trading later AND free market data now

6. Create a .env file to store credentials safely
   - Never commit this file to GitHub
   - Add .env to your .gitignore immediately

7. Test that both API connections return data without errors

### Go / no-go for Session 2
Both APIs return data when you run a test script. Reddit shows you recent WSB
posts. Alpaca shows you a stock price. Nothing else needed.

---

## Session 2: SQLite Schema and Reddit Scraper
*Goal: raw WSB posts hitting your database reliably.*

### Database schema

Five tables to start. Keep it simple.

**posts**
Stores every raw Reddit post scraped from WSB.
Fields: post_id, author, title, body, score, upvote_ratio, num_comments,
created_utc, url, flair, is_self, scraped_at

**authors**
One row per unique Reddit username seen so far.
Fields: author_id, username, account_age_days, comment_karma,
post_karma, first_seen_at, last_seen_at, total_posts_scraped

**tickers**
Known stock tickers with basic metadata.
Fields: ticker, company_name, exchange, sector, is_ambiguous
(is_ambiguous flags things like AI, ON, ARE, LOVE that are also English words)

**post_tickers**
Links posts to tickers mentioned in them. Many-to-many.
Fields: post_id, ticker, mention_count, extraction_method, surrounding_text

**scrape_log**
Records every scrape run so you can catch missed runs and gaps.
Fields: run_id, started_at, finished_at, posts_fetched, errors, status

### Reddit scraper behavior

- Scrape the top 100 posts from WSB by hot, new, and rising once per day
- Store the raw title and body text before any processing
- Record the exact timestamp of when you scraped it, not just when it was posted
- Never delete raw data. If parsing breaks later, you can reparse from the original.
- Skip posts already in the database by checking post_id before inserting
- Log every run to scrape_log with success or failure status

### Important: raw data first, parsing second

Write the scraper to store raw text immediately. Do not try to extract tickers
or analyze anything in the same script. Raw storage is one job. Parsing is a
separate job. Keeping them separate means a bug in your parser never corrupts
your raw data.

### Go / no-go for Session 3
Run the scraper manually and confirm 50 or more posts land in your SQLite
database with all fields populated correctly. Check for duplicates. Check that
the timestamps look right.

---

## Session 3: Kaggle Dataset Import
*Goal: load historical WSB data for baseline context without touching Reddit API.*

### What to download

Search Kaggle for "Reddit WallStreetBets Posts" and download the dataset that
covers posts back to 2012. It is a CSV file, typically several hundred MB.

### Import script behavior

- Read the CSV in chunks, not all at once, because it is large
- Normalize column names to match your posts schema
- Skip any row missing a post_id, author, or created_utc
- Mark all imported rows with a source field set to kaggle so you can
  distinguish historical data from live-scraped data later
- Run deduplication after import to remove any posts already in the database

### What to expect

The import will take a while on the Pi. Probably 20 to 60 minutes depending
on dataset size. Let it run. Do not interrupt it. Check the scrape_log
afterward to confirm row counts look reasonable.

### Go / no-go for Session 4
Database contains historical posts going back at least to 2020. Row count
looks plausible. No obvious data corruption. A quick SQL query for the most
mentioned words in titles returns things that look like stock tickers and
financial discussion.

---

## Session 4: Alpaca Market Data Connection
*Goal: pull historical stock prices and store them locally.*

### What you need

A price history table in SQLite:

**prices**
Fields: ticker, date, open, high, low, close, volume, source

### Alpaca data script behavior

- Accept a list of tickers as input
- Pull daily OHLCV data (open, high, low, close, volume) going back 2 years
- Store in the prices table, skipping dates already present
- Rate limit yourself to stay within Alpaca's free tier
  (200 requests per minute maximum)
- Log any tickers that return no data so you can investigate

### Important caveat on Alpaca free tier

Alpaca's free tier gives you 15-minute delayed data for live prices but full
historical daily data for backtesting purposes. That is fine for Phase 1.
Real-time live prices are not needed until you start paper trading.

### Go / no-go for Session 5
Query your prices table and confirm you have at least 12 months of daily
price history for 20 or more well-known tickers like AAPL, TSLA, GME, AMC,
NVDA. Verify the numbers look correct against a quick Google search.

---

## Session 5: Linking Posts to Price Moves
*Goal: connect what WSB was talking about to what the stock actually did.*

### Ticker extraction

Write a deterministic ticker extractor. No LLM yet. Just rules-based code.

Rules to implement in order:
1. Look for words in ALL CAPS between 1 and 5 characters long
2. Cross-reference against a known ticker list (downloadable from NASDAQ for free)
3. Check against an ambiguous word blocklist: AI, ON, ARE, LOVE, CAN, GO,
   NOW, RUN, NEW, IT, AT, BE, OR, AND, FOR, THE (add more as you find them)
4. Store extraction method as regex so you know how it was found
5. Store surrounding text (20 words either side) for later review

This will not be perfect. That is okay. You are logging the method so you can
improve it in Phase 2 without losing the original data.

### Forward returns calculation

For each ticker mentioned in a post, calculate what the stock did:
- 1 day after the post
- 3 days after the post
- 7 days after the post
- 30 days after the post

Store these as forward_return_1d, forward_return_3d, forward_return_7d,
forward_return_30d in the post_tickers table.

This is the core of the Signal Lab. You are building a lookup table of
"WSB mentioned this ticker, and here is what happened afterward."

### Go / no-go for Session 6
Run a simple query: show me the 20 most mentioned tickers in the last 30 days
of posts alongside their average 7-day forward return. The output should look
plausible. Some tickers will have positive returns. Some negative. If everything
shows 0% or identical numbers, something is broken.

---

## Session 6: Basic Dashboard
*Goal: something visual you can look at every day that makes the data real.*

### Technology

Flask running on the Pi, accessible over Tailscale just like your Plex dashboard.
Keep it simple. One page. No user login needed since it is Tailscale-only.

### What the dashboard shows

**Top section: today's WSB pulse**
- Top 10 most mentioned tickers in the last 24 hours
- Each ticker shows: mention count, average post score, current price,
  price change today

**Middle section: signal history table**
- Last 30 days of top tickers
- Columns: date, ticker, mention count, 1-day return, 7-day return, 30-day return
- Color coded: green for positive returns, red for negative

**Bottom section: data health**
- Last scrape run time and status
- Total posts in database
- Total tickers tracked
- Any errors from last 24 hours

### What the dashboard does NOT show yet
- Trade recommendations
- LLM output
- Paper trading results
- Anything that requires the system to make decisions

### Go / no-go for Session 7
Dashboard loads over Tailscale on your phone. Data updates reflect yesterday's
scrape. No errors visible. Looks clean enough that you actually want to check
it daily.

---

## Session 7: Scheduler and Pi Deployment
*Goal: the system runs itself every day without you touching it.*

### Cron jobs to set up

Daily at 6:00 AM: run Reddit scraper
Daily at 6:30 AM: run ticker extractor on new posts
Daily at 7:00 AM: fetch latest prices from Alpaca
Daily at 7:30 AM: calculate forward returns for posts now old enough
Weekly Sunday 8:00 AM: run a basic stats summary and log it

### Error handling requirements before you deploy

- Every script must write success or failure to scrape_log
- Every script must catch exceptions and log them instead of crashing silently
- If the Reddit API is down, the script exits cleanly and logs the failure
- If Alpaca rate limits you, the script waits and retries up to 3 times
- Dashboard shows last successful run time so you can see at a glance if
  something has been failing silently

### Go / no-go for 30-day live test
All cron jobs run without manual intervention for 7 consecutive days.
Database grows daily. Dashboard reflects fresh data. No silent failures.

---

## 30-Day Live Test
*Goal: prove the pipeline is airtight before adding any complexity.*

### What you are watching for

- Does the scraper run every day without intervention?
- Are there data gaps, duplicate posts, or missing prices?
- Does the ticker extractor produce obviously wrong results?
  (common false positives to watch for: IT, AT, GO, BE)
- Does the dashboard stay up and reflect fresh data?
- Are forward returns calculating correctly?

### Your job during this period

Check the dashboard roughly every 2 to 3 days. You are not analyzing trades.
You are just making sure the machine is doing its job. When something breaks,
fix it and document what happened. Every bug you fix now is one less bug that
corrupts your signal data later.

### End of Phase 1 success criteria

- 30 consecutive days of clean data collection
- Database contains historical posts from Kaggle plus 30 days of live data
- Top ticker mentions align with what you actually remember seeing in financial
  news during that period (a basic sanity check)
- Dashboard is accessible on your phone over Tailscale and updates daily
- No manual intervention required for daily operation
- Error log shows no silent failures

If all of that is true, you are ready for Phase 2.

---

## Things That Will Probably Go Wrong

Be ready for these. They are normal.

- Reddit API credentials expire or get rate limited. Solution: check praw
  documentation and re-authenticate.
- Kaggle import crashes halfway through. Solution: add a resume checkpoint
  so it picks up where it left off.
- Alpaca returns missing data for certain tickers on certain dates.
  Solution: log the gap and fill it on the next run.
- Ticker extractor goes crazy on a post with a lot of capitalized words.
  Solution: add those words to your ambiguous blocklist.
- Cron job runs but the Pi is under load from Plex and it times out.
  Solution: schedule scraping jobs in the early morning when Plex is idle.
- Dashboard Flask server crashes silently. Solution: run it under systemd
  just like your Plex dashboard so it restarts automatically.

---

## What Phase 1 Does NOT Include

To be explicit about scope:

- No LLM inference of any kind
- No trade recommendations
- No Alpaca paper trading
- No Claude API calls
- No author reputation scoring beyond storing their metadata
- No signal scoring
- No benchmark comparison

All of that comes in later phases, built on top of a foundation that actually works.

---

## Estimated Total Cost for Phase 1

| Item | Cost |
|---|---|
| Reddit API (personal use) | Free |
| Alpaca market data | Free |
| Kaggle dataset download | Free |
| Pi 5 (already running) | $0 additional |
| Claude API calls (debugging help) | $0 to $5 |
| **Total** | **Free to $5** |

---

*End of Phase 1 plan. Build this. Let it run for 30 days. Then come back.*
