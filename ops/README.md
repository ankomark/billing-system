# Server scripts

These run on the host, not in a container. They are here because until now they
existed in exactly one place — `/home/deploy/` on the production box — so
losing that server meant losing the deploy and backup procedure along with it.

They are the copy of record. The server's copies are what actually run, so
after changing one here, copy it up.

```bash
scp ops/deploy.sh smartbill:/home/deploy/deploy.sh
ssh smartbill 'chmod 700 /home/deploy/deploy.sh'
```

| Script | What it does |
|---|---|
| `deploy.sh` | backup → pull → build → migrate → roll out. Backend only; the frontend deploys from Vercel on push. |
| `backup.sh` | encrypted database dump, kept locally and pushed to remote storage |
| `deploy-quiet-hour.sh` | deploy scheduled for a low-traffic window |
| `wg-setup.sh` | first-time WireGuard server setup; generates the server keypair |
| `wg-add-peer.sh` | add one operator's router to the tunnel |

## What these scripts expect to already exist

None of it is in this repository, and none of it can be, because it is either a
secret or a key:

| Path | What it is | If lost |
|---|---|---|
| `/home/deploy/billing/backend/.env` | real credentials — database, M-Pesa, WhatsApp, `SECRET_KEY`, `FIELD_ENCRYPTION_KEY` | `backend/.env.example` lists every key; the values must be re-obtained or rotated |
| `/home/deploy/.backup-pass` | passphrase the dumps are encrypted with | **existing backups become unreadable** |
| `/etc/wireguard/` | server keypair and peer list | tunnel rebuilt with `wg-setup.sh`, then every router re-added |
| rclone remote config | where dumps are shipped | reconfigure `rclone` |

`FIELD_ENCRYPTION_KEY` deserves singling out: it decrypts stored router
passwords. Lose it and the database still restores, but every router credential
in it is unreadable and has to be re-entered by hand.

## Restoring onto a new machine

1. `git clone` this repository
2. `cp backend/.env.example backend/.env` and fill it in — every key is listed
3. copy these scripts to `/home/deploy/` and `chmod 700`
4. restore `.backup-pass`, then a dump
5. `wg-setup.sh`, then `wg-add-peer.sh` once per router

Step 4 is the one with a hard dependency on something outside version control.
A dump without its passphrase is not a backup.
