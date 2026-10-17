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
sudo install -d /usr/src/mt7925-bt-recovery-0.1
sudo install -m 0644 Makefile dkms.conf btmtk.c btmtk.h /usr/src/mt7925-bt-recovery-0.1/
sudo dkms add -m mt7925-bt-recovery -v 0.1
sudo dkms install -m mt7925-bt-recovery -v 0.1
sudo reboot
```

With Secure Boot enabled, sign and enroll the DKMS module before rebooting.

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
