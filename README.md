# MT7925 Bluetooth recovery

Some MediaTek MT7925 Bluetooth controllers time out while downloading firmware
(`-110`), leaving no controller available after boot. We fixed it with this
custom DKMS driver, which resets the controller and retries the failed setup
once.

It is an out-of-tree backport for the Linux `btmtk` driver.

## Install (Ubuntu/Debian)

```bash
sudo apt install dkms git linux-headers-$(uname -r)
git clone https://github.com/Josh-Archer/mt7925-bt-recovery.git
cd mt7925-bt-recovery
sudo install -d /usr/src/mt7925-bt-recovery-0.1
sudo install -m 0644 Makefile dkms.conf btmtk.c btmtk.h /usr/src/mt7925-bt-recovery-0.1/
sudo dkms add -m mt7925-bt-recovery -v 0.1
sudo dkms install -m mt7925-bt-recovery -v 0.1
sudo reboot
```

With Secure Boot enabled, sign and enroll the DKMS module before rebooting.
