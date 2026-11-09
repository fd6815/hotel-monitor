"""查價前確認 NordVPN 沒開：VPN 的機房 IP 會被萬豪、IHG 擋（2026-11-09 查明）。
NordVPN 8.15 的命令列斷線（NordVPN.exe／NordVPNApp.exe -d、--disconnect）實測無效，所以只偵測、寫 log。"""
import subprocess

_PS = ("Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and "
       "($_.InterfaceDescription -match 'NordLynx|NordVPN' -or $_.Name -match 'NordLynx|NordVPN') } "
       "| Measure-Object | Select-Object -ExpandProperty Count")


def nord_connected() -> bool:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _PS], capture_output=True, text=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return int(out.strip() or 0) > 0
    except Exception:  # noqa: BLE001
        return False


def warn_if_on() -> bool:
    if nord_connected():
        print("  [vpn] NordVPN 連線中：官網可能擋 VPN 的 IP，被擋請先關 VPN")
        return True
    return False
