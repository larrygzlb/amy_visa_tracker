# Visa application tracker

Checks a Belgian visa application on two sites and sends the result to WxPusher:

| Source | What it shows |
|---|---|
| [VFS Global passport tracking](https://visatracking.vfsglobal.com/Global-Passporttracking/) | Where the passport/application is (e.g. "under process at Embassy"). Image CAPTCHA solved via 2Captcha. |
| [Infovisa](https://infovisa.ibz.be/InfovisaFr.aspx) (Immigration Office / DOFI) | Decision status once the embassy has sent the file to Belgium. "No Result" until then. |

## Setup

Copy `.env.example` to `.env` and fill it in. `.env` holds all personal data and keys and is never committed.

## Run

```sh
uv run app.py         # local web app: "Start check" and "Start loop" (every 20 min)
./check_once.sh       # one check from the terminal
```

Results are saved to `data/checks.jsonl`.

## GitHub Actions

`.github/workflows/check.yml` runs `check_once.sh` every 30 minutes. Add one repository secret,
`ENV_FILE`, containing the full contents of your `.env`
(Settings → Secrets and variables → Actions → New repository secret).
