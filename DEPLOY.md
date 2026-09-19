# Deploying TurnitOut

Three supported modes, cheapest first. All use the same codebase — no dependencies to install either way.

---

## 1. Local machine (demo / single user)

```
python run.py
```

Open http://127.0.0.1:8333. Demo data (with sample plagiarism) is seeded on first run.
Delete `data/turnitout.db` to reset.

## 2. School LAN (recommended free option — data never leaves your network)

On the teacher's PC:

- Double-click **`run_lan.bat`** (Windows), or run `python run.py --lan` on any OS.
- The console prints the addresses students should open, e.g. `http://192.168.1.20:8333`.
- If students can't connect, allow Python through Windows Firewall
  (Windows Security → Firewall → Allow an app) and make sure everyone is on the same Wi-Fi.

Production tips for a real class:

- Set `TURNITOUT_DEMO=0` (rename `.env.example` to `.env` first) so demo accounts aren't created;
  the first instructor to register owns the instance.
- Register yourself as the first instructor, then generate invite codes
  (**Classes → Invites**) for colleagues — instructors can no longer self-register freely.
- Back up `data/turnitout.db` occasionally — it's the whole app.

## 3a. Online via Render (free tier, zero management)

1. Push this repo to GitHub.
2. On render.com: **New → Blueprint**, select the repo — `render.yaml` configures everything.
3. (Optional) Add your `GPTZERO_API_KEY` env var in the dashboard.
4. First visitor registers as instructor and bootstraps the instance.

⚠️ **Free-tier caveat:** Render's free instances have *ephemeral disks* — the SQLite
database resets on every redeploy/restart. Fine for a pilot; use option 3b for real data,
or add a paid disk mounted at `/app/data`.

## 3b. Online via any VPS with Docker (full control, automatic HTTPS)

Requires: a VPS (any $4–6/mo box), a domain pointed at it, Docker + compose plugin installed.

```bash
cp .env.example .env
# edit .env:  DOMAIN=turnitout.yourdomain.com   TURNITOUT_DEMO=0   (GPTZERO_API_KEY=...)
docker compose up -d --build
```

Caddy obtains and renews the HTTPS certificate automatically. Data persists in the
`turnitout_data` volume. Logs: `docker compose logs -f turnitout`. Update: `git pull && docker compose up -d --build`.

---

## Production checklist (applies to any public deployment)

- [ ] `TURNITOUT_DEMO=0` — no demo accounts on the internet
- [ ] First instructor registered by **you**, then invite codes for everyone else
- [ ] HTTPS in front (Caddy config included; Render terminates TLS for you)
- [ ] `GPTZERO_API_KEY` set if you want model-based AI detection (sends text to GPTZero — see README privacy note)
- [ ] Backups: copy `data/turnitout.db` (VPS: `docker compose cp turnitout:/app/data/turnitout.db .`)
- [ ] Rate limits are built in (login: 5/5min per IP, register: 5/hour per IP) — tune in `app/server.py` if needed
