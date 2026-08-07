#!/bin/sh
set -eu

package_dir=${PALWORLD_PACKAGE_DIR:-/pal/Package}
saved_dir="$package_dir/Pal/Saved"
settings_source=${PALWORLD_CONFIG_SOURCE:-/run/palworldselfhost/PalWorldSettings.ini}
settings_dir="$saved_dir/Config/LinuxServer"
settings_target="$settings_dir/PalWorldSettings.ini"
ready_file=${PALWORLD_READY_FILE:-/tmp/palworldselfhost.ready}
pid_file=${PALWORLD_PID_FILE:-/tmp/palworldselfhost.pid}
child=

# shellcheck disable=SC2329  # invoked by EXIT trap
cleanup() {
    rm -f "$ready_file" "$pid_file"
}

# Ask Palworld to save and shut itself down before falling back to a signal.
# shellcheck disable=SC2329  # invoked by the TERM/INT trap handler
rest_shutdown() {
    [ "${PALWORLD_DISABLE_REST_SHUTDOWN:-false}" != true ] || return 1
    command -v curl >/dev/null 2>&1 || return 1
    option_line=$(grep -E '^OptionSettings=\(.*\)[[:space:]]*$' "$settings_target" | tail -n 1) || return 1
    admin_password=$(printf '%s\n' "$option_line" | sed -n 's/.*AdminPassword="\([^"]*\)".*/\1/p')
    rest_port=$(printf '%s\n' "$option_line" | sed -n 's/.*RESTAPIPort=\([0-9][0-9]*\).*/\1/p')
    [ -n "$rest_port" ] || rest_port=8212
    case "$rest_port" in *[!0-9]*|'') return 1 ;; esac
    rest_url="http://127.0.0.1:$rest_port/v1/api"
    curl -fsS --max-time 30 -u "admin:$admin_password" \
        -H 'Content-Type: application/json' -X POST -d '{}' "$rest_url/save" >/dev/null || return 1
    curl -fsS --max-time 8 -u "admin:$admin_password" \
        -H 'Content-Type: application/json' -X POST \
        -d '{"waittime":1,"message":"Container is stopping."}' "$rest_url/shutdown" >/dev/null
}

# shellcheck disable=SC2329  # invoked by TERM/INT traps
forward_shutdown() {
    trap - TERM INT
    cleanup
    if [ -n "$child" ]; then
        rest_shutdown || kill -INT -- "-$child" 2>/dev/null || true
        wait "$child" || true
    fi
    exit 0
}

trap forward_shutdown TERM INT
trap cleanup EXIT

if [ "${PALWORLD_SKIP_CHOWN:-false}" != true ]; then
    sudo mkdir -p "$saved_dir"
    sudo chown -R user:usergroup "$saved_dir"
fi
mkdir -p "$settings_dir"

if [ -r "$settings_source" ]; then
    if ! grep -Eq '^OptionSettings=\(.*\)[[:space:]]*$' "$settings_source"; then
        echo "refusing invalid PalWorldSettings.ini: OptionSettings is absent or multiline" >&2
        exit 64
    fi
    umask 077
    settings_tmp="$settings_dir/.PalWorldSettings.ini.$$"
    cp "$settings_source" "$settings_tmp"
    chmod 600 "$settings_tmp"
    if [ "$(id -u)" -eq 0 ]; then
        chown user:usergroup "$settings_tmp"
    fi
    mv -f "$settings_tmp" "$settings_target"
elif [ ! -s "$settings_target" ]; then
    echo "missing settings source $settings_source and no persisted PalWorldSettings.ini" >&2
    exit 66
fi

select_world_id() {
    explicit=${PALWORLD_WORLD_ID:-}
    save_root="$saved_dir/SaveGames/0"
    selected=
    count=0
    only=
    mkdir -p "$save_root"
    for level in "$save_root"/*/Level.sav; do
        [ -f "$level" ] && [ ! -L "$level" ] || continue
        candidate=${level%/Level.sav}; candidate=${candidate##*/}
        printf '%s\n' "$candidate" | grep -Eq '^[0-9A-Fa-f]{32}$' || continue
        count=$((count + 1)); only=$candidate
    done
    game_user="$settings_dir/GameUserSettings.ini"
    if [ -f "$game_user" ] && [ ! -L "$game_user" ]; then
        selected=$(sed -n 's/^DedicatedServerName=\([0-9A-Fa-f]\{32\}\)[[:space:]]*$/\1/p' "$game_user" | tail -n 1)
    fi
    if [ -n "$explicit" ]; then
        printf '%s\n' "$explicit" | grep -Eq '^[0-9A-Fa-f]{32}$' || {
            echo "PALWORLD_WORLD_ID must be exactly 32 hexadecimal characters" >&2; return 64;
        }
        if [ "$count" -gt 0 ] && [ ! -f "$save_root/$explicit/Level.sav" ]; then
            echo "PALWORLD_WORLD_ID does not match an existing persisted world" >&2; return 65
        fi
        printf '%s\n' "$explicit"; return
    fi
    if [ "$count" -eq 1 ]; then
        printf '%s\n' "$only"; return
    fi
    if [ "$count" -gt 1 ]; then
        [ -n "$selected" ] && [ -f "$save_root/$selected/Level.sav" ] || {
            echo "multiple persisted worlds exist and GameUserSettings.ini does not select one" >&2; return 65;
        }
        printf '%s\n' "$selected"; return
    fi
    if [ -n "$selected" ]; then
        printf '%s\n' "$selected"; return
    fi
    generated=$(tr -d '-' </proc/sys/kernel/random/uuid 2>/dev/null || true)
    printf '%s\n' "$generated" | grep -Eq '^[0-9A-Fa-f]{32}$' || {
        echo "could not generate a persistent world ID" >&2; return 70;
    }
    printf '%s\n' "$generated"
}

world_id=$(select_world_id)
game_user="$settings_dir/GameUserSettings.ini"
game_user_tmp="$settings_dir/.GameUserSettings.ini.$$"
matches=0
if [ -f "$game_user" ] && [ ! -L "$game_user" ]; then
    matches=$(grep -Ec '^DedicatedServerName=' "$game_user" || true)
fi
if [ "$matches" -gt 1 ]; then
    echo "GameUserSettings.ini contains multiple DedicatedServerName values" >&2
    exit 65
elif [ "$matches" -eq 1 ]; then
    sed "s/^DedicatedServerName=.*/DedicatedServerName=$world_id/" "$game_user" >"$game_user_tmp"
else
    if [ -f "$game_user" ]; then cat "$game_user" >"$game_user_tmp"; else printf '%s\n' '[/Script/Pal.PalGameLocalSettings]' >"$game_user_tmp"; fi
    printf 'DedicatedServerName=%s\n' "$world_id" >>"$game_user_tmp"
fi
chmod 600 "$game_user_tmp"
if [ "$(id -u)" -eq 0 ]; then chown user:usergroup "$game_user_tmp"; fi
mv -f "$game_user_tmp" "$game_user"
printf 'Selected persistent world %s\n' "$world_id"

if [ ! -f "$package_dir/PalServer.sh" ] || [ ! -r "$package_dir/PalServer.sh" ]; then
    echo "missing readable $package_dir/PalServer.sh" >&2
    exit 69
fi

if [ "$(id -u)" -eq 0 ]; then
    (trap - INT TERM; exec setsid sudo -u user -g usergroup /bin/sh "$package_dir/PalServer.sh" "$@") &
else
    (trap - INT TERM; exec setsid /bin/sh "$package_dir/PalServer.sh" "$@") &
fi
child=$!
printf '%s\n' "$child" >"$pid_file"
: >"$ready_file"

set +e
wait "$child"
rc=$?
set -e
exit "$rc"
