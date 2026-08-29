# Matched Betting Documenter

Local tracker for UK matched betting. Log qualifying bets and free-bet conversions as grouped **offers**, keep bookie and exchange balances honest, and keep an Excel workbook in sync after every change.

The calculator uses the same core formulas as [Matched Betting Blog](https://matchedbettingblog.com/matched-betting-calculator/):

- Qualifying / stake-returned: `lay stake = (back odds × back stake) / (lay odds − commission)`
- Stake-not-returned free bet: `lay stake = ((back odds − 1) × free bet) / (lay odds − commission)`

## Send to a friend

They need **Python 3.10+** installed ([python.org/downloads](https://www.python.org/downloads/) — on Windows, tick **Add python.exe to PATH**).

1. Send `dist/MatchedBettingDocumenter.zip` (from `./pack.sh`).
2. They unzip it. The folder should show **Start** and a **data** folder — everything else is hidden on purpose.
3. They click Start:
   - **Windows:** double-click `start.bat`
   - **Mac / Linux:** `chmod +x start.sh && ./start.sh`
4. Open [http://127.0.0.1:5050](http://127.0.0.1:5050).

Their own database and spreadsheet are created locally in `data/` the first time they run it. Nothing is uploaded anywhere.

To build a clean zip from this repo:

```bash
./pack.sh
```

That writes `dist/MatchedBettingDocumenter.zip`.

## Auto-update (GitHub Releases)

Installed copies check GitHub when Start runs. If a newer release exists they download `MatchedBettingDocumenter.zip` and overlay the app. `data/` and `.venv/` stay put. You can also drop a new zip next to Start and click Start again.

A git checkout (this repo) skips that check so it cannot overwrite your working tree.

### Publish a release

You need the [GitHub CLI](https://cli.github.com/) once:

```bash
gh auth login
./release.sh
```

That creates a public `MatchedBettingDocumenter` repo if needed, writes the repo name into `app/update_source.py`, packs the zip, and uploads it as `v` + the version in `app/version.py`. Bump that version before each release.

Friends’ copies then update on next start. For a private repo, put a token in `data/github_token` (not shared) or set `GITHUB_TOKEN`.

## Update from a zip by hand

Keep your existing folder. Drop `MatchedBettingDocumenter.zip` next to Start and click Start again. The zip is applied and then deleted. `data/` is not overwritten.

From this source checkout:

```bash
./update.sh dist/MatchedBettingDocumenter.zip
./start.sh
```

## Link a laptop and a PC

Both on the same Wi‑Fi, both running the app.

1. On the computer with the **latest** log: **Devices → Start sharing**. Copy the code (`482193@192.168.1.10:5050`).
2. On the other computer: **Devices → Join with a code**, paste, confirm. That machine’s log is **replaced** with the source log.

If they are not on the same network, download **backup.json** on one and restore it on the other.

A home firewall may ask to allow Python on port 5050 the first time.

## Run (from source)

```bash
./start.sh
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open [http://127.0.0.1:5050](http://127.0.0.1:5050).

## Typical loop

1. Add a deposit on **Accounts** (bookie and/or Smarkets).
2. Open **Calculator**, work out the lay stake, and log the qualifier against a new or existing offer.
3. When the event settles, open the bet and choose Back won / Lay won / Void.
4. Log the free-bet conversion on the same offer.
5. Open **Excel** to download `data/matched_betting.xlsx` (Dashboard, Offers, Bets, Accounts, Transfers), or import an existing spreadsheet there.

SQLite (`data/app.db`) is the source of truth. The spreadsheet is rewritten from the database — edit in the app, not in Excel.

## Tests

```bash
pytest
```
