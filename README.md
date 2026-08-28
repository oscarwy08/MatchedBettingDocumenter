# Matched Betting Documenter

Local tracker for UK matched betting. Log qualifying bets and free-bet conversions as grouped **offers**, keep bookie and exchange balances honest, and keep an Excel workbook in sync after every change.

The calculator uses the same core formulas as [Matched Betting Blog](https://matchedbettingblog.com/matched-betting-calculator/):

- Qualifying / stake-returned: `lay stake = (back odds × back stake) / (lay odds − commission)`
- Stake-not-returned free bet: `lay stake = ((back odds − 1) × free bet) / (lay odds − commission)`

## Send to a friend

They need **Python 3.10+** installed ([python.org/downloads](https://www.python.org/downloads/) — on Windows, tick **Add python.exe to PATH**).

1. Zip this folder **without** `.venv`, `.git`, or `data/*.db` / `data/*.xlsx` (those are your personal bets).
2. Send the zip.
3. They unzip it, then:
   - **Mac / Linux:** `chmod +x start.sh && ./start.sh`
   - **Windows:** double-click `start.bat`
4. Open [http://127.0.0.1:5050](http://127.0.0.1:5050).

Their own database and spreadsheet are created locally in `data/` the first time they run it. Nothing is uploaded anywhere.

To build a clean zip from this repo:

```bash
./pack.sh
```

That writes `dist/MatchedBettingDocumenter.zip`.

## Update without losing bets

Keep your existing folder. Drop the new zip next to it, then:

**Mac / Linux**

```bash
./update.sh dist/MatchedBettingDocumenter.zip
./start.sh
```

**Windows**

`update.bat` has to live **inside the unzipped app folder** (next to `start.bat`), not inside the zip preview in Explorer.

1. Stop the app if it is running.
2. Copy the new `MatchedBettingDocumenter.zip` onto the PC (Downloads is fine).
3. Open the **existing** app folder in Explorer.
4. Double-click `update.bat` and choose that zip in the file picker.

In PowerShell you must type a `.\` and be in that folder:

```powershell
cd path\to\MatchedBettingDocumenter
.\update.bat C:\Users\You\Downloads\MatchedBettingDocumenter.zip
```

If `update.bat` is missing, this copy is still the old app. Unzip the new zip to a **new** folder, copy the old `data` folder into it, then double-click `start.bat` there.

`data/app.db` and `data/matched_betting.xlsx` are not overwritten. After a successful update the zip file is deleted.

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
