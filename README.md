# MT7925 Bluetooth recovery

Some MediaTek MT7925 Bluetooth controllers time out while downloading firmware
(`-110`), leaving no controller available after boot. We fixed it with this
custom DKMS driver, which resets the controller and retries the failed setup.
MT7922 uses the same firmware-download retry. How many times to retry is the
`fw_dl_retry_count` module parameter (default 3, range 0-10).

It is an out-of-tree backport for the Linux `btmtk` driver.

## Requirements

Linux 6.4 or newer (tested on 6.8, 6.17, 7.0).

## Install (Ubuntu/Debian)

```bash
sudo apt install dkms git linux-headers-$(uname -r)
git clone https://github.com/Josh-Archer/mt7925-bt-recovery.git
cd mt7925-bt-recovery
sudo install -d /usr/src/mt7925-bt-recovery-0.2.0
sudo install -m 0644 Makefile dkms.conf btmtk.c btmtk.h /usr/src/mt7925-bt-recovery-0.2.0/
sudo install -D -m 0755 scripts/sign-module.sh /usr/src/mt7925-bt-recovery-0.2.0/scripts/sign-module.sh
sudo dkms add -m mt7925-bt-recovery -v 0.2.0
sudo dkms install -m mt7925-bt-recovery -v 0.2.0
```

### Secure Boot

When Secure Boot is enabled, the module must be signed with a key trusted by
the machine and that key's certificate must be enrolled through MOK. The script
uses an existing private key and certificate; it does not create keys. Keep
both files outside this repository. Private keys must have 0600 or 0400
permissions and be owned by root or the caller.

Set `MOK_KEY` and `MOK_CERT` to the paths for your MOK private key and
certificate. For example, with the standard Ubuntu shim-signed key locations:

```bash
export MOK_KEY=/var/lib/shim-signed/mok/MOK.priv
export MOK_CERT=/var/lib/shim-signed/mok/MOK.der
sudo --preserve-env=MOK_KEY,MOK_CERT /usr/src/mt7925-bt-recovery-0.2.0/scripts/sign-module.sh enroll
sudo --preserve-env=MOK_KEY,MOK_CERT /usr/src/mt7925-bt-recovery-0.2.0/scripts/sign-module.sh sign
sudo reboot
```

Complete the enrollment in the blue MOK Manager screen after reboot. If your
key uses different paths, set those paths instead. You can also configure the
paths in `/etc/mt7925-bt-recovery/sign.conf`:

```ini
MOK_KEY=/path/to/your/MOK.priv
MOK_CERT=/path/to/your/MOK.der
# HASH_ALGO=sha256
```

For automatic signing with a custom key across kernel upgrades, DKMS 3.0.4 or
newer supports native framework settings. Install a drop-in rather than editing
the package-managed `/etc/dkms/framework.conf`:

```bash
sudo install -d -m 0755 /etc/dkms/framework.conf.d
sudo tee /etc/dkms/framework.conf.d/mt7925-bt-recovery.conf >/dev/null <<'EOF'
mok_signing_key="/path/to/your/MOK.priv"
mok_certificate="/path/to/your/MOK.der"
EOF
```

DKMS's native signing also uses its distribution defaults when those settings
are omitted. On Ubuntu this can be the default MOK at
`/var/lib/shim-signed/mok/MOK.priv` and `MOK.der`. No project signing hook is
needed; DKMS signs the module in its build flow before saving the copy it later
installs. The `POST_BUILD` hook is intentionally not used because it runs
after that saved copy is made.

The native `mok_signing_key` and `mok_certificate` settings require DKMS 3.0.4
or newer; DKMS 3.0.0–3.0.3 use the older `sign_tool` interface. Ubuntu's DKMS
2.8.x packages already auto-sign modules with the Ubuntu MOK, so Ubuntu users
normally do not need to configure a signing hook. On systems where DKMS does
not provide automatic signing, sign the installed module manually with this
script after each DKMS build/install. The framework options and build hook
order are documented in the [DKMS manual](https://github.com/dkms-project/dkms/blob/main/dkms.8.in).

The script accepts `--key` and `--cert` options as well. It targets the
installed module under `/lib/modules/<kernel>/updates/dkms` (handling `.ko`,
`.ko.gz`, `.ko.xz`, and `.ko.zst`); pass `--kernel VERSION` and `--module PATH` to select
a different build. Signing uses the kernel's `sign-file` utility (`kmodsign`
is not supported). If neither signing path is configured, signing is skipped with
a warning when Secure Boot is disabled or unknown; if Secure Boot is enabled,
signing must be configured or the script exits with an error. If only one path
is configured or a configured file is missing, the script exits with a
descriptive error. `scripts/sign-module.sh --help` lists all options.

## Module parameters

`fw_dl_retry_count` (unsigned integer, default 3, range 0-10)

Maximum number of controller resets after a firmware-download failure on
MT7922 (`0x7922`) and MT7925 (`0x7925`). `0` disables that reset retry.
Values outside 0-10 are rejected at load time and when written at runtime.
This does not change the separate WMT func-ctrl timeout retry limit.

`modinfo btmtk` prints the parameter, its default, and its range. Examples:

```bash
# load time
sudo modprobe btmtk fw_dl_retry_count=5

# runtime, after the module is loaded
echo 5 | sudo tee /sys/module/btmtk/parameters/fw_dl_retry_count

# persist across boots
echo 'options btmtk fw_dl_retry_count=5' | sudo tee /etc/modprobe.d/btmtk.conf
```
