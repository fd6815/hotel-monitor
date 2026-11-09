"""查價前確認 NordVPN 沒開：VPN 的機房 IP 會被萬豪、IHG 擋（2026-11-09 查明）。"""
import subprocess
import time
from pathlib import Path

NORD_EXE = Path(r"C:\Program Files\NordVPN\NordVPN.exe")
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


def ensure_off(wait=30) -> bool:
    """NordVPN 連線中就斷線並等網路切回家用 IP。回傳是否有關掉 VPN。"""
    if not NORD_EXE.exists() or not nord_connected():
        return False
    print("  [vpn] NordVPN 連線中，查價前先斷線")
    subprocess.run([str(NORD_EXE), "-d"], timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    end = time.time() + wait
    while time.time() < end and nord_connected():
        time.sleep(2)
    time.sleep(3)   # 等路由表、DNS 切回來
    print(f"  [vpn] {'仍未斷線' if nord_connected() else '已斷線'}")
    return True
