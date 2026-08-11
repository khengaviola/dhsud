# DHSUD License-to-Sell List Monitor — GitHub Actions Setup

This runs automatically in the cloud via GitHub Actions — no server or
laptop of your own needs to stay on. It checks daily and emails you only
when the "Data as of ..." date on DHSUD's list changes.

## What's in this folder

```
dhsud_monitor/
├── check_dhsud_update.py          # the monitor script
├── requirements.txt                # Python dependency (playwright)
├── state.json                      # created automatically after first run
└── .github/
    └── workflows/
        └── dhsud-monitor.yml       # the GitHub Actions schedule/workflow
```

## Step 1 — Create a GitHub repository

1. Go to https://github.com/new
2. Name it something like `dhsud-monitor`
3. Set it to **Private** (recommended, since it'll reference your email setup)
4. Click **Create repository**

## Step 2 — Upload these files

Easiest way (no git command line needed):
1. On your new repo's page, click **"uploading an existing file"**
2. Drag in all the files from this folder, **including the hidden
   `.github/workflows/dhsud-monitor.yml` file** — make sure the folder
   structure `.github/workflows/dhsud-monitor.yml` is preserved (GitHub's
   web uploader supports drag-and-drop of folders in most browsers; if it
   flattens the structure, use the git command-line method below instead)
3. Commit the files to the `main` branch

**Alternative (git command line), if you're comfortable with it:**
```bash
git init
git add .
git commit -m "Initial commit: DHSUD monitor"
git branch -M main
git remote add origin https://github.com/<your-username>/dhsud-monitor.git
git push -u origin main
```

## Step 3 — Add your email credentials as repo Secrets

Secrets keep your password out of the code, encrypted by GitHub.

1. In your repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** and add each of these one at a time:

| Secret name     | Example value                  |
|-----------------|---------------------------------|
| `SMTP_HOST`     | `smtp.gmail.com`                |
| `SMTP_PORT`     | `587`                           |
| `SMTP_USER`     | `youraddress@gmail.com`         |
| `SMTP_PASSWORD` | your Gmail **App Password** (see below) |
| `EMAIL_FROM`    | `youraddress@gmail.com`         |
| `EMAIL_TO`      | `you@example.com` (comma-separate for multiple) |

### Getting a Gmail App Password
Your normal Gmail password will NOT work here. You need:
1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Go to https://myaccount.google.com/apppasswords
3. Create an app password for "Mail" — copy the 16-character code
4. Use that as `SMTP_PASSWORD`

(Using Outlook, Yahoo, or another provider instead? Just use their SMTP
host/port and login — no code changes needed.)

## Step 4 — Run it once manually to set the baseline

1. Go to the **Actions** tab in your repo
2. Click **"DHSUD License-to-Sell Monitor"** in the left sidebar
3. Click **Run workflow** (top right) → **Run workflow**
4. Wait ~30-60 seconds, then click into the run to see the log

First run just records today's "as of" date into `state.json` and commits
it back to the repo — it will NOT send an email yet, since there's nothing
to compare against.

## Step 5 — Done. It now runs automatically.

The workflow is scheduled to run **daily at 9:00 AM Manila time**
(`0 1 * * *` UTC in the `.yml` file). You can:
- Change the schedule by editing the `cron:` line in
  `.github/workflows/dhsud-monitor.yml` (cron format: minute hour day month weekday, all in UTC)
- Manually trigger it any time from the **Actions** tab → **Run workflow**
- Check past runs and their logs anytime under the **Actions** tab

You'll get an email automatically the day the list flips from
"June 30, 2026" to "July 31, 2026" (or whatever the next date is).

## Notes

- GitHub Actions free tier includes 2,000 minutes/month for private repos —
  this job takes under a minute per run, so daily runs cost basically nothing.
- If DHSUD changes their page/embed structure, the script may need updating —
  check the Actions log for errors if it stops finding a date.
- Keep the repo **Private** since your email address will appear in commit
  logs/secrets configuration (though the password itself stays encrypted
  and hidden).
