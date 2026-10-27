# Shooting Diary Suite

A self-hosted shooting diary for a few people: range visits and the licence record, muzzle velocities
(Garmin Xero), scope zeroes, guns loaned to friends and a click calculator. Flask + SQLite, served by Waitress,
installable on the phone. Each person has their own account and their own database file.

The site is the diary: the bare domain (`/`) redirects to `/diary/`. Everything under `/diary/` needs a login
except the login, sign-up and privacy pages. (Markdown files dropped into a `notes/` folder are served at
`/notes/<name>` – a leftover from the old site, unused now.)

New user? See the **[quick start guide](docs/QUICKSTART.md)** for how to use the app.

## Pages

| Address | What |
|---|---|
| `/diary/` | Landing page: a tile per section with a one-line summary; Password, My data, Accounts (admins), Install app |
| `/diary/log` | **Diary** – range visits, guns and their round counts, licence export (PDF/CSV) |
| `/diary/velocity` | **Velocity** – Garmin Xero strings, loads, trends, charge ladders |
| `/diary/zeroes` | **Zeroes** – scope dial positions, swaps, zeroing card (PDF) |
| `/diary/loans` | **Guns loaned** – friends' shots with your guns/ammo and what they owe (tile only) |
| `/diary/calculator` | **Click calculator** – group offset → clicks to dial (tile only) |
| `/diary/login`, `/diary/signup`, `/diary/privacy` | Public |
| `/ammo/<club>/<code>` | **Club ammo** – a box's shooter form, public (the code is the box's secret, printed as a QR code) |
| `/ammo/<club>/pay/<code>` | **Club fees** – payments page for season / one-time fees and merch, public (secret link, QR code) |
| `/ammo/<club>/qr` | **Club ammo** – printable QR stickers for the boxes (admins) |
| `/ammo/<club>/admin` | **Club ammo** – stock, refills, moves, counts, payments (diary login + a role in the club) |

Diary, Velocity and Zeroes have a switcher at the top (Home · Diary · Velocity · Zeroes); Guns loaned and the
click calculator are opened from their tiles. The old `/diary/chrono`, `/diary/zero` and `/diary/lent`
addresses redirect. Long-press the installed app icon for shortcuts.

Code lives in `diary/` and `club/`; `main.py` calls `diary.init_app(app)` and `club.init_app(app)` and redirects `/`.

Data is stored outside git in `diary_data/`: `users.db` (accounts), one SQLite file per account (`diary.db` for
the first one, `users/<name>.db` for the rest), `clubs.db` (club ammo), `deleted/` (final copies of deleted accounts, 30 days) and the
cookie key. `diary_data/`, `diary_backups/`, `.env`, `diary_seed*.json` and `*.xls(x)` are in `.gitignore` –
this repo is public, never commit them. Tests use synthetic data only.

## Running it

### First-time setup

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m diary user-add NAME --admin --password   # first account; asks twice, min. 10 chars
./venv/bin/python main.py                                      # Waitress on <LAN-IP>:5000
```

`main.py` serves the app with **Waitress** (a production WSGI server – not Flask's development server) on the
machine's LAN address, port 5000. Put it behind a reverse proxy that does HTTPS (here Nginx Proxy Manager on
another machine) and point the domain at it; the login cookie is HTTPS-only by default.

Run it as a systemd service (`shooting-diary.service`, `Restart=always`, enabled at boot). Allow port 5000 only
from the proxy, e.g. with ufw on the diary server:

```bash
ufw default allow incoming
ufw allow from <proxy-IP> to any port 5000 proto tcp
ufw deny 5000/tcp
ufw enable
```

### Settings (`.env`, all optional)

| Variable | Default | Purpose |
|---|---|---|
| `DIARY_DATA_DIR` | `./diary_data` | Where the databases and secrets live |
| `DIARY_CONTACT` | – | Contact shown on the privacy page, e.g. an email address |
| `DIARY_COOKIE_SECURE` | `1` | Login cookie only over HTTPS. `0` allows logging in via `http://<LAN-IP>:5000` |
| `DIARY_BEHIND_PROXY` | `1` | Trust one `X-Forwarded-For` hop (the proxy) for the client IP |
| `DIARY_LOGIN_DAYS` | `365` | Days a login stays valid on a device without being used (renewed on every visit) |
| `DIARY_ADMIN_USER` | `osku` | Name given to a pre-accounts single-user diary when it's converted |
| `FLASK_SECRET_KEY` | auto | Cookie signing key; generated into `diary_data/secret_key` if unset |
| `DIARY_SMTP_*`, `DIARY_BACKUP_TO` | – | Backup e-mail, see below |
| `SSI_API_KEY`, `SSI_EMAIL`, `SSI_PASSWORD` | – | Club ammo: fetch matches and competitors from Shoot'n Score It's API (all three needed) |

### Updating the server

```bash
./venv/bin/python -m pip install pytest   # once: lets the update run the tests before restarting
./scripts/update.sh --check               # what would come in
./scripts/update.sh                       # pull + check + restart
```

`update.sh` refuses to run over local edits, snapshots every database to `diary_backups/pre-update/` (newest
5 kept), pulls the checked-out branch (`main`, or a test branch such as `club-ammo` after
`git checkout club-ammo`; back with `git checkout main && ./scripts/update.sh`), installs `requirements.txt` if it changed, compiles, starts the app once in a throwaway
folder and runs the tests. If anything fails it rolls back to the previous commit and leaves the running site
alone; otherwise it restarts and checks the site answers. Restart: `RESTART_CMD="…"`, or the systemd unit
`SERVICE` (default `shooting-diary.service`, falling back to the old `flask-telemetry.service`).
`./scripts/update.sh --restart` only restarts.

### Weekly backup

`scripts/diary_backup.sh` runs `python -m diary backup diary_backups --keep 12 --mail`: for **every account**
a dated `diary-<user>-YYYY-MM-DD.json` + `.db` on the server (newest 12 per account), and all the JSONs in one
e-mail. Backups of deleted accounts and their final copy in `diary_data/deleted/` are removed after 30 days.

Add to the server's `.env`:

```
DIARY_SMTP_USER=you@gmail.com            # must be the Gmail address the app password belongs to
DIARY_SMTP_PASSWORD=abcdefghijklmnop     # Gmail app password, myaccount.google.com/apppasswords
# DIARY_BACKUP_TO=other@address          # optional, defaults to DIARY_SMTP_USER
```

```bash
./scripts/diary_backup.sh   # test once – check your inbox
crontab -e                  # 17 3 * * 0  /root/Shooting_Diary_Suite/scripts/diary_backup.sh >> /var/log/diary_backup.log 2>&1
```

The same script also copies the club ammo database (`python -m club backup`): `clubs-YYYY-MM-DD.db`, newest 12
(not e-mailed). Before that, shooters' names on club entries older than `CLUB_NAMES_YEARS` (default 3) are blanked.

Backups contain everyone's data, licence numbers included – the privacy page says they go to the admin's
mailbox. Restore one account: `python -m diary import --user NAME diary_backups/diary-NAME-YYYY-MM-DD.json --replace`
(or stop the app and copy the `.db` over that account's file in `diary_data/`).

### Health check (Uptime Kuma)

`GET /diary/health` needs no login and answers JSON:

| `status` | HTTP | Meaning |
|---|---|---|
| `ok` | 200 | databases readable, disk space fine, newest backup recent |
| `degraded` | 200 | site works, but disk is low (`DIARY_DISK_MIN_MB`, default 1024) or the newest backup is older than `DIARY_BACKUP_MAX_DAYS` (default 8; 0 = don't check) |
| `down` | 503 | a database can't be read |

`checks` names which part failed; `version` is the running git commit. Uptime Kuma: *HTTP(s) – Keyword* monitor
on `https://<your-domain>/diary/health` with keyword `"status":"ok"` alerts on both down and degraded; a plain
*HTTP(s)* monitor alerts only when it's down. Backups are looked for in `DIARY_BACKUP_DIR` (default `diary_backups/`).

### Tests

`python -m pytest tests` (synthetic data). GitHub Actions runs them on every push (`.github/workflows/tests.yml`).

## Accounts

Every account has its own database file – one user's pages, API calls, offline queue and backups never touch
another's data. Three ways in:

- **Admin adds it in the app**: landing page → **Accounts** → username → *Add account*, then *Share login
  details* sends the address, username and "leave the password empty". The person logs in with an **empty
  password** within 7 days and must choose their own (min. 10 characters) before anything else.
- **The person asks** on the login page (*No account? Ask for one*): username + their own password. The account
  is created switched off; the admin sees *Accounts · 1 new* and taps **Approve** or **Reject**. Until then,
  logging in says it's waiting for approval. Limits: 3 requests per address per hour, 10 open at a time.
- **Command line** (below).

In *Accounts* an admin can also *Reset password* (back to "choose one at next login"), *Disable* / *Enable* and
*Delete* (the data goes; a final copy is kept in `diary_data/deleted/` for 30 days) – never on their own
account. Everyone changes their own password with *Password* and downloads all their data with *My data*
(one JSON file). Five wrong passwords from one address lock logins for 15 minutes; when an admin
resets or clears the password of an account tried from there (admin page or `python -m diary`), that account can log
in again at once.

```bash
python -m diary user-add mikko             # new account, no password yet – tell them to log in soon
python -m diary user-add NAME --password   # …or set the password now
python -m diary user-add NAME --admin      # admin: can manage accounts in the app
python -m diary user-list                  # accounts, data files, last login, unclaimed accounts
python -m diary user-passwd mikko          # new password – logs that user out on every device
python -m diary user-passwd mikko --clear  # forgot it: they choose a new one at next login
python -m diary user-disable mikko         # blocks login and ends their sessions (data kept); user-enable
python -m diary user-rename old new        # login name only; the data file stays
python -m diary user-delete mikko          # account + data (final copy kept 30 days)
```

A diary from before accounts becomes the admin account **osku** (or `DIARY_ADMIN_USER`) on first start, with
the same password and data (`diary.db`).

### Demo account

A shared showcase with invented sample data (four guns, ~34 visits, velocity strings with a charge ladder,
a scope with three positions, two loans). Anyone can open it without a password:
**`https://<your-domain>/diary/demo`**, or *Try the demo* on the login page (shown once the demo exists).

```bash
python -m diary demo-reset    # create it / put the sample data back; also logs demo visitors out
                              # (update.sh also does this after every update; DEMO_RESET=0 skips it)
crontab -e                    # nightly: 30 4 * * *  cd /root/Shooting_Diary_Suite && ./venv/bin/python -m diary demo-reset
```

The demo account can't change its password (it's random and never shown) or see other accounts. Visitors
share it, so the home page tells them not to type anything personal. To remove it: `python -m diary user-delete demo`.

### Privacy

`/diary/privacy` (public, linked from the login and sign-up pages) says what is stored and who can see it.
Keep it true when changing things: fonts and the PDF library are served from `diary/static/` (no requests to
other sites, no analytics), backups and deletion work as described above, and `DIARY_CONTACT` sets the contact
line.

## Phone app

The diary is an installable web app (manifest + service worker in `diary/static/`). Open `/diary/` over HTTPS:
Android Chrome → menu → **Install app** (or *Install app* on the landing page); iPhone Safari → Share → **Add
to Home Screen**. It opens full-screen, starts without a connection using the last synced data, and queues
saves until the server is reachable again. Logging out clears the cached data. After changing anything in
`diary/static/`, bump `VERSION` in `sw.js` so phones drop the old cache.

**Drafts and Back** (`diary/static/drafts.js`, on every page): whatever is typed into a form – a visit, gun,
your details, a load, a loan, a zero position, scope info, notes – is kept on the phone until it's saved.
Closing the sheet, pressing Back or the browser dropping the page loses nothing: opening the same record again
brings the unsaved values back with a *Discard* button. Drafts are per account and record and go after 14 days.
The phone's Back button closes the open sheet instead of leaving the page.

## Diary (`/diary/log`)

- **Visits**: *+ New visit* – only the date is required; place, organiser, start/end time are optional. A row per
  gun (or *Other row*) with type, calibre, firing mode, shots and notes. Save is at the top and the bottom of the
  sheet; a field the browser refuses is named in a message.
- **Signature (optional)**: someone (e.g. the range officer) can sign a visit – a name and a signature drawn
  with a finger at the bottom of the sheet (open by default). The ⛶ button signs full screen: on a phone held
  upright the page turns sideways (turn the phone left), and the signature is stored upright. It's stored with the visit (a small PNG), the list marks the visit
  *signed*, and the export gets a *Kuittaus* / *Signed by* column (name, signature and a small "allekirjoitettu 21.10.2026 21.02 EEST" timestamp in the PDF; name and time in the CSV)
  when any visit in the range is signed; *Include signatures* in Export is ticked when the range has signed visits and can be unticked.
- **Guns**: name, type, calibre, firing mode, shots before the diary, acquired date. A gun can fire other calibres too
  (*Also fires*, e.g. .38 Special in a .357 Magnum revolver): visits get a button per calibre, the row's calibre
  becomes a choice, and the guns list counts shots per calibre. Velocity loads and loans accept them as well. The list shows each gun's
  round count, including shots of friends it was loaned to.
- **Firing mode** is a fixed list (1 single shot, 2 magazine-fed single shot, 3 semi-automatic, 4 automatic),
  stored in the Finnish legal wording so the licence export matches.
- **Export**: *Preview* shows the PDF page by page before anything is downloaded. *Page*: portrait or landscape (remembered).
  The table text is as large as the page allows while no cell needs more than two lines (portrait about 6.5 pt,
  landscape up to 10 pt); date, time and shots never wrap. PDF or CSV, in Finnish (the shooting-activity record for a licence application) or English, for
  the last 2 years, a year, custom dates (from – to) or the whole diary. Exports drop gun names and merge rows of
  the same type, calibre, firing mode and notes within a visit.
- **My details**: name and licence numbers for the export (optional; the row is hidden while empty).
- **Henkilötunnus** can be typed in Export for that one PDF/CSV (checked for format and check character). It's never
  stored – not on the server, not in the browser – and the field empties when Export closes.

Data commands work on one account: add `--user NAME` (default: the first admin account).

```bash
python -m diary export backup.json --user mikko    # full JSON backup of one account
python -m diary import file.json [--replace] --user mikko
python -m diary assign-gun --list-guns             # tie old rows (without a gun) to a gun; dates inclusive
python -m diary assign-gun g-wwsd --cal ".223 Rem" --from 2024-04-17 --to 2026-07-10 --dry-run
```

Import files may contain `profile`, `guns`, `sessions` and the other sections of an export; each part is
optional and upserted by id.

## Muzzle velocity (`/diary/velocity`)

Garmin Xero C1 ShotView exports, your own velocity workbooks and Kestrel DROP weather logs.

- **Share from ShotView** (Android, installed app): ShotView → session → Share → *Shooting Diary* opens the
  import preview with the file. Install the app with **Chrome**: other browsers only make a home-screen
  shortcut, which Android never lists as a share target. After updating, remove and re-add the app once so
  Android learns the share target. Not available on iPhone.
- **Import** (*+ Import velocities*): pick files or a whole folder. ShotView `.xls`/`.xlsx`, velocity workbooks
  (`<gun>, <bullet>, <brass>, <charge> gr.xlsx`, one `DDMMYYYY` sheet per day) and Kestrel CSVs are recognised
  by content. Folder names give the powder (`N555`, `N133`, `N150` …) and the gun, in either order. Nothing is
  saved until *Save selected* in the preview.
- Shots are fingerprinted (date + time + speed): re-importing is harmless, and a raw ShotView session that
  overlaps a stored day only fills missing weather — fliers you removed in a workbook stay removed.
- **Statistics** from included shots: average, sample SD (÷ n−1), Garmin's SD (÷ n), ES, 95 % CI, average
  energy (J). Days pool all strings of the same gun + load; the same load in two guns is tracked separately.
- **Page**: gun buttons, then **charge ladders** (a button per gun + bullet/powder/brass; the chart opens on a
  tap), the **load** list grouped by calibre, then the strings, newest first. Picking a load shows its trend
  (average + CI, SD per day), velocity vs temperature with the sensitivity in m/s per °C, and its ladder.
  Each string: shot dot-plot with exclude/include per shot and manual weather.
- **Loads**: handload – bullet maker, bullet, weight, brass, powder, charge, primer, COAL; factory – maker,
  product, bullet weight. The name is generated in the order bullet · powder · brass, e.g.
  `6.5 CM · Lapua Scenar 139 gr · N555 44.0 gr · Lapua brass`. The calibre is part of the load. Saving a load
  whose name matches another offers to merge them.

```bash
python -m diary chrono-alias "KP-15" g-wwsd        # folder/file name → diary gun (no args lists them)
python -m diary chrono-import <folders…> --dry-run  # then without --dry-run; --keep-swaps, --fix-dates
python -m diary chrono-stats --gun g-t3x           # per-day table
```

## Scope zeroes (`/diary/zeroes`)

Scopes moved between rifles: a dial position (elevation + up, windage left/right) per rifle and load. The
database is the only copy.

- **Several scopes**: *+ Scope* adds one; each has its own positions, zero stop, reference and history. The
  chips show where each scope is. Mounting a scope on a rifle takes any other scope off that rifle (logged).
  Old scopes can be archived.
- **Swap**: *Scope is on* → *Move to* shows what to dial (e.g. `DOWN 2.7` / `0.1 RIGHT`, in clicks too), the
  target's turret readings and its distance above the zero stop. *Mounted on …* records the swap, then asks
  for the check shot: *On zero ✓* logs it, *Off – adjust…* opens the correction.
- **Cap readings**: with the turret turn sizes in *Scope info* (e.g. 10 MRAD), the swap also says where the
  caps stop — elevation one way (DOWN 2.9 → `7.1`), windage both ways to the half-turn mark
  (`1 L … 4 L, 5, 4 R … 1 R`) with a warning when a move passes that mark.
- **Adjust**: UP/DOWN and LEFT/RIGHT by clicks for what you corrected at the range; logged with date and note.
  *From 0 / 0* instead counts the clicks from the reference (where the new zero is, e.g. 3 down / 3 left) and
  works out the move from the current position (each position opens on *From dial position now*). *Undo* reverts the newest change of a position. Exact values can be typed under *Details* (logged as "set").
- **0 / 0 reference**: by default 0 / 0 is the turret's own zero (values are dial readings). *Make this the
  0 / 0 reference* (or *Scope info*) relabels everything so a position reads 0 / 0 — e.g. after slipping the
  caps on a new zero: *Adjust* first, slip the caps, then make it the reference. If that position is later
  moved off 0 / 0, the reference goes back to the turret zero by itself.
- **Offline**: adjustments, check shots, mounts, position edits and notes made without a connection are kept on
  the phone (marked *not sent yet*) and sent in order later; each is applied once. Undo, reference changes
  and deletes need a connection.
- **Notes** (collapsible): Markdown with `## headings`, bullets and `- [ ]` to-dos. **History** shows the last
  3 changes (*Show all*).
- **Zeroing card (PDF)**: one A4 page for the range bag — positions with distance above the zero stop and last
  check shot, every swap both ways with clicks, rifles and mounts, notes, recent changes.

An old Markdown zeroing note can be brought in once: `python -m diary zero-import NOTE.md --dry-run` (then
without `--dry-run`; `--replace` overwrites). Rifle names are matched to diary guns like velocity folders.

## Guns loaned (`/diary/loans`)

When friends shoot your guns and/or your ammo (e.g. at a match): *+ Loan*, one entry per trip – date, event
and a line per friend and gun with shots and price per round. Their shots count toward each gun's round count
("incl. N loaned") but never toward your own shooting record or the licence export. A friend's own gun with
your ammo: *Not my gun – ammo only* and the calibre. Cost per friend, *Paid* ticks, *Share cost* sends the
breakdown; unpaid totals on the page and the landing tile. *Ammo prices* holds the price per round: calibre, ammo
brand / load (optional) and € per round, so one calibre can have several (e.g. .22 LR SK Standard Plus 0.12 € and
Lapua Center-X 0.25 €). A loan line then offers an *Ammo* choice that sets its price; the first entry of a calibre is
the default. Stored as "calibre · ammo" keys (starting values in `diary/loans.py`, `DEFAULT_AMMO_PRICES`; each
account edits its own).

## Click calculator (`/diary/calculator`)

The Scope Adjustment Calculator app as a page: distance (m / yd / ft) and where the group hit (high/low,
left/right in mm / cm / in) → the correction in clicks, plus MRAD and MOA, for your scopes' own click values or
¼, ½, 1 MOA, 0.1 / 0.05 MRAD, 0.1 mil. *Reset* clears the offsets. Inputs are remembered; works offline.

## Club ammo (`/ammo`, package `club/`)

For a shooting club's ammo boxes: shooters log what they take with their own phone, no account needed; the
club's admins see stock and payments and know when to buy more. Separate from the diary – its own package,
pages and database (`diary_data/clubs.db`) – and talks to the diary only through `club/bridge.py` (who's logged
in, their name, "add to my diary"). Moving it to its own repository means rewriting that one file.

- **Several calibres in one box** (Edit box → *More calibres in this box*, e.g. 12/70 and 7.62x54R in a box that is
  used less): the form then lists every ammo in the box on its own line with its own rounds; the shooter fills in
  what they shot and pays the total. Stock, totals, refill levels (one per calibre in the box) and buy-more
  levels are per calibre; Move works between boxes
  that share the calibre; the diary gets one line per calibre.
- **Boxes**, not just calibres: each physical box has a name, one calibre, a holder and a refill level. Every
  box has its own secret link; print it as a QR code on the lid. *New link* replaces a leaked one.
- **Shooter form** (scan the box), in Finnish, Swedish or English (FI · SV · EN at the top, remembered on the phone;
  Finnish by default, Swedish if the phone is in Swedish): name (remembered on the phone; filled in from the diary profile when logged
  in), date, ammo – only what's in that box – and rounds with 25/50/100/200 buttons. It shows the total and,
  after Save, an *Open MobilePay* button (the box's MobilePay link – paste the address from your MobilePay QR code
  into Edit box, so the number and payment category are right) or else the club's MobilePay number, with a
  4-character reference for the payment message, after the club's own text if one is set in Setup (e.g.
  *HARRASTEMAKSU A7K3*). One QR code per box is enough: the form's link. *I'm paying now*
  is ticked by default; unticked entries show as not paid. Logged-in shooters can tick *Also add to my diary*
  (the rounds go onto that day's visit, or a new one, with the box's firearm type). Works offline: entries wait
  on the phone and are sent later. The same phone can *Take back* an entry for 30 minutes.
- **Admin page**: *Stock* lists each box with what's in it per calibre (and per ammo) and a *Level* link per
  calibre to set that box's refill level (Alpha 9 mm below 300, another 9 mm box below 250 …), then the calibres
  over all boxes; *Refill* (new rounds into a box), *Move* (box → box), *Count*
  (the difference to the log is saved as a correction), *Hand over* (new holder, logged). Warnings when a box is
  below its refill level and when all boxes of a calibre are below its *buy more* level. *Log & payments* per
  month: rounds shot, paid / not paid totals, search by name or reference, *Hide voided*, *Mark paid*, *Void* (stays in the
  log, crossed out). *Setup*: club name, MobilePay number, payment message, ammo & prices, boxes, admins.
- **QR stickers** (Setup → Boxes → *Print QR stickers*, or *Print sticker* on a box): 1, 2 or 4 per A4, Finnish or
  English, with the club, box, calibre, the steps (scan → name and rounds → MobilePay with the message) and the
  prices. Print or save as PDF at 100 %. The QR codes are made in the browser (`club/static/vendor/qrcode.js`).
- **Fees & merch** (`club/shop.py`): Setup → *Fees & merch* – season fee (*kausimaksu*, once a year), one-time fee
  (*kertamaksu*, e.g. per SRA match) and merch (patches …, optional stock with *Refill* / *Count* and a low warning, and an optional quantity price:
  10 € each but 3 for 25 € → 1 = 10, 2 = 20, 3 = 25, 4 = 35 …; stock counts single items).
  Each has its own price, MobilePay link (its category) and message template – `{pvm}` date, `{vuosi}` year,
  `{kpl}` count, `{koodi}` the code (else added at the end): "SRA KERTAMAKSU {pvm}" → SRA KERTAMAKSU 27.10.2026 A7K3. One **payments page** per club
  (Setup → *Payments page*: link, *Print sticker*, *New link*): name, pick **one** fee or item (and how many),
  Save → *Avaa MobilePay*, the amount and the message + code. One per entry, so every MobilePay payment carries its
  own right message; the next one with *New entry*. Same languages, offline queue and *Take back* as the box form; also works for the organiser at the desk
  (one shooter after another with *New entry*). Admins: **Season fees** tab – who has paid in a year, search by name
  (names as typed) – and the fees in *Log & payments* (filter *Fees & merch*, Mark paid, Void). Box holders don't
  see fees.
- **Treasurer's export** (*Log & payments* → *Export for the treasurer…*, admins): a period (this / last month, this /
  last year or dates), everything / ammo / fees & merch, optionally only not paid → **PDF** (entries + summary, made
  in the browser with the diary's jsPDF), **Excel** (sheets entries + summary, real dates and numbers) or **CSV**
  (Finnish: `;` and decimal comma, UTF-8 for Excel). One row per ammo / product line; voided entries left out; in
  the admin page's language (`club/export.py`).
- **Archived** ammo prices, boxes, fees and items are hidden in Setup (*Show N archived*). One that was never used
  (no rounds / payments / stock in the log; a box's hand-overs don't count) can be deleted from its Edit sheet.
- **Matches** (`club/match.py`, admin tab *Matches*): a second check on club ammo at a match. The admin adds the
  match (date, number of stages, which boxes are there) – or pastes the match's SSI link and presses *Fetch*: name,
  date, stages and the competitors with their squads come from SSI's GraphQL API (`club/ssi.py`; needs `SSI_API_KEY`,
  `SSI_EMAIL`, `SSI_PASSWORD` in `.env` – SSI wants an API key plus a login token, fetched with that account and kept
  in memory). *Fetch from SSI* on the match fetches again: new competitors are added, changed squads / numbers updated.
  Without the API: copy the participants table on shootnscoreit.com, *Paste from SSI…* and pick which column is the
  name, squad, division and number. On the server, `venv/bin/python -m club ssi <link>` shows what the API returns
  without changing anything. **Officers** (range / stat officers, Setup →
  *+ Officer*, a diary account) get only the officer page `/ammo/<club>/ro`: stage chips, squad filter, and a −/+
  count per competitor and ammo; saved at once, queued on the phone without signal. On the match day the box form
  suggests the competitor names, so a shooter's own entry links to them. *Reconciliation* compares officers' rounds
  with the shooters' own entries (✓ ok · fewer · no own entry · not paid) and shows the missing €; PDF / CSV for
  the treasurer. *Default ammo per stage* (match setup, optional; several on a pistol + rifle stage) opens each stage
  on the officer page with those ammo lines; *+ another ammo* adds one. SSI's competitor *category* is read too: Special 1 / 2 / 3 = loan rifle / pistol / shotgun –
  those competitors (and anyone with rounds) are listed first on the officer page with a tag, the rest under *Other
  competitors*. The officer page shows who entered each count and reloads others' entries every 45 s. Officers never see
  money, stock or names outside the competitor list. The list can't be replaced once officers have entered rounds.
- **Languages**: admin pages in Finnish or English (FI · EN at the top, remembered per phone; Finnish texts in
  `club/static/admin_fi.js`); stickers in Finnish, Swedish or English; shooter forms in Finnish, Swedish or English.
- **Roles per club**: admins (diary accounts) do everything; a box's holder, if given a diary account, can buy,
  move and count for that box and sees stock but not shooters' names. Site admins act as admin of every club.
  Stock is never stored – it's the sum of all moves, so the log is the audit trail.
- Abuse: a hidden field for bots, 300 entries / hour per address (phones on mobile data often share one), resent offline entries aren't doubled.
- Command line (on the server: `venv/bin/python -m club …`): `club-add "Name" [--id SLUG] [--admin USERNAME]`
  (the id in links is made from the name: "VaaRes SRA" → `vaares-sra`), `club-delete SLUG [--yes]` (the club and
  everything in it), `admin SLUG USERNAME [--remove]`, `list` (stock and warnings), `backup DIR`. A site admin can also create a club
  from `/ammo/`. The diary home page shows a *Club ammo* tile to admins and box holders.

Getting started: `venv/bin/python -m club club-add "My Club" --admin osku`, then on `/ammo/my-club/admin` → Setup:
MobilePay number, ammo & prices, boxes (holder, refill level); *Refill* each box with its starting rounds; tap
*Print QR stickers* for the lids.

## Language

Pages, command line and backup e-mail are in English. Firearm types and firing modes are stored in the Finnish
legal wording (Ampuma-aselaki: *Kivääri*, *2 - lippaallinen kertatuli* …) and shown in English; the export can
be Finnish or English.

## Third-party files

`diary/static/fonts/` – Barlow Condensed, IBM Plex Sans, IBM Plex Mono (SIL Open Font License 1.1, licences
alongside). `diary/static/vendor/` – jsPDF 2.5.1 and jsPDF-AutoTable 3.8.2 (MIT), and pdf.js 3.11.174 in `vendor/pdfjs/` for the export preview (Apache 2.0); licences alongside.
`club/static/vendor/qrcode.js` – qrcode-generator 1.4.4 by Kazuhiko Arase (MIT), licence alongside.
