# MT7925 Bluetooth recovery

Some MediaTek MT7925 Bluetooth controllers time out while downloading firmware
(`-110`), leaving no controller available after boot. We fixed it with this
custom DKMS driver, which resets the controller and retries the failed setup
once.

It is an out-of-tree backport for the Linux `btmtk` driver. Install it with
DKMS on a system with matching kernel headers; Secure Boot systems must sign
and enroll the resulting module.
