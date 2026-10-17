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
  --hash ALGO     Hash algorithm for module signing (default: sha256).
  --module PATH   Path to btmtk.ko kernel module to sign.
  --kernel VER    Target kernel version (default: running kernel or $kernelver).
  --sign-file BIN Path to kernel sign-file binary (overrides auto-detection).
  --config PATH   Path to signing configuration file.
  -h, --help      Display this help message.

Configuration:
  Configuration can be supplied via environment variables or a configuration file.
  Default configuration file locations checked:
    1. Value of SIGN_CONF environment variable (if set)
    2. /etc/mt7925-bt-recovery/sign.conf
    3. /etc/dkms/sign.conf
    4. /etc/dkms/framework.conf (reads mok_signing_key and mok_certificate)

  Variables:
    MOK_KEY       Path to private signing key.
    MOK_CERT      Path to X.509 public certificate (PEM or DER format).
    HASH_ALGO     Signature digest algorithm (default: sha256).
    SIGN_FILE     Path to kernel sign-file binary.

Behavior:
  - If Secure Boot signing is not configured (neither key nor cert specified),
    the script emits a clear warning and exits successfully (exit code 0),
    allowing standard DKMS installations without Secure Boot to proceed cleanly.
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

# Load configuration file if present
load_config() {
    local conf=""
    if [[ -n "$CLI_CONFIG" ]]; then
        if [[ ! -f "$CLI_CONFIG" ]]; then
            echo "Error: Specified configuration file not found at '$CLI_CONFIG'." >&2
            exit 1
        fi
        conf="$CLI_CONFIG"
    elif [[ -n "${SIGN_CONF:-}" ]]; then
        if [[ ! -f "$SIGN_CONF" ]]; then
            echo "Error: Configuration file specified by SIGN_CONF not found at '$SIGN_CONF'." >&2
            exit 1
        fi
        conf="$SIGN_CONF"
    elif [[ -f "/etc/mt7925-bt-recovery/sign.conf" ]]; then
        conf="/etc/mt7925-bt-recovery/sign.conf"
    elif [[ -f "/etc/dkms/sign.conf" ]]; then
        conf="/etc/dkms/sign.conf"
    fi

    if [[ -n "$conf" && -r "$conf" ]]; then
        # Source safe key=value pairs without arbitrary execution
        while IFS='=' read -r key val || [[ -n "$key" ]]; do
            # Trim leading/trailing whitespace
            key="$(echo "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
            # Strip comments
            [[ "$key" =~ ^# ]] && continue
            [[ -z "$key" ]] && continue
            val="$(echo "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
            case "$key" in
                MOK_KEY|mok_signing_key)
                    MOK_KEY="${MOK_KEY:-$val}"
                    ;;
                MOK_CERT|mok_certificate)
                    MOK_CERT="${MOK_CERT:-$val}"
                    ;;
                HASH_ALGO)
                    HASH_ALGO="${HASH_ALGO:-$val}"
                    ;;
                SIGN_FILE|sign_file)
                    SIGN_FILE="${SIGN_FILE:-$val}"
                    ;;
            esac
        done < "$conf"
    fi

    # Fallback to framework.conf if variables still empty
    if [[ -z "${MOK_KEY:-}" || -z "${MOK_CERT:-}" ]] && [[ -f "/etc/dkms/framework.conf" ]]; then
        while IFS='=' read -r key val || [[ -n "$key" ]]; do
            key="$(echo "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
            [[ "$key" =~ ^# ]] && continue
            [[ -z "$key" ]] && continue
            val="$(echo "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
            case "$key" in
                mok_signing_key)
                    MOK_KEY="${MOK_KEY:-$val}"
                    ;;
                mok_certificate)
                    MOK_CERT="${MOK_CERT:-$val}"
                    ;;
                sign_file)
                    SIGN_FILE="${SIGN_FILE:-$val}"
                    ;;
            esac
        done < "/etc/dkms/framework.conf"
    fi
}

load_config

# Resolve active variables with precedence: CLI > ENV > Config file
KEY_FILE="${CLI_KEY:-${MOK_KEY:-${MOK_SIGNING_KEY:-${KBUILD_SIGN_KEY:-}}}}"
CERT_FILE="${CLI_CERT:-${MOK_CERT:-${MOK_CERTIFICATE:-${KBUILD_SIGN_PIN:-}}}}"
HASH_ALGO="${CLI_HASH:-${HASH_ALGO:-sha256}}"
SIGN_FILE_BIN="${CLI_SIGN_FILE:-${SIGN_FILE:-}}"

# Resolve kernel version: CLI > positional > $kernelver (from DKMS) > uname -r
TARGET_KERNEL="${CLI_KERNEL:-}"
MODULE_TARGET="${CLI_MODULE:-}"

for arg in "${POSITIONAL_ARGS[@]}"; do
    if [[ -z "$TARGET_KERNEL" && "$arg" =~ ^[0-9]+\.[0-9]+ ]]; then
        TARGET_KERNEL="$arg"
    elif [[ -z "$MODULE_TARGET" && "$arg" =~ \.ko$ ]]; then
        MODULE_TARGET="$arg"
    fi
done

if [[ -z "$TARGET_KERNEL" ]]; then
    TARGET_KERNEL="${kernelver:-$(uname -r)}"
fi

find_sign_file() {
    if [[ -n "$SIGN_FILE_BIN" && -x "$SIGN_FILE_BIN" ]]; then
        echo "$SIGN_FILE_BIN"
        return 0
    fi

    local candidates=(
        "/lib/modules/${TARGET_KERNEL}/build/scripts/sign-file"
        "/usr/src/linux-headers-${TARGET_KERNEL}/scripts/sign-file"
        "/lib/modules/${TARGET_KERNEL}/source/scripts/sign-file"
    )

    for cand in "${candidates[@]}"; do
        if [[ -x "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done

    # Check headers across /lib/modules/*/build/scripts/sign-file
    for cand in /lib/modules/*/build/scripts/sign-file; do
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

    local candidates=(
        "./btmtk.ko"
        "${dkms_tree:-/var/lib/dkms}/${PACKAGE_NAME:-mt7925-bt-recovery}/${PACKAGE_VERSION:-0.1}/build/btmtk.ko"
        "/var/lib/dkms/${PACKAGE_NAME:-mt7925-bt-recovery}/${PACKAGE_VERSION:-0.1}/${TARGET_KERNEL}/$(uname -m)/module/btmtk.ko"
        "/lib/modules/${TARGET_KERNEL}/updates/dkms/btmtk.ko"
        "/lib/modules/${TARGET_KERNEL}/updates/btmtk.ko"
        "/lib/modules/${TARGET_KERNEL}/extra/btmtk.ko"
    )

    for cand in "${candidates[@]}"; do
        if [[ -f "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done

    # Fallback search in /lib/modules/${TARGET_KERNEL}
    if [[ -d "/lib/modules/${TARGET_KERNEL}" ]]; then
        local found
        found="$(find "/lib/modules/${TARGET_KERNEL}" -name "btmtk.ko" 2>/dev/null | head -n 1)"
        if [[ -n "$found" && -f "$found" ]]; then
            echo "$found"
            return 0
        fi
    fi

    return 1
}

do_sign() {
    # Check if signing is configured
    if [[ -z "$KEY_FILE" && -z "$CERT_FILE" ]]; then
        echo "Warning: Secure Boot signing is not configured (MOK_KEY / MOK_CERT not set)." >&2
        echo "Skipping module signing for kernel ${TARGET_KERNEL}." >&2
        exit 0
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

    # Validate file presence
    if [[ ! -f "$KEY_FILE" ]]; then
        echo "Error: MOK private key file not found at: '$KEY_FILE'." >&2
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
        echo "Build the driver first or specify path with --module." >&2
        exit 1
    fi

    # Locate sign-file binary
    local sign_bin
    if ! sign_bin="$(find_sign_file)"; then
        echo "Error: Kernel 'sign-file' tool not found for kernel ${TARGET_KERNEL}." >&2
        echo "Please install linux-headers-${TARGET_KERNEL} or specify tool with --sign-file." >&2
        exit 1
    fi

    # Perform signature
    echo "Signing kernel module '${mod_file}' for kernel ${TARGET_KERNEL} using ${HASH_ALGO}..."
    if "$sign_bin" "$HASH_ALGO" "$KEY_FILE" "$CERT_FILE" "$mod_file"; then
        echo "Successfully signed ${mod_file} for Secure Boot."
    else
        local status=$?
        echo "Error: Failed to sign ${mod_file} with sign-file (exit code ${status})." >&2
        exit "$status"
    fi
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
    if mokutil --import "$enroll_cert"; then
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
        local status=$?
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
