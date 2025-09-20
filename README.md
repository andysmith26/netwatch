# netwatch (Raspberry Pi network watchdog)

A tiny, no-frills network watchdog for Raspberry Pi (or any Debian-ish Linux).
It logs link/gateway/WAN/DNS health every 15 seconds to a monthly CSV and includes a report tool to summarize outages.

## What it checks
- **Link**: whether the Ethernet interface is physically up.
- **Gateway**: ping your default gateway (local LAN reachability).
- **WAN**: ping a public IP (default: `1.1.1.1`) to confirm internet reachability without DNS.
- **DNS**: resolve a hostname (default: `www.google.com`) to verify resolver health.

## Install (Pi OS / Debian)
```bash
# 1) Install the logger script
sudo install -m 0755 netwatch.sh /usr/local/bin/netwatch.sh

# 2) Install the systemd unit
sudo install -m 0644 systemd/netwatch.service /etc/systemd/system/netwatch.service
sudo systemctl daemon-reload
sudo systemctl enable --now netwatch.service

# 3) Verify logs
tail -f /var/log/netwatch/netwatch_$(date +%Y-%m).csv
```

## Uninstall
```bash
sudo systemctl disable --now netwatch.service
sudo rm -f /etc/systemd/system/netwatch.service
sudo systemctl daemon-reload
sudo rm -f /usr/local/bin/netwatch.sh
sudo rm -rf /var/log/netwatch
```

## Configuration
Default interval is 15 seconds on interface `eth0`. Override via environment variables in the unit:

```ini
# /etc/systemd/system/netwatch.service (uncomment/modify)
[Service]
Environment=INTERFACE=eth0
Environment=SLEEP_SECS=15
```

Change WAN/DNS targets by editing `netwatch.sh`:
```bash
wan_ip="1.1.1.1"
dns_host="www.google.com"
```

## Report tool
Summarize outages for the **current** and **previous** month:

```bash
# Run locally (no install needed), or copy to your Pi and execute
python3 tools/netwatch_report.py
```

Output example:
```
=== 2025-09 — /var/log/netwatch/netwatch_2025-09.csv ===
01. WAN_DOWN   from 2025-09-20 21:50:47+01:00 to 2025-09-20 21:52:32+01:00  (dur 1m 45s)
— Total outages: 1
— Cumulative downtime: 1m 45s
— Longest single outage: 1m 45s
```

By default the report looks for `/var/log/netwatch/netwatch_YYYY-MM.csv`. If you keep logs elsewhere, pass `NETWATCH_DIR`:

```bash
NETWATCH_DIR=/some/path python3 tools/netwatch_report.py
```

## Troubleshooting
- **Service fails with `status=203/EXEC`**: the script path is wrong, not executable, or bad shebang/line endings. Fix with:
  ```bash
  sudo chmod +x /usr/local/bin/netwatch.sh
  sudo sed -i 's/\r$//' /usr/local/bin/netwatch.sh
  sudo systemctl restart netwatch.service && systemctl status netwatch.service
  ```
- **No log created**: check permissions for `/var/log/netwatch`, or view service logs:
  ```bash
  journalctl -u netwatch.service -b --no-pager | tail -100
  ```
- **Interface isn’t `eth0`**: set `Environment=INTERFACE=...` in the unit.

## Why systemd (not cron)?
- Automatic restart if it crashes.
- Clean boot ordering (`network-online.target`).
- Easy to check status & logs (`systemctl`, `journalctl`).

## License
MIT — see [LICENSE](LICENSE).
