#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 server for the charter990 pipeline, as root.
# Safe to rerun: each step checks what's already there.
#
#   REPO=git@github.com:<owner>/charter-school-990.git bash setup.sh
#
# First run prints a deploy key to add to the GitHub repo (read-only), then
# stops. Rerun after adding it, and again after writing /etc/charter990.env:
#   DATABASE_URL=<Neon direct owner connection string>
#   DAGSTER_HOME=/var/lib/charter990/dagster_home
#   CHARTER990_SCHEDULE=on
#   ADJUDICATOR=both          (apply the picks the scored model and Jev agree on)
#   JEV_API_KEY=...           (optional: without it Jev isn't asked about new schools)
set -euo pipefail

REPO=${REPO:?set REPO to the repo SSH URL}
APP=/opt/charter990
HOME_DIR=/var/lib/charter990
KEY=$HOME_DIR/.ssh/id_ed25519
UV_VERSION=0.12.22
as_app() { sudo -u charter990 -H "$@"; }

apt-get update -qq
apt-get install -y -qq git curl ca-certificates rsync postgresql-client >/dev/null

id charter990 >/dev/null 2>&1 || useradd --system --create-home --home-dir "$HOME_DIR" --shell /bin/bash charter990
command -v uv >/dev/null || curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" \
  | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

# Read-only deploy key: the server can pull the repo and nothing else.
if [ ! -f "$KEY" ]; then
  as_app mkdir -p "$HOME_DIR/.ssh"
  as_app ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C charter990-deploy
fi
as_app sh -c "ssh-keyscan -t ed25519 github.com 2>/dev/null >> $HOME_DIR/.ssh/known_hosts && sort -u -o $HOME_DIR/.ssh/known_hosts $HOME_DIR/.ssh/known_hosts"

if [ ! -d "$APP/.git" ]; then
  if ! err=$(as_app git ls-remote "$REPO" 2>&1 >/dev/null); then
    # Only a refused key means the deploy key is missing; say so for anything else.
    if printf '%s' "$err" | grep -qiE "permission denied \(publickey\)|repository not found"; then
      echo "Add this key to the GitHub repo (Settings > Deploy keys, read-only), then rerun:"
      cat "$KEY.pub"
    else
      echo "Can't reach $REPO:" >&2
      echo "$err" >&2
    fi
    exit 1
  fi
  mkdir -p "$APP" && chown charter990: "$APP"
  as_app git clone -q "$REPO" "$APP"
else
  as_app git -C "$APP" pull -q --ff-only
fi

as_app mkdir -p "$APP/data/raw" "$HOME_DIR/dagster_home"
install -o charter990 -g charter990 -m 644 "$APP/deploy/dagster.yaml" "$HOME_DIR/dagster_home/dagster.yaml"
(cd "$APP/pipeline" && as_app uv sync -q --frozen)

install -m 644 "$APP"/deploy/charter990-dagster-*.service /etc/systemd/system/
systemctl daemon-reload

if [ ! -f /etc/charter990.env ]; then
  echo "Write /etc/charter990.env (see the top of this script), chmod 640, chown root:charter990, then rerun."
  exit 1
fi
systemctl enable -q charter990-dagster-webserver charter990-dagster-daemon
systemctl restart charter990-dagster-webserver charter990-dagster-daemon
echo "Running. Tunnel: ssh -L 3070:localhost:3070 root@<this server>, then http://localhost:3070"
