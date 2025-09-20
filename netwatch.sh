#!/usr/bin/env bash
set -Eeuo pipefail

INTERFACE="${INTERFACE:-eth0}"
SLEEP_SECS="${SLEEP_SECS:-15}"
LOG_DIR="/var/log/netwatch"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/netwatch_$(date +%Y-%m).csv"
if [[ ! -f "$LOG_FILE" ]]; then
  echo "timestamp,iface,link_state,gw_ip,gw_ok,wan_ip,wan_ok,dns_host,dns_ok,rtt_gw_ms,rtt_wan_ms,status" >> "$LOG_FILE"
fi

ping_once() {
  local target="$1"
  local out rc
  out=$(ping -n -c1 -W2 "$target" 2>/dev/null) && rc=0 || rc=$?
  if [[ $rc -eq 0 ]]; then
    local rtt
    rtt=$(grep -oE 'time=([0-9.]+)' <<<"$out" | head -1 | cut -d= -f2)
    echo "ok,${rtt}"
  else
    echo "fail,"
  fi
}

dns_check() {
  local host="$1"
  if getent hosts "$host" >/dev/null 2>&1; then
    echo "ok"
  else
    echo "fail"
  fi
}

while true; do
  ts="$(date --iso-8601=seconds)"

  link_state="unknown"
  if [[ -f "/sys/class/net/${INTERFACE}/operstate" ]]; then
    link_state=$(cat "/sys/class/net/${INTERFACE}/operstate")
  fi

  gw_ip=""
  gw_ok="fail"; rtt_gw=""; wan_ok="fail"; rtt_wan=""; dns_ok="fail"
  wan_ip="1.1.1.1"
  dns_host="www.google.com"

  gw_ip=$(ip route | awk '/^default/ {print $3; exit}')

  status="UNKNOWN"

  if [[ "$link_state" != "up" ]]; then
    status="LINK_DOWN"
  else
    if [[ -n "$gw_ip" ]]; then
      IFS=',' read -r gw_res rtt_gw <<<"$(ping_once "$gw_ip")"
      if [[ "$gw_res" == "ok" ]]; then
        gw_ok="ok"
        IFS=',' read -r wan_res rtt_wan <<<"$(ping_once "$wan_ip")"
        if [[ "$wan_res" == "ok" ]]; then
          wan_ok="ok"
          dns_ok="$(dns_check "$dns_host")"
          if [[ "$dns_ok" == "ok" ]]; then
            status="OK"
          else
            status="DNS_DOWN"
          fi
        else
          status="WAN_DOWN"
        fi
      else
        status="GW_DOWN"
      fi
    else
      status="NO_GATEWAY"
    fi
  fi

  echo "$ts,$INTERFACE,$link_state,$gw_ip,$gw_ok,$wan_ip,$wan_ok,$dns_host,$dns_ok,$rtt_gw,$rtt_wan,$status" >> "$LOG_FILE"
  sleep "$SLEEP_SECS"
done
