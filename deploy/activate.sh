#!/usr/bin/env bash
#
# Puts an uploaded Kaskazi release live. Installed as /usr/local/bin/kaskazi-activate, owned by
# root, and the forced command of the activation deploy key: the only thing that key can ask for
# is "activate the release with this commit id".
#
# A release is a folder named by its commit sha, holding the Python runtime with the app, the
# built web app and REVISION. There are two slots, each an API and its model gateway:
#   blue:  API 8800, gateway 8802        green: API 8801, gateway 8803
# The new release starts in the idle slot beside the live one and must pass its checks there.
# Only then does nginx send visitors to it, and the checks run again through nginx; if they fail,
# nginx goes back to the release before, which kept running throughout.
#
# Release code never runs as root or as deploy. Every step that runs it (the CPU check, the
# migrations, the corpus load, the checks) is a transient systemd unit under the app's own user,
# with the same protections as the services, and secrets come from the env files through
# systemd, never on a command line.
#
# To go back by hand, run this with the sha of an earlier release still in releases/.

set -euo pipefail

BASE=/var/www/kaskazi.jeffgicharu.com
HOST=kaskazi.jeffgicharu.com
ENV_DIR=$BASE/shared/env
NGINX_DIR=/etc/nginx/kaskazi
KEEP_RELEASES=3

sha="${SSH_ORIGINAL_COMMAND:-${1:-}}"
if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Expected a 40-character commit sha." >&2
    exit 64
fi
release="$BASE/releases/$sha"
short="${sha:0:7}"

# The upload key can write anything into releases/, including a symlink named like a sha. A
# release must be a real folder.
if [[ ! -d "$release" || -L "$release" ]]; then
    echo "Release $short is not a folder in $BASE/releases." >&2
    exit 66
fi

# One activation at a time.
exec 9>"$BASE/.activate.lock"
flock -n 9 || { echo "Another activation is running." >&2; exit 75; }

# nginx is shared by every site here. If its configuration is already broken, touch nothing.
if ! sudo -n nginx -t >/dev/null 2>&1; then
    echo "nginx fails its own test before this release changed anything. Nothing was changed." >&2
    exit 70
fi

# --- The release is complete and is what it says it is -----------------------------------------

for file in REVISION MANIFEST.sha256 python/bin/python3 web/index.html pages/unavailable.html; do
    if [[ ! -e "$release/$file" ]]; then
        echo "Release $short is incomplete: $file is missing." >&2
        exit 66
    fi
done
if [[ "$(cat "$release/REVISION")" != "$sha" ]]; then
    echo "Release $short carries the wrong REVISION." >&2
    exit 66
fi
if ! (cd "$release" && sha256sum --quiet --strict -c MANIFEST.sha256); then
    echo "Release $short does not match its manifest." >&2
    exit 66
fi
# Nothing in it may be writable by anyone but deploy, and every link must stay inside it. The
# services read it as other users, so the runtime must be readable and executable by others
# (CI uploads with --chmod=go+rX,go-w).
writable="$(find "$release" ! -type l -perm /022 -print -quit)"
if [[ -n "$writable" ]]; then
    echo "Release $short has a group- or world-writable file: ${writable#"$release"/}" >&2
    exit 66
fi
while IFS= read -r -d '' link; do
    target="$(readlink -f -- "$link" || true)"
    if [[ "$target" != "$release"/* ]]; then
        echo "Release $short has a link leading outside it: ${link#"$release"/}" >&2
        exit 66
    fi
done < <(find "$release" -type l -print0)
python_bin="$(readlink -f -- "$release/python/bin/python3")"
if [[ -z "$(find "$python_bin" -perm -o=rx -print -quit)" ]] \
    || [[ -n "$(find "$release" ! -type l ! -perm -o=r -print -quit)" ]] \
    || [[ -n "$(find "$release" -type d ! -perm -o=x -print -quit)" ]]; then
    echo "Release $short isn't readable by the services' users (upload with --chmod=go+rX,go-w)." >&2
    exit 66
fi
if ! grep -q '<meta name="robots" content="noindex, nofollow">' "$release/web/index.html"; then
    echo "Release $short's index.html is missing its noindex tag." >&2
    exit 66
fi

# --- Running release code ----------------------------------------------------------------------

# as_app <user> <env file or ""> <network: local|none> <args...>
# Runs the release's Python as <user> in a transient, protected unit and passes its output back.
as_app() {
    local user="$1" env_file="$2" network="$3"
    shift 3
    local props=(
        --quiet --wait --pipe --collect
        -p "User=$user" -p "Group=$user"
        -p NoNewPrivileges=yes -p PrivateTmp=yes -p PrivateDevices=yes -p ProtectHome=yes
        -p ProtectSystem=strict -p ProtectKernelTunables=yes -p ProtectKernelModules=yes
        -p ProtectControlGroups=yes -p RestrictSUIDSGID=yes -p RestrictNamespaces=yes
        -p LockPersonality=yes -p MemoryDenyWriteExecute=yes -p SystemCallArchitectures=native
        -p CapabilityBoundingSet= -p MemoryMax=384M -p MemorySwapMax=0
        -p RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX"
        -p WorkingDirectory=/
    )
    if [[ "$network" == none ]]; then
        props+=(-p PrivateNetwork=yes)
    else
        props+=(-p IPAddressDeny=any -p IPAddressAllow=localhost)
    fi
    if [[ -n "$env_file" ]]; then
        props+=(-p "EnvironmentFile=$env_file")
    fi
    sudo -n systemd-run "${props[@]}" "$release/python/bin/python3" -I -m kaskazi.release "$@"
}

# The processor here has the baseline x86-64 instructions only (SSE3 at most). Prove that every
# compiled module in this build imports on it, as each service's own user, before anything else.
for user in kaskazi-api kaskazi-gw; do
    if ! reported="$(as_app "$user" "" none version 2>&1)" || [[ "$reported" != "$sha" ]]; then
        echo "Release $short does not run on this server as $user:" >&2
        echo "$reported" >&2
        exit 1
    fi
done

# Migrations only ever add (expand first, contract in a later release), so the live release keeps
# working on the database while this one starts. Then this release's corpus and prompts are
# loaded beside the live release's, keyed by hash.
echo "Migrating the database and loading release $short's documents and prompts"
as_app kaskazi-api "$ENV_DIR/migrate.env" local migrate
as_app kaskazi-api "$ENV_DIR/migrate.env" local load --release "$sha"

# --- Start the idle slot -----------------------------------------------------------------------

api_port() { case "$1" in blue) echo 8800 ;; green) echo 8801 ;; esac; }
gw_port() { case "$1" in blue) echo 8802 ;; green) echo 8803 ;; esac; }

live_port="$(grep -oE '127\.0\.0\.1:880[01]' "$NGINX_DIR/live-upstream.conf" | head -n1 | cut -d: -f2 || true)"
case "$live_port" in
    8800) live=blue; idle=green ;;
    8801) live=green; idle=blue ;;
    *) echo "Can't tell which slot is live from $NGINX_DIR/live-upstream.conf." >&2; exit 70 ;;
esac
live_release="$(readlink -f "$BASE/slots/$live" 2>/dev/null || true)"
first_release=""
if [[ -z "$live_release" ]] || ! systemctl is-active --quiet "kaskazi-api@$live.service"; then
    first_release=yes
fi
if [[ -z "$first_release" && "$live_release" == "$release" ]]; then
    echo "Release $short is already live in the $live slot."
    exit 0
fi
idle_api="$(api_port "$idle")"
idle_gw="$(gw_port "$idle")"

# The new release's fingerprinted files join the shared folder before any page can ask for them.
mkdir -p "$BASE/shared/static/assets"
rsync -a --ignore-existing "$release/web/assets/" "$BASE/shared/static/assets/"
install -m 644 "$release/pages/unavailable.html" "$BASE/shared/pages/unavailable.html"

echo "Starting release $short in the $idle slot (API $idle_api, gateway $idle_gw)"
sudo -n systemctl stop "kaskazi-api@$idle.service" "kaskazi-gateway@$idle.service" 2>/dev/null || true
ln -sfn "$release" "$BASE/slots/$idle.new"
mv -Tf "$BASE/slots/$idle.new" "$BASE/slots/$idle"
sudo -n systemctl start "kaskazi-gateway@$idle.service"
sudo -n systemctl start "kaskazi-api@$idle.service"

abandon() {
    sudo -n systemctl stop "kaskazi-api@$idle.service" "kaskazi-gateway@$idle.service" 2>/dev/null || true
    echo "$1" >&2
    exit 1
}
show_logs() {
    sudo -n journalctl -u "kaskazi-api@$idle.service" -n 25 --no-pager >&2 || true
    sudo -n journalctl -u "kaskazi-gateway@$idle.service" -n 25 --no-pager >&2 || true
}
reports_release() {
    curl --silent --max-time 3 --head "$1" 2>/dev/null | grep -qi "^x-release: $sha"
}

started=""
for _ in $(seq 1 40); do
    if reports_release "http://127.0.0.1:$idle_gw/healthz" && reports_release "http://127.0.0.1:$idle_api/api/healthz"; then
        started=yes
        break
    fi
    sleep 1
done
if [[ -z "$started" ]]; then
    show_logs
    abandon "Release $short did not start. The site is unchanged."
fi

# --- The gate: visitors are still on the release before ----------------------------------------

echo "Checking release $short in the idle slot"
if ! as_app kaskazi-api "$ENV_DIR/api.env" local check --api "http://127.0.0.1:$idle_api" \
        --gateway "http://127.0.0.1:$idle_gw" --release "$sha"; then
    show_logs
    abandon "Release $short failed its checks and was not put live."
fi

# --- Switch: two include files, one reload -----------------------------------------------------

write_live() {
    local slot="$1" target="$2"
    printf 'server 127.0.0.1:%s;\n' "$(api_port "$slot")" | sudo -n tee "$NGINX_DIR/live-upstream.conf.new" >/dev/null
    printf 'set $kaskazi_web %s/web;\n' "$target" | sudo -n tee "$NGINX_DIR/live-root.conf.new" >/dev/null
    sudo -n mv -f "$NGINX_DIR/live-upstream.conf.new" "$NGINX_DIR/live-upstream.conf"
    sudo -n mv -f "$NGINX_DIR/live-root.conf.new" "$NGINX_DIR/live-root.conf"
}
previous_upstream="$(cat "$NGINX_DIR/live-upstream.conf")"
previous_root="$(cat "$NGINX_DIR/live-root.conf")"
restore_nginx() {
    printf '%s\n' "$previous_upstream" | sudo -n tee "$NGINX_DIR/live-upstream.conf" >/dev/null
    printf '%s\n' "$previous_root" | sudo -n tee "$NGINX_DIR/live-root.conf" >/dev/null
    sudo -n nginx -t >/dev/null 2>&1 && sudo -n systemctl reload nginx
}

echo "Switching visitors to release $short"
write_live "$idle" "$release"
if ! sudo -n nginx -t >/dev/null 2>&1; then
    restore_nginx || true
    abandon "nginx refused the switch. Visitors stay on the release before."
fi
if ! sudo -n systemctl reload nginx; then
    restore_nginx || true
    abandon "nginx would not reload for the switch. Its files are back as they were."
fi

# Through nginx, from this machine, under the public name. The certificate is the Cloudflare
# origin certificate, which curl doesn't know, hence --insecure; nothing leaves this machine.
fetch() {
    curl --silent --show-error --insecure --max-time 15 --resolve "$HOST:443:127.0.0.1" "$@"
}
through_nginx() {
    local headers asset stream page
    page="$(mktemp)"
    # The page copy is removed on every way out of this function.
    trap 'rm -f -- "$page"; trap - RETURN' RETURN
    for _ in $(seq 1 10); do
        fetch --head "https://$HOST/api/healthz" 2>/dev/null | grep -qi "^x-release: $sha" && break
        sleep 1
    done
    fetch --head "https://$HOST/api/healthz" | grep -qi "^x-release: $sha" || { echo "The API through nginx isn't $short." >&2; return 1; }
    for path in / /chat /console /no-such-page; do
        headers="$(fetch --fail --dump-header - --output "$page" "https://$HOST$path")" || { echo "$path did not load." >&2; return 1; }
        grep -qi '^x-robots-tag: noindex, nofollow' <<<"$headers" || { echo "$path has no noindex header." >&2; return 1; }
        grep -qi '^content-security-policy:' <<<"$headers" || { echo "$path has no CSP header." >&2; return 1; }
        cmp -s "$page" "$release/web/index.html" || { echo "$path is not release $short's page." >&2; return 1; }
    done
    fetch --head "https://$HOST/api/healthz" | grep -qi '^x-robots-tag: noindex, nofollow' || { echo "The API has no noindex header." >&2; return 1; }
    asset=""
    for f in "$release"/web/assets/*.js; do asset="$(basename "$f")"; break; done
    [[ -n "$asset" ]] || { echo "Release $short has no scripts in web/assets." >&2; return 1; }
    fetch --fail --output /dev/null "https://$HOST/assets/$asset" || { echo "assets/$asset did not load." >&2; return 1; }
    # An event stream opens with a retry line, also for a visitor who hasn't signed in. It stays
    # open, so curl is cut off after a few seconds.
    stream="$(fetch --max-time 4 "https://$HOST/api/stream" 2>/dev/null || true)"
    grep -q '^retry:' <<<"$stream" || { echo "Event streams don't get through nginx." >&2; return 1; }
}

echo "Checking release $short through nginx"
if ! through_nginx; then
    # For a first release, the files before named the placeholder, which is where visitors should
    # go back to.
    if restore_nginx; then
        if [[ -n "$first_release" ]]; then
            abandon "Release $short failed through nginx. The site is back on its placeholder page."
        fi
        abandon "Release $short failed through nginx. Visitors are back on the release before."
    fi
    abandon "Release $short failed through nginx and nginx could not be pointed back. Look now."
fi

# The live slot is the one that starts at boot.
sudo -n systemctl enable "kaskazi-gateway@$idle.service" "kaskazi-api@$idle.service" >/dev/null 2>&1 || true
sudo -n systemctl disable "kaskazi-gateway@$live.service" "kaskazi-api@$live.service" >/dev/null 2>&1 || true

# Requests in hand on the old release get time to finish; its event streams close and browsers
# reconnect to the new one.
sleep 15
sudo -n systemctl stop "kaskazi-api@$live.service" "kaskazi-gateway@$live.service" 2>/dev/null || true

# --- Tidy up -----------------------------------------------------------------------------------

echo "Keeping the newest $KEEP_RELEASES releases"
cd "$BASE/releases"
ls -1t | tail -n +$((KEEP_RELEASES + 1)) | while read -r old; do
    [[ "$old" =~ ^[0-9a-f]{40}$ && "$old" != "$sha" && "$BASE/releases/$old" != "$live_release" ]] || continue
    rm -rf -- "${BASE:?}/releases/$old"
done

# Fingerprinted files are kept while any release on disk still refers to them.
keep="$(mktemp)"
trap 'rm -f "$keep"' EXIT
for kept in "$BASE"/releases/*/; do
    [[ -d "$kept/web/assets" ]] && (cd "$kept/web/assets" && find . -type f) >>"$keep"
done
sort -u "$keep" -o "$keep"
if [[ -s "$keep" ]]; then
    (cd "$BASE/shared/static/assets" && find . -type f) | sort | comm -23 - "$keep" | while read -r stale; do
        rm -f -- "$BASE/shared/static/assets/$stale"
    done
fi

echo "Release $short is live in the $idle slot."
