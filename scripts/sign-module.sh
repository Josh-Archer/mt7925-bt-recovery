#!/usr/bin/env bash
#
# scripts/sign-module.sh
# DKMS Secure Boot kernel module signing and MOK enrollment script.
#
# Part of the MT7925 Bluetooth recovery driver project.
#
set -euo pipefail

export PATH="$PATH:/sbin:/usr/sbin:/usr/local/sbin"

usage() {
    cat <<'EOF'
Usage:
  scripts/sign-module.sh [options] [sign] [KERNEL_VERSION] [MODULE_PATH]
  scripts/sign-module.sh [options] enroll

Commands:
  sign          Sign the btmtk.ko kernel module (default command).
  enroll        Enroll the MOK public certificate into UEFI MOK database via mokutil.

Options:
  --key PATH      Path to MOK private key file (overrides MOK_KEY).
  --cert PATH     Path to MOK X.509 certificate file (overrides MOK_CERT).
  --hash ALGO     Hash algorithm for module signing: sha256, sha384, sha512 (default: sha256).
  --module PATH   Path to btmtk.ko kernel module to sign (handles .ko, .ko.gz, .ko.xz, .ko.zst).
  --kernel VER    Target kernel version (default: running kernel or $kernelver).
  --sign-file BIN Path to kernel sign-file binary (overrides auto-detection).
  --config PATH   Path to signing configuration file.
  -h, --help      Display this help message.

Configuration:
  Configuration can be supplied via environment variables or a configuration file.
  Private keys and certificates must come from a single consistent source.
  Private keys must have 0600 or 0400 permissions and be owned by root or the caller.
  Default configuration file locations checked:
    1. Value of SIGN_CONF environment variable (if set)
    2. /etc/mt7925-bt-recovery/sign.conf
    3. /etc/dkms/sign.conf
    4. /etc/dkms/framework.conf.d/*.conf and /etc/dkms/framework.conf

  For automatic signing across kernel upgrades with DKMS 3.0.4 or newer,
  configure mok_signing_key and mok_certificate in a drop-in under
  /etc/dkms/framework.conf.d/.

  Variables:
    MOK_KEY       Path to private signing key.
    MOK_CERT      Path to X.509 public certificate (PEM or DER format).
    HASH_ALGO     Signature digest algorithm: sha256, sha384, sha512 (default: sha256).
    SIGN_FILE     Path to kernel sign-file binary.

  Notes:
    - Signing is performed with the kernel's sign-file tool; kmodsign is not supported.
    - Native mok_signing_key/mok_certificate settings require DKMS 3.0.4 or newer.
    - Ubuntu's DKMS 2.8.x packages already auto-sign with the Ubuntu MOK.

Behavior:
  - If Secure Boot is enabled and signing is not configured (or files are missing),
    the script terminates with an error (exit code 1).
  - If Secure Boot is disabled or unknown and signing is not configured,
    the script emits a warning and exits cleanly (exit code 0).
  - If signing is configured but any required file is missing or invalid, the
    script terminates immediately with a descriptive error (exit code 1).
EOF
}

# Defaults
COMMAND=""
ENROLL_CLEANUP_DIR=""
CLI_KEY=""
CLI_CERT=""
CLI_HASH=""
CLI_MODULE=""
CLI_KERNEL=""
CLI_SIGN_FILE=""
CLI_CONFIG=""

# Positional arguments accumulator
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --key)
            if [[ $# -lt 2 ]]; then
                echo "Error: --key requires a path argument." >&2
                exit 1
            fi
            CLI_KEY="$2"
            shift 2
            ;;
        --cert)
            if [[ $# -lt 2 ]]; then
                echo "Error: --cert requires a path argument." >&2
                exit 1
            fi
            CLI_CERT="$2"
            shift 2
            ;;
        --hash)
            if [[ $# -lt 2 ]]; then
                echo "Error: --hash requires an algorithm argument (e.g. sha256)." >&2
                exit 1
            fi
            CLI_HASH="$2"
            shift 2
            ;;
        --module)
            if [[ $# -lt 2 ]]; then
                echo "Error: --module requires a path argument." >&2
                exit 1
            fi
            CLI_MODULE="$2"
            shift 2
            ;;
        --kernel)
            if [[ $# -lt 2 ]]; then
                echo "Error: --kernel requires a version argument." >&2
                exit 1
            fi
            CLI_KERNEL="$2"
            shift 2
            ;;
        --sign-file)
            if [[ $# -lt 2 ]]; then
                echo "Error: --sign-file requires a path argument." >&2
                exit 1
            fi
            CLI_SIGN_FILE="$2"
            shift 2
            ;;
        --config)
            if [[ $# -lt 2 ]]; then
                echo "Error: --config requires a path argument." >&2
                exit 1
            fi
            CLI_CONFIG="$2"
            shift 2
            ;;
        sign|enroll)
            if [[ -z "$COMMAND" ]]; then
                COMMAND="$1"
            else
                POSITIONAL_ARGS+=("$1")
            fi
            shift
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ -z "$COMMAND" ]]; then
    COMMAND="sign"
fi

# Helpers for configuration parsing
strip_val() {
    local v="$1"
    if [[ "$v" =~ ^[[:space:]]*\"([^\"]*)\"([[:space:]]*#.*)?$ ]]; then
        echo "${BASH_REMATCH[1]}"
    elif [[ "$v" =~ ^[[:space:]]*\'([^\']*)\'([[:space:]]*#.*)?$ ]]; then
        echo "${BASH_REMATCH[1]}"
    elif [[ "$v" =~ ^[[:space:]]*([^#]*)[[:space:]]*(#.*)?$ ]]; then
        local unquoted="${BASH_REMATCH[1]}"
        if [[ "$unquoted" =~ ^(.*[^[:space:]])[[:space:]]*$ ]]; then
            echo "${BASH_REMATCH[1]}"
        else
            echo "$unquoted"
        fi
    else
        echo "$v"
    fi
}

PARSED_KEY=""
PARSED_CERT=""
PARSED_HASH=""
PARSED_SIGN_FILE=""

parse_config_file() {
    local conf="$1"
    [[ -f "$conf" && -r "$conf" ]] || return 0

    while IFS='=' read -r key val || [[ -n "$key" ]]; do
        key="$(echo "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [[ "$key" =~ ^# ]] && continue
        [[ -z "$key" ]] && continue
        val="$(strip_val "$val")"
        case "$key" in
            MOK_KEY|mok_signing_key)
                PARSED_KEY="${PARSED_KEY:-$val}"
                ;;
            MOK_CERT|mok_certificate)
                PARSED_CERT="${PARSED_CERT:-$val}"
                ;;
            HASH_ALGO)
                PARSED_HASH="${PARSED_HASH:-$val}"
                ;;
            SIGN_FILE|sign_file)
                PARSED_SIGN_FILE="${PARSED_SIGN_FILE:-$val}"
                ;;
        esac
    done < "$conf"
}

KEY_FILE=""
CERT_FILE=""
CONF_HASH=""
CONF_SIGN_FILE=""

resolve_credentials() {
    # If command is enroll, only certificate is required
    if [[ "$COMMAND" == "enroll" ]]; then
        if [[ -n "$CLI_CERT" ]]; then
            CERT_FILE="$CLI_CERT"
            return 0
        fi

        local env_c="${MOK_CERT:-${MOK_CERTIFICATE:-}}"
        if [[ -n "$env_c" ]]; then
            CERT_FILE="$env_c"
            return 0
        fi

        local explicit_conf=""
        if [[ -n "$CLI_CONFIG" ]]; then
            if [[ ! -f "$CLI_CONFIG" ]]; then
                echo "Error: Specified configuration file not found at '$CLI_CONFIG'." >&2
                exit 1
            fi
            explicit_conf="$CLI_CONFIG"
        elif [[ -n "${SIGN_CONF:-}" ]]; then
            if [[ ! -f "$SIGN_CONF" ]]; then
                echo "Error: Configuration file specified by SIGN_CONF not found at '$SIGN_CONF'." >&2
                exit 1
            fi
            explicit_conf="$SIGN_CONF"
        fi

        if [[ -n "$explicit_conf" ]]; then
            PARSED_KEY=""
            PARSED_CERT=""
            PARSED_HASH=""
            PARSED_SIGN_FILE=""
            parse_config_file "$explicit_conf"
            CERT_FILE="$PARSED_CERT"
            return 0
        fi

        local conf_candidates=(
            "/etc/mt7925-bt-recovery/sign.conf"
            "/etc/dkms/sign.conf"
        )
        local dropin
        for dropin in /etc/dkms/framework.conf.d/*.conf; do
            [[ -f "$dropin" ]] && conf_candidates+=("$dropin")
        done
        conf_candidates+=("/etc/dkms/framework.conf")
        for conf in "${conf_candidates[@]}"; do
            if [[ -f "$conf" ]]; then
                PARSED_KEY=""
                PARSED_CERT=""
                PARSED_HASH=""
                PARSED_SIGN_FILE=""
                parse_config_file "$conf"
                if [[ -n "$PARSED_CERT" ]]; then
                    CERT_FILE="$PARSED_CERT"
                    return 0
                fi
            fi
        done
        return 0
    fi

    # Command is sign: require both key and cert to come from a consistent source

    # 1. Command-line options
    if [[ -n "$CLI_KEY" || -n "$CLI_CERT" ]]; then
        if [[ -z "$CLI_KEY" ]]; then
            echo "Error: Incomplete Secure Boot configuration: MOK_CERT is set but MOK_KEY is missing." >&2
            exit 1
        fi
        if [[ -z "$CLI_CERT" ]]; then
            echo "Error: Incomplete Secure Boot configuration: MOK_KEY is set but MOK_CERT is missing." >&2
            exit 1
        fi
        KEY_FILE="$CLI_KEY"
        CERT_FILE="$CLI_CERT"
        return 0
    fi

    # 2. Environment variables
    local env_k="${MOK_KEY:-${MOK_SIGNING_KEY:-}}"
    local env_c="${MOK_CERT:-${MOK_CERTIFICATE:-}}"
    if [[ -n "$env_k" || -n "$env_c" ]]; then
        if [[ -z "$env_k" ]]; then
            echo "Error: Incomplete Secure Boot configuration: MOK_CERT is set but MOK_KEY is missing." >&2
            exit 1
        fi
        if [[ -z "$env_c" ]]; then
            echo "Error: Incomplete Secure Boot configuration: MOK_KEY is set but MOK_CERT is missing." >&2
            exit 1
        fi
        KEY_FILE="$env_k"
        CERT_FILE="$env_c"
        return 0
    fi

    # 3. Explicit config file via CLI or SIGN_CONF
    local explicit_conf=""
    if [[ -n "$CLI_CONFIG" ]]; then
        if [[ ! -f "$CLI_CONFIG" ]]; then
            echo "Error: Specified configuration file not found at '$CLI_CONFIG'." >&2
            exit 1
        fi
        explicit_conf="$CLI_CONFIG"
    elif [[ -n "${SIGN_CONF:-}" ]]; then
        if [[ ! -f "$SIGN_CONF" ]]; then
            echo "Error: Configuration file specified by SIGN_CONF not found at '$SIGN_CONF'." >&2
            exit 1
        fi
        explicit_conf="$SIGN_CONF"
    fi

    if [[ -n "$explicit_conf" ]]; then
        PARSED_KEY=""
        PARSED_CERT=""
        PARSED_HASH=""
        PARSED_SIGN_FILE=""
        parse_config_file "$explicit_conf"
        if [[ -n "$PARSED_KEY" || -n "$PARSED_CERT" ]]; then
            if [[ -z "$PARSED_KEY" ]]; then
                echo "Error: Incomplete Secure Boot configuration in '$explicit_conf': MOK_CERT is set but MOK_KEY is missing." >&2
                exit 1
            fi
            if [[ -z "$PARSED_CERT" ]]; then
                echo "Error: Incomplete Secure Boot configuration in '$explicit_conf': MOK_KEY is set but MOK_CERT is missing." >&2
                exit 1
            fi
            KEY_FILE="$PARSED_KEY"
            CERT_FILE="$PARSED_CERT"
        fi
        CONF_HASH="$PARSED_HASH"
        CONF_SIGN_FILE="$PARSED_SIGN_FILE"
        return 0
    fi

    # 4. Default config files in priority order
    local conf_candidates=(
        "/etc/mt7925-bt-recovery/sign.conf"
        "/etc/dkms/sign.conf"
    )
    local dropin
    for dropin in /etc/dkms/framework.conf.d/*.conf; do
        [[ -f "$dropin" ]] && conf_candidates+=("$dropin")
    done
    conf_candidates+=("/etc/dkms/framework.conf")
    for conf in "${conf_candidates[@]}"; do
        if [[ -f "$conf" ]]; then
            PARSED_KEY=""
            PARSED_CERT=""
            PARSED_HASH=""
            PARSED_SIGN_FILE=""
            parse_config_file "$conf"
            if [[ -n "$PARSED_KEY" || -n "$PARSED_CERT" ]]; then
                if [[ -z "$PARSED_KEY" ]]; then
                    echo "Error: Incomplete Secure Boot configuration in '$conf': MOK_CERT is set but MOK_KEY is missing." >&2
                    exit 1
                fi
                if [[ -z "$PARSED_CERT" ]]; then
                    echo "Error: Incomplete Secure Boot configuration in '$conf': MOK_KEY is set but MOK_CERT is missing." >&2
                    exit 1
                fi
                KEY_FILE="$PARSED_KEY"
                CERT_FILE="$PARSED_CERT"
                CONF_HASH="$PARSED_HASH"
                CONF_SIGN_FILE="$PARSED_SIGN_FILE"
                return 0
            fi
            [[ -z "$CONF_HASH" ]] && CONF_HASH="$PARSED_HASH"
            [[ -z "$CONF_SIGN_FILE" ]] && CONF_SIGN_FILE="$PARSED_SIGN_FILE"
        fi
    done
}

resolve_credentials

# Resolve active digest and sign-file
HASH_ALGO="${CLI_HASH:-${CONF_HASH:-${HASH_ALGO:-sha256}}}"
case "$HASH_ALGO" in
    sha256|sha384|sha512)
        ;;
    *)
        echo "Error: Invalid hash algorithm '$HASH_ALGO'. Allowed algorithms: sha256, sha384, sha512." >&2
        exit 1
        ;;
esac

SIGN_FILE_BIN="${CLI_SIGN_FILE:-${CONF_SIGN_FILE:-${SIGN_FILE:-}}}"

# Resolve kernel version: CLI > positional > $kernelver (from DKMS) > uname -r
TARGET_KERNEL="${CLI_KERNEL:-}"
MODULE_TARGET="${CLI_MODULE:-}"

for arg in "${POSITIONAL_ARGS[@]}"; do
    if [[ -z "$TARGET_KERNEL" && "$arg" =~ ^[0-9]+\.[0-9]+ ]]; then
        TARGET_KERNEL="$arg"
    elif [[ -z "$MODULE_TARGET" && "$arg" =~ \.ko(\.(gz|xz|zst))?$ ]]; then
        MODULE_TARGET="$arg"
    fi
done

if [[ -z "$TARGET_KERNEL" ]]; then
    TARGET_KERNEL="${kernelver:-$(uname -r)}"
fi

find_sign_file() {
    if [[ -n "$SIGN_FILE_BIN" ]]; then
        if [[ -x "$SIGN_FILE_BIN" ]]; then
            echo "$SIGN_FILE_BIN"
            return 0
        fi
        echo "Error: Configured sign-file binary is not executable: '$SIGN_FILE_BIN'." >&2
        return 1
    fi

    local mod_base="${MODULES_DIR:-/lib/modules}"
    local candidates=(
        "${mod_base}/${TARGET_KERNEL}/build/scripts/sign-file"
        "/usr/src/linux-headers-${TARGET_KERNEL}/scripts/sign-file"
        "${mod_base}/${TARGET_KERNEL}/source/scripts/sign-file"
    )

    for cand in "${candidates[@]}"; do
        if [[ -x "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done

    # Check headers across modules/*/build/scripts/sign-file
    for cand in "${mod_base}"/*/build/scripts/sign-file; do
        if [[ -x "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done

    # Check kbuild packages
    for cand in /usr/lib/linux-kbuild-*/scripts/sign-file; do
        if [[ -x "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done

    # Check PATH
    if command -v sign-file >/dev/null 2>&1; then
        command -v sign-file
        return 0
    fi

    return 1
}

find_module_file() {
    if [[ -n "$MODULE_TARGET" ]]; then
        if [[ -f "$MODULE_TARGET" ]]; then
            echo "$MODULE_TARGET"
            return 0
        fi
        echo "Error: Specified module not found at '$MODULE_TARGET'." >&2
        return 1
    fi

    local mod_base="${MODULES_DIR:-/lib/modules}"
    local candidates=(
        "${mod_base}/${TARGET_KERNEL}/updates/dkms/btmtk.ko"
        "${mod_base}/${TARGET_KERNEL}/updates/dkms/btmtk.ko.gz"
        "${mod_base}/${TARGET_KERNEL}/updates/dkms/btmtk.ko.xz"
        "${mod_base}/${TARGET_KERNEL}/updates/dkms/btmtk.ko.zst"
        "${mod_base}/${TARGET_KERNEL}/updates/btmtk.ko"
        "${mod_base}/${TARGET_KERNEL}/updates/btmtk.ko.gz"
        "${mod_base}/${TARGET_KERNEL}/updates/btmtk.ko.xz"
        "${mod_base}/${TARGET_KERNEL}/updates/btmtk.ko.zst"
        "${mod_base}/${TARGET_KERNEL}/extra/btmtk.ko"
        "${mod_base}/${TARGET_KERNEL}/extra/btmtk.ko.gz"
        "${mod_base}/${TARGET_KERNEL}/extra/btmtk.ko.xz"
        "${mod_base}/${TARGET_KERNEL}/extra/btmtk.ko.zst"
    )

    for cand in "${candidates[@]}"; do
        if [[ -f "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done

    # Fallback search restricted to DKMS locations with exact module names
    local dkms_dirs=(
        "${mod_base}/${TARGET_KERNEL}/updates/dkms"
        "${mod_base}/${TARGET_KERNEL}/updates"
        "${mod_base}/${TARGET_KERNEL}/extra"
    )
    for ddir in "${dkms_dirs[@]}"; do
        if [[ -d "$ddir" ]]; then
            local found
            found="$(find "$ddir" -type f \( -name "btmtk.ko" -o -name "btmtk.ko.gz" -o -name "btmtk.ko.xz" -o -name "btmtk.ko.zst" \) 2>/dev/null | head -n 1)"
            if [[ -n "$found" && -f "$found" ]]; then
                echo "$found"
                return 0
            fi
        fi
    done

    return 1
}

is_secure_boot_enabled() {
    if command -v mokutil >/dev/null 2>&1; then
        local sb_state
        sb_state="$(mokutil --sb-state 2>/dev/null || true)"
        if [[ "$sb_state" =~ [Ss]ecure[Bb]oot[[:space:]]+enabled ]]; then
            return 0
        elif [[ "$sb_state" =~ [Ss]ecure[Bb]oot[[:space:]]+disabled ]]; then
            return 1
        fi
    fi

    for efivar in /sys/firmware/efi/efivars/SecureBoot-*; do
        if [[ -f "$efivar" ]]; then
            local data
            data="$(od -An -t u1 -j 4 -N 1 "$efivar" 2>/dev/null || true)"
            if [[ "${data//[[:space:]]/}" == "1" ]]; then
                return 0
            elif [[ "${data//[[:space:]]/}" == "0" ]]; then
                return 1
            fi
        fi
    done

    return 2
}

sign_target_module() {
    local mod_file="$1"
    local sign_bin="$2"

    echo "Signing kernel module '${mod_file}' for kernel ${TARGET_KERNEL} using ${HASH_ALGO}..."

    if [[ "$mod_file" =~ \.(gz|xz|zst)$ ]]; then
        local ext="${BASH_REMATCH[1]}"
        local tmpdir
        tmpdir="$(mktemp -d -t dkms-sign.XXXXXXXXXX)"
        local base_mod
        base_mod="$(basename "$mod_file" ".$ext")"
        local uncompressed="${tmpdir}/${base_mod}"

        if [[ "$ext" == "gz" ]]; then
            if ! command -v gzip >/dev/null 2>&1; then
                rm -rf "$tmpdir"
                echo "Error: 'gzip' command not found to decompress/recompress module '${mod_file}'." >&2
                exit 1
            fi
            gzip -dc "$mod_file" > "$uncompressed"
        elif [[ "$ext" == "xz" ]]; then
            if ! command -v xz >/dev/null 2>&1; then
                rm -rf "$tmpdir"
                echo "Error: 'xz' command not found to decompress/recompress module '${mod_file}'." >&2
                exit 1
            fi
            xz -d -c "$mod_file" > "$uncompressed"
        elif [[ "$ext" == "zst" ]]; then
            if ! command -v zstd >/dev/null 2>&1; then
                rm -rf "$tmpdir"
                echo "Error: 'zstd' command not found to decompress/recompress module '${mod_file}'." >&2
                exit 1
            fi
            zstd -d -q -c "$mod_file" > "$uncompressed"
        fi

        local status=0
        "$sign_bin" "$HASH_ALGO" "$KEY_FILE" "$CERT_FILE" "$uncompressed" || status=$?
        if [[ "$status" -ne 0 ]]; then
            rm -rf "$tmpdir"
            echo "Error: Failed to sign ${mod_file} with sign-file (exit code ${status})." >&2
            exit "$status"
        fi

        local recompressed="${tmpdir}/recompressed.${ext}"
        if [[ "$ext" == "gz" ]]; then
            gzip -n -c "$uncompressed" > "$recompressed"
        elif [[ "$ext" == "xz" ]]; then
            xz --check=crc32 -c "$uncompressed" > "$recompressed"
        elif [[ "$ext" == "zst" ]]; then
            zstd -f -q "$uncompressed" -o "$recompressed"
        fi

        chmod --reference="$mod_file" "$recompressed" 2>/dev/null || true
        chown --reference="$mod_file" "$recompressed" 2>/dev/null || true
        mv -f "$recompressed" "$mod_file"
        rm -rf "$tmpdir"
    else
        local status=0
        "$sign_bin" "$HASH_ALGO" "$KEY_FILE" "$CERT_FILE" "$mod_file" || status=$?
        if [[ "$status" -ne 0 ]]; then
            echo "Error: Failed to sign ${mod_file} with sign-file (exit code ${status})." >&2
            exit "$status"
        fi
    fi

    echo "Successfully signed ${mod_file} for Secure Boot."
}

do_sign() {
    # Check if signing is configured
    if [[ -z "$KEY_FILE" && -z "$CERT_FILE" ]]; then
        local sb_status=2
        if is_secure_boot_enabled; then
            sb_status=0
        elif [[ $? -eq 1 ]]; then
            sb_status=1
        fi

        if [[ "$sb_status" -eq 0 ]]; then
            echo "Error: Secure Boot is enabled, but module signing is not configured (MOK_KEY / MOK_CERT not set)." >&2
            exit 1
        else
            echo "Warning: Secure Boot signing is not configured (MOK_KEY / MOK_CERT not set)." >&2
            echo "Skipping module signing for kernel ${TARGET_KERNEL}." >&2
            exit 0
        fi
    fi

    # Incomplete configuration check
    if [[ -z "$KEY_FILE" ]]; then
        echo "Error: Incomplete Secure Boot configuration: MOK_CERT is set but MOK_KEY is missing." >&2
        exit 1
    fi

    if [[ -z "$CERT_FILE" ]]; then
        echo "Error: Incomplete Secure Boot configuration: MOK_KEY is set but MOK_CERT is missing." >&2
        exit 1
    fi

    # Validate file presence (do not print private key path)
    if [[ ! -f "$KEY_FILE" ]]; then
        echo "Error: MOK private key file not found." >&2
        exit 1
    fi

    # Validate private key permissions and ownership (do not print private key path)
    local key_stat key_mode key_uid
    key_stat="$(stat -c '%a %u' "$KEY_FILE" 2>/dev/null || true)"
    read -r key_mode key_uid <<< "$key_stat" || true

    if [[ "$key_mode" != "600" && "$key_mode" != "0600" && "$key_mode" != "400" && "$key_mode" != "0400" ]]; then
        echo "Error: MOK private key has insecure permissions: mode must be 0600 or 0400." >&2
        exit 1
    fi

    local current_uid
    current_uid="$(id -u)"
    if [[ "$key_uid" != "0" && "$key_uid" != "$current_uid" && ( -z "${SUDO_UID:-}" || "$key_uid" != "$SUDO_UID" ) ]]; then
        echo "Error: MOK private key has invalid owner: must be owned by root or the caller." >&2
        exit 1
    fi

    if [[ ! -f "$CERT_FILE" ]]; then
        echo "Error: MOK certificate file not found at: '$CERT_FILE'." >&2
        exit 1
    fi

    # Locate module file
    local mod_file
    if ! mod_file="$(find_module_file)"; then
        echo "Error: btmtk.ko module not found for kernel ${TARGET_KERNEL}." >&2
        echo "Build and install the driver first or specify path with --module." >&2
        exit 1
    fi

    # Locate sign-file binary
    local sign_bin
    if ! sign_bin="$(find_sign_file)"; then
        if [[ -z "$SIGN_FILE_BIN" ]]; then
            echo "Error: Kernel 'sign-file' tool not found for kernel ${TARGET_KERNEL}." >&2
            echo "Please install linux-headers-${TARGET_KERNEL} or specify tool with --sign-file." >&2
        fi
        exit 1
    fi

    sign_target_module "$mod_file" "$sign_bin"
}

do_enroll() {
    if [[ -z "$CERT_FILE" ]]; then
        echo "Error: MOK certificate is not configured. Set MOK_CERT, specify --cert, or configure /etc/mt7925-bt-recovery/sign.conf." >&2
        exit 1
    fi

    if [[ ! -f "$CERT_FILE" ]]; then
        echo "Error: MOK certificate file not found at: '$CERT_FILE'." >&2
        exit 1
    fi

    if ! command -v mokutil >/dev/null 2>&1; then
        echo "Error: 'mokutil' command not found." >&2
        echo "Please install mokutil using: sudo apt install mokutil" >&2
        exit 1
    fi

    local enroll_cert="$CERT_FILE"

    # If certificate is in PEM format, convert to DER for mokutil
    if grep -q -- "-----BEGIN CERTIFICATE-----" "$CERT_FILE" 2>/dev/null; then
        if ! command -v openssl >/dev/null 2>&1; then
            echo "Error: openssl command not found. Cannot convert PEM certificate to DER for mokutil." >&2
            exit 1
        fi
        ENROLL_CLEANUP_DIR="$(mktemp -d -t mok-enroll.XXXXXXXXXX)"
        enroll_cert="${ENROLL_CLEANUP_DIR}/MOK.der"
        openssl x509 -in "$CERT_FILE" -outform DER -out "$enroll_cert"
    fi

    # Cleanup temp dir on exit
    cleanup() {
        if [[ -n "${ENROLL_CLEANUP_DIR:-}" && -d "$ENROLL_CLEANUP_DIR" ]]; then
            rm -rf "$ENROLL_CLEANUP_DIR"
        fi
    }
    trap cleanup EXIT

    echo "Enrolling MOK certificate '$CERT_FILE' into UEFI MOK database..."
    local status=0
    mokutil --import "$enroll_cert" || status=$?
    if [[ "$status" -eq 0 ]]; then
        cat <<'EOF'

MOK certificate staged for enrollment.
Next steps to complete Secure Boot activation:
  1. Reboot your machine: sudo reboot
  2. The UEFI 'Perform MOK management' blue screen will appear upon reboot.
  3. Select 'Enroll MOK' -> 'Continue' -> 'Yes'.
  4. Enter the one-time enrollment password you just provided to mokutil.
  5. Select 'Reboot' to boot with your signed module trusted by UEFI.
EOF
    else
        echo "Error: mokutil --import failed with exit code ${status}." >&2
        exit "$status"
    fi
}

case "$COMMAND" in
    sign)
        do_sign
        ;;
    enroll)
        do_enroll
        ;;
    *)
        echo "Error: Unknown command '$COMMAND'. Expected 'sign' or 'enroll'." >&2
        usage >&2
        exit 1
        ;;
esac
