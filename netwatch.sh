#!/usr/bin/env bash
set -Eeuo pipefail

# -------- config (override via systemd Environment=...) ----------
INTERFACE="${INTERFACE:-eth0}"
SLEEP_SECS="${SLEEP_SECS:-15}"

# Probes per check (higher = steadier signal, more traffic)
GW_COUNT="${GW_COUNT:-3}"
WAN_COUNT="${WAN_COUNT:-5}"

# Degradation thresholds
LOSS_WARN="${LOSS_WARN:-3}"      # %
RTT_WARN_MS="${RTT_WARN_MS:-200}" # ms

WAN_IP_A="${WAN_IP_A:-1.1.1.1}"
WAN_IP_B="${WAN_IP_B:-8.8.8.8}"
DNS_HOST="${DNS_HOST:-www.google.com}"
# ---------------------------------------------------------------

LOG_DIR="/var/log/netwatch"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/netwatch_$(date +%Y-%m).csv"

if [[ ! -f "$LOG_FILE" ]]; then
  echo "timestamp,iface,link_state,gw_ip,gw_ok,gw_loss_pct,gw_rtt_avg_ms,wan_best_ip,wan_ok,wan_loss_pct,wan_rtt_avg_ms,wan_alt_ip,wan_alt_ok,wan_alt_loss_pct,wan_alt_rtt_avg_ms,dns_host,dns_ok,status" >> "$LOG_FILE"
fi

# Parse ping output for loss% and avg RTT (ms). Echo: "<ok|fail>,<loss_pct>,<avg_ms>"
ping_stats() {
  local target="$1" count="$2"
  local out rc
  out=$(ping -n -c "$count" -W2 "$target" 2>/dev/null) && rc=0 || rc=$?
  # Loss%
  local loss
  loss=$(printf "%s\n" "$out" | grep -Eo '[0-9]+(\.[0-9]+)?% packet loss' | head -1 | sed 's/% packet loss//') || true
  [[ -z "${loss:-}" ]] && loss="100"
  # Avg RTT
  # Works for "rtt min/avg/max/mdev" and "round-trip min/avg/max" formats
  local avg
  avg=$(printf "%s\n" "$out" | awk -F'/' '/min\/avg\/|round-trip/ {print $5; exit}') || true
  [[ -z "${avg:-}" ]] && avg=""
  # ok if any reply arrived (loss < 100)
  if awk "BEGIN{exit !($loss < 100)}"; then
    echo "ok,${loss},${avg}"
  else
    echo "fail,${loss},${avg}"
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

  # Link state
  link_state="unknown"
  if [[ -f "/sys/class/net/${INTERFACE}/operstate" ]]; then
    link_state=$(cat "/sys/class/net/${INTERFACE}/operstate")
  fi

  gw_ip=""; gw_ok="fail"; gw_loss=""; gw_avg=""
  wan_best_ip=""; wan_ok="fail"; wan_loss=""; wan_avg=""
  dns_ok="fail"; status="UNKNOWN"

  # Discover default gateway
  gw_ip=$(ip route | awk '/^default/ {print $3; exit}')

  if [[ "$link_state" != "up" ]]; then
    status="LINK_DOWN"
  else
    if [[ -z "$gw_ip" ]]; then
      status="NO_GATEWAY"
    else
      IFS=',' read -r gw_res gw_loss gw_avg <<<"$(ping_stats "$gw_ip" "$GW_COUNT")"
      if [[ "$gw_res" != "ok" ]]; then
        gw_ok="fail"
        status="GW_DOWN"
      else
        gw_ok="ok"
        # Probe WAN on two targets, take the "best" (lowest loss, then lowest RTT)
        IFS=',' read -r wa_res wa_loss wa_avg <<<"$(ping_stats "$WAN_IP_A" "$WAN_COUNT")"
        IFS=',' read -r wb_res wb_loss wb_avg <<<"$(ping_stats "$WAN_IP_B" "$WAN_COUNT")"

        # Select best
        choose_a="false"
        if [[ "$wa_res" == "ok" && "$wb_res" != "ok" ]]; then
          choose_a="true"
        elif [[ "$wa_res" == "ok" && "$wb_res" == "ok" ]]; then
          # both ok: pick lower loss; tie-breaker: lower avg
          if awk "BEGIN{exit !($wa_loss < $wb_loss)}"; then
            choose_a="true"
          elif awk "BEGIN{exit !($wa_loss == $wb_loss)}"; then
            # equal loss: compare avg if both present
            if [[ -n "$wa_avg" && -n "$wb_avg" ]]; then
              if awk "BEGIN{exit !($wa_avg < $wb_avg)}"; then choose_a="true"; fi
            elif [[ -n "$wa_avg" && -z "$wb_avg" ]]; then
              choose_a="true"
            fi
          fi
        fi

        if [[ "$choose_a" == "true" ]]; then
          wan_best_ip="$WAN_IP_A"; wan_ok="$wa_res"; wan_loss="$wa_loss"; wan_avg="$wa_avg"
        else
          wan_best_ip="$WAN_IP_B"; wan_ok="$wb_res"; wan_loss="$wb_loss"; wan_avg="$wb_avg"
        fi

        # Also record the alternate target’s stats
        if [[ "$wan_best_ip" == "$WAN_IP_A" ]]; then
          wan_alt_ip="$WAN_IP_B"; wan_alt_ok="$wb_res"; wan_alt_loss="$wb_loss"; wan_alt_avg="$wb_avg"
        else
          wan_alt_ip="$WAN_IP_A"; wan_alt_ok="$wa_res"; wan_alt_loss="$wa_loss"; wan_alt_avg="$wa_avg"
        fi


        if [[ "$wan_ok" != "ok" ]]; then
          status="WAN_DOWN"
        else
          # DNS check
          dns_ok="$(dns_check "$DNS_HOST")"
          if [[ "$dns_ok" != "ok" ]]; then
            status="DNS_DOWN"
          else
            # classify degraded if loss/latency exceed thresholds
            if awk "BEGIN{exit !($wan_loss >= $LOSS_WARN)}"; then
              status="WAN_DEGRADED"
            elif [[ -n "$wan_avg" ]] && awk "BEGIN{exit !($wan_avg >= $RTT_WARN_MS)}"; then
              status="WAN_DEGRADED"
            else
              status="OK"
            fi
          fi
        fi
      fi
    fi
  fi

  echo "$ts,$INTERFACE,$link_state,$gw_ip,$gw_ok,$gw_loss,$gw_avg,$wan_best_ip,$wan_ok,$wan_loss,$wan_avg,$wan_alt_ip,$wan_alt_ok,$wan_alt_loss,$wan_alt_avg,$DNS_HOST,$dns_ok,$status" >> "$LOG_FILE"

  sleep "$SLEEP_SECS"
done
