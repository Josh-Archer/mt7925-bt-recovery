# MT7925 Bluetooth recovery

Some MediaTek MT7925 Bluetooth controllers time out while downloading firmware
(`-110`), leaving no controller available after boot. We fixed it with this
custom DKMS driver, which resets the controller and retries the failed setup
once.

It is an out-of-tree backport for the Linux `btmtk` driver.

## Requirements

Linux 6.4 or newer (tested on 6.8, 6.17, 7.0).

## Install (Ubuntu/Debian)

```bash
sudo apt install dkms git linux-headers-$(uname -r)
git clone https://github.com/Josh-Archer/mt7925-bt-recovery.git
cd mt7925-bt-recovery
sudo install -d /usr/src/mt7925-bt-recovery-0.1
sudo install -m 0644 Makefile dkms.conf btmtk.c btmtk.h /usr/src/mt7925-bt-recovery-0.1/
sudo install -D -m 0755 scripts/sign-module.sh /usr/src/mt7925-bt-recovery-0.1/scripts/sign-module.sh
sudo dkms add -m mt7925-bt-recovery -v 0.1
sudo dkms install -m mt7925-bt-recovery -v 0.1
```

### Secure Boot

When Secure Boot is enabled, the module must be signed with a key trusted by
the machine and that key's certificate must be enrolled through MOK. The script
uses an existing private key and certificate; it does not create keys. Keep
both files outside this repository and restrict access to the private key.

Set `MOK_KEY` and `MOK_CERT` to the paths for your MOK private key and
certificate. For example, with the standard Ubuntu shim-signed key locations:

```bash
export MOK_KEY=/var/lib/shim-signed/mok/MOK.priv
export MOK_CERT=/var/lib/shim-signed/mok/MOK.der
sudo --preserve-env=MOK_KEY,MOK_CERT /usr/src/mt7925-bt-recovery-0.1/scripts/sign-module.sh enroll
sudo --preserve-env=MOK_KEY,MOK_CERT /usr/src/mt7925-bt-recovery-0.1/scripts/sign-module.sh sign
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

The script accepts `--key` and `--cert` options as well. It finds `btmtk.ko`
for the running kernel by default; pass `--kernel VERSION` and `--module PATH`
to select a different build. If neither signing path is configured, signing is
skipped with a warning so systems without Secure Boot can install normally. If
only one path is configured or a configured file is missing, the script exits
with a descriptive error. `scripts/sign-module.sh --help` lists all options.
