#!/usr/bin/env bash
#
# Prepares the server for kaskazi.jeffgicharu.com. Run once, as deploy, from a temporary folder
# made with mktemp -d that holds this script and the files it installs:
#
#   kaskazi-api@.service  kaskazi-gateway@.service  activate.sh  nginx.conf
#   security-headers.conf  unavailable.html  deploy-keys.pub
#   secrets/anthropic-key  secrets/anthropic-workspace  secrets/openai-key
#
# The folder is packed, sent and unpacked in the one SSH connection that runs it:
#
#   tar -czf - -C <folder> . | ssh deploy@<server> \
#     'd="$(mktemp -d)" && { tar -xzf - -C "$d" && bash "$d/provision.sh"; rc=$?; rm -rf -- "$d"; exit $rc; }'
#
# The folder holds the provider keys, so this script removes it when it ends, whether it
# finishes or fails. It is safe to run again: anything that exists is left alone. It never prints
# a secret; it shows variable names and file modes only.

set -euo pipefail

BASE=/var/www/kaskazi.jeffgicharu.com
SITE=https://kaskazi.jeffgicharu.com
DB=kaskazi
# The Cloudflare origin certificate this server's sites share (*.jeffgicharu.com, valid to 2041).
ORIGIN_CERT="${ORIGIN_CERT:-/etc/ssl/cloudflare/jeffgicharu.com.pem}"
ORIGIN_KEY="${ORIGIN_KEY:-/etc/ssl/cloudflare/jeffgicharu.com.key}"

HERE="$(cd "$(dirname "$0")" && pwd)"
case "$HERE" in
    /tmp/tmp.*) trap 'rm -rf -- "$HERE"' EXIT ;;
    *) echo "Run this from a folder made by mktemp -d, so it can be removed afterwards." >&2; exit 64 ;;
esac

step() { printf '\n== %s\n' "$1"; }

if [[ -z "$ORIGIN_CERT" || -z "$ORIGIN_KEY" ]] || ! sudo -n test -r "$ORIGIN_CERT" || ! sudo -n test -r "$ORIGIN_KEY"; then
    echo "Set ORIGIN_CERT and ORIGIN_KEY to the origin certificate and key this server uses." >&2
    exit 64
fi
for f in anthropic-key anthropic-workspace openai-key; do
    [[ -s "$HERE/secrets/$f" ]] || { echo "secrets/$f is missing or empty." >&2; exit 64; }
done

step "nginx before any change"
if ! sudo -n nginx -t 2>&1 | sed 's/^/  /'; then
    echo "nginx fails its own test already. Stopping without touching anything." >&2
    exit 70
fi

step "Application users"
# kaskazi-api owns the visitor data; kaskazi-gw holds the provider keys and may reach out.
for user in kaskazi-api kaskazi-gw; do
    if id "$user" >/dev/null 2>&1; then
        echo "$user exists"
    else
        sudo -n useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "$user"
        echo "created $user"
    fi
done

step "Folders"
sudo -n install -d -o deploy -g deploy -m 755 "$BASE" "$BASE/releases" "$BASE/slots" "$BASE/shared" \
    "$BASE/shared/static" "$BASE/shared/static/assets" "$BASE/shared/pages" "$BASE/shared/placeholder"
sudo -n install -d -o root -g root -m 755 "$BASE/shared/env"
# Uploaded sample documents. Only the API reads or writes here.
sudo -n install -d -o kaskazi-api -g kaskazi-api -m 750 "$BASE/shared/data" "$BASE/shared/data/uploads"
install -m 644 "$HERE/unavailable.html" "$BASE/shared/pages/unavailable.html"
# Until the first release, the site serves this page.
install -m 644 "$HERE/unavailable.html" "$BASE/shared/placeholder/index.html"
ls -ld "$BASE" "$BASE"/* "$BASE"/shared/* | sed 's/^/  /'

step "Slot ports"
# Each slot is an API and its gateway. The API finds its gateway from this file.
printf 'KASKAZI_API_PORT=8800\nKASKAZI_GATEWAY_PORT=8802\nKASKAZI_GATEWAY_URL=http://127.0.0.1:8802\n' \
    | sudo -n tee "$BASE/shared/env/blue.ports" >/dev/null
printf 'KASKAZI_API_PORT=8801\nKASKAZI_GATEWAY_PORT=8803\nKASKAZI_GATEWAY_URL=http://127.0.0.1:8803\n' \
    | sudo -n tee "$BASE/shared/env/green.ports" >/dev/null
sudo -n chmod 644 "$BASE/shared/env/blue.ports" "$BASE/shared/env/green.ports"
sudo -n cat "$BASE/shared/env/blue.ports" "$BASE/shared/env/green.ports" | sed 's/^/  /'

step "Database"
# Three roles, each with a connection limit:
#   kaskazi_owner   owns the schema; used only by migrations during activation
#   kaskazi_app     the API: no schema changes, no BYPASSRLS
#   kaskazi_gateway the gateway: its own schema only
# Passwords are generated here and go straight into the env files. Statement logging is off for
# this session, so no password reaches the server log either.
declare -A LIMITS=([kaskazi_owner]=3 [kaskazi_app]=12 [kaskazi_gateway]=6)
declare -A PASSWORDS=()
for role in kaskazi_owner kaskazi_app kaskazi_gateway; do
    if sudo -n -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$role'" | grep -q 1; then
        echo "role $role exists; its password is left as it is"
        sudo -n -u postgres psql -q -v ON_ERROR_STOP=1 -c "ALTER ROLE $role CONNECTION LIMIT ${LIMITS[$role]}"
    else
        PASSWORDS[$role]="$(openssl rand -hex 24)"
        # If psql ever quotes the statement in an error, the password is hidden from the output.
        sudo -n -u postgres psql -q -v ON_ERROR_STOP=1 2>&1 <<SQL | sed -E "s/PASSWORD '[^']*'/PASSWORD '…'/g"
SET log_statement = 'none';
SET log_min_duration_statement = -1;
CREATE ROLE $role LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT ${LIMITS[$role]} PASSWORD '${PASSWORDS[$role]}';
SQL
        echo "created role $role (connection limit ${LIMITS[$role]})"
    fi
done
if sudo -n -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
    echo "database $DB exists"
else
    sudo -n -u postgres psql -q -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DB OWNER kaskazi_owner"
    echo "created database $DB"
fi
sudo -n -u postgres psql -q -v ON_ERROR_STOP=1 -d "$DB" <<'SQL'
REVOKE ALL ON DATABASE kaskazi FROM PUBLIC;
GRANT CONNECT ON DATABASE kaskazi TO kaskazi_owner, kaskazi_app, kaskazi_gateway;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
sudo -n -u postgres psql -tA -d "$DB" -c "SELECT 'pgvector ' || extversion FROM pg_extension WHERE extname = 'vector'" | sed 's/^/  /'
sudo -n -u postgres psql -tA -c "SELECT rolname || ': limit ' || rolconnlimit || ', bypassrls ' || rolbypassrls FROM pg_roles WHERE rolname LIKE 'kaskazi_%' ORDER BY 1" | sed 's/^/  /'

dsn() { printf 'postgresql://%s:%s@127.0.0.1:5432/%s' "$1" "$2" "$DB"; }

step "Settings"
# Each file is created with its final owner and mode before anything is written into it.
write_env() {
    local file="$1" group="$2" mode="$3"
    sudo -n install -m "$mode" -o root -g "$group" /dev/null "$file"
    sudo -n tee -a "$file" >/dev/null
}
gateway_token=""
if sudo -n test -f "$BASE/shared/env/api.env"; then
    echo "api.env exists; left as it is"
else
    [[ -n "${PASSWORDS[kaskazi_app]:-}" ]] || { echo "kaskazi_app exists but api.env doesn't: set its password by hand." >&2; exit 1; }
    gateway_token="$(openssl rand -hex 32)"
    {
        echo "KASKAZI_SITE_URL=$SITE"
        echo "KASKAZI_DATABASE_URL=$(dsn kaskazi_app "${PASSWORDS[kaskazi_app]}")"
        echo "KASKAZI_SECRET=$(openssl rand -hex 32)"
        echo "KASKAZI_VAULT_KEY=$(openssl rand -base64 32)"
        echo "KASKAZI_GATEWAY_TOKEN=$gateway_token"
    } | write_env "$BASE/shared/env/api.env" kaskazi-api 640
    echo "wrote api.env"
fi
if sudo -n test -f "$BASE/shared/env/gateway.env"; then
    echo "gateway.env exists; left as it is"
else
    [[ -n "${PASSWORDS[kaskazi_gateway]:-}" && -n "$gateway_token" ]] || { echo "gateway.env can't be made without a new api.env." >&2; exit 1; }
    {
        echo "KASKAZI_GATEWAY_DATABASE_URL=$(dsn kaskazi_gateway "${PASSWORDS[kaskazi_gateway]}")"
        echo "KASKAZI_GATEWAY_TOKEN=$gateway_token"
        echo "ANTHROPIC_KEY=$(tr -d '\n' < "$HERE/secrets/anthropic-key")"
        echo "ANTHROPIC_WORKSPACE_ID=$(tr -d '\n' < "$HERE/secrets/anthropic-workspace")"
        echo "OPENAI_API_KEY=$(tr -d '\n' < "$HERE/secrets/openai-key")"
        echo "KASKAZI_CAP_DAY_USD=1.30"
        echo "KASKAZI_CAP_MONTH_USD=40"
        echo "KASKAZI_CAP_VISITOR_DAY_USD=0.04"
        echo "KASKAZI_CAP_ADDRESS_DAY_USD=0.12"
        echo "KASKAZI_AI_ENABLED=1"
    } | write_env "$BASE/shared/env/gateway.env" kaskazi-gw 640
    echo "wrote gateway.env"
fi
if sudo -n test -f "$BASE/shared/env/migrate.env"; then
    echo "migrate.env exists; left as it is"
else
    [[ -n "${PASSWORDS[kaskazi_owner]:-}" ]] || { echo "kaskazi_owner exists but migrate.env doesn't: set its password by hand." >&2; exit 1; }
    echo "KASKAZI_OWNER_DATABASE_URL=$(dsn kaskazi_owner "${PASSWORDS[kaskazi_owner]}")" \
        | write_env "$BASE/shared/env/migrate.env" root 600
    echo "wrote migrate.env"
fi
unset PASSWORDS gateway_token
for f in api.env gateway.env migrate.env; do
    printf '  %s  %s\n' "$(sudo -n stat -c '%A %U:%G' "$BASE/shared/env/$f")" "$f"
    printf '    names: %s\n' "$(sudo -n cut -d= -f1 "$BASE/shared/env/$f" | tr '\n' ' ')"
done

step "systemd units"
for unit in kaskazi-api@.service kaskazi-gateway@.service; do
    sudo -n install -o root -g root -m 644 "$HERE/$unit" "/etc/systemd/system/$unit"
    echo "installed $unit"
done
sudo -n systemctl daemon-reload
sudo -n systemd-analyze verify kaskazi-api@blue.service kaskazi-gateway@blue.service 2>&1 \
    | grep -v -i 'condition' | sed 's/^/  /' || true

step "Activation script"
sudo -n install -o root -g root -m 755 "$HERE/activate.sh" /usr/local/bin/kaskazi-activate
ls -l /usr/local/bin/kaskazi-activate | sed 's/^/  /'

step "Deploy keys"
# Two keys, each restricted to one command: uploading into releases/, and activating a sha.
touch ~/.ssh/authorized_keys
backup=~/.ssh/authorized_keys.bak-$(date -u +%F)-kaskazi
[[ -e "$backup" ]] || { cp -p ~/.ssh/authorized_keys "$backup"; echo "backed up authorized_keys to $backup"; }
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    key="$(awk '{print $(NF-1)}' <<<"$line")"
    if grep -qF "$key" ~/.ssh/authorized_keys; then
        echo "already present: $(awk '{print $NF}' <<<"$line")"
    else
        printf '%s\n' "$line" >> ~/.ssh/authorized_keys
        echo "added: $(awk '{print $NF}' <<<"$line")"
    fi
done < "$HERE/deploy-keys.pub"
kept="$(grep -c . "$backup")"
if cmp -s <(head -n "$kept" ~/.ssh/authorized_keys) "$backup"; then
    echo "lines 1 to $kept are unchanged"
else
    echo "lines 1 to $kept do NOT match the backup" >&2
fi
grep -n 'kaskazi-\(upload\|activate\)$' ~/.ssh/authorized_keys | sed -E 's/ssh-ed25519 [A-Za-z0-9+/=]+/ssh-ed25519 …/; s/^/  /'

step "nginx"
sudo -n install -d -o root -g root -m 755 /etc/nginx/kaskazi
sudo -n install -o root -g root -m 644 "$HERE/security-headers.conf" /etc/nginx/kaskazi/security-headers.conf
# Until the first release, blue is named live; nothing listens there yet, and pages come from the
# placeholder.
sudo -n test -f /etc/nginx/kaskazi/live-upstream.conf \
    || echo "server 127.0.0.1:8800;" | sudo -n tee /etc/nginx/kaskazi/live-upstream.conf >/dev/null
sudo -n test -f /etc/nginx/kaskazi/live-root.conf \
    || echo "set \$kaskazi_web $BASE/shared/placeholder;" | sudo -n tee /etc/nginx/kaskazi/live-root.conf >/dev/null
sed -e "s#__ORIGIN_CERT__#$ORIGIN_CERT#" -e "s#__ORIGIN_KEY__#$ORIGIN_KEY#" "$HERE/nginx.conf" \
    | sudo -n tee /etc/nginx/sites-available/kaskazi.jeffgicharu.com >/dev/null
sudo -n ln -sfn /etc/nginx/sites-available/kaskazi.jeffgicharu.com /etc/nginx/sites-enabled/kaskazi.jeffgicharu.com
if sudo -n nginx -t 2>&1 | sed 's/^/  /'; then
    sudo -n systemctl reload nginx
    echo "nginx reloaded with kaskazi.jeffgicharu.com"
else
    sudo -n rm -f /etc/nginx/sites-enabled/kaskazi.jeffgicharu.com
    sudo -n nginx -t >/dev/null 2>&1 && echo "the kaskazi site file failed nginx's test and was taken out again" >&2
    exit 1
fi

step "Checks"
command -v rrsync >/dev/null && echo "rrsync: $(command -v rrsync)"
curl -s -o /dev/null -w '  / through nginx before the first release: %{http_code}\n' --insecure \
    --resolve kaskazi.jeffgicharu.com:443:127.0.0.1 https://kaskazi.jeffgicharu.com/ || true
curl -s -o /dev/null -w '  /api/healthz before the first release: %{http_code} (503 expected)\n' --insecure \
    --resolve kaskazi.jeffgicharu.com:443:127.0.0.1 https://kaskazi.jeffgicharu.com/api/healthz || true
free -m | head -2 | sed 's/^/  /'
df -h /var/www | tail -1 | sed 's/^/  /'

echo
echo "Provisioning finished."
