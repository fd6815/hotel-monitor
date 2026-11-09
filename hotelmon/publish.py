"""把房價資料推到 GitHub repo 的 site/data.json，GitHub Actions 再部署到 Pages 讓手機看。

需要 GitHub fine-grained token（只給該 repo 的 Contents: Read and write），
放在 config.yaml 的 github.token，或環境變數 GITHUB_TOKEN，或檔案 data/github_token.txt。
"""
import base64
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

import requests


def _token(cfg):
    gh = cfg.get("github") or {}
    t = gh.get("token") or os.environ.get("GITHUB_TOKEN")
    f = Path(cfg.get("data_dir", "data")) / "github_token.txt"
    if not t and f.exists():
        t = f.read_text(encoding="utf-8").strip()
    return t


def token_warning(cfg, db):
    """GitHub token 7 天內到期或已失效就回傳提醒文字，每天最多檢查一次。"""
    repo, token = (cfg.get("github") or {}).get("repo"), _token(cfg)
    key = f"token-check:{date.today()}"
    if not repo or not token or db.was_sent(key):
        return None
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    except Exception:  # noqa: BLE001
        return None
    db.mark_sent(key)
    how = "請到 GitHub 重新產生，更新電腦的 data\\github_token.txt 和手機網頁的設定"
    if r.status_code == 401:
        return f"【GitHub token】已失效，房價無法上傳。{how}"
    exp = r.headers.get("github-authentication-token-expiration")
    if not exp:
        return None
    day = datetime.strptime(exp[:10], "%Y-%m-%d").date()
    left = (day - date.today()).days
    return f"【GitHub token】還有 {left} 天到期（{day}）。{how}" if left <= 7 else None


def publish_data(cfg, file_path: Path):
    """data.json 沒實質變動就不上傳，省 commit 與部署；每 heartbeat_hours（3）小時至少傳一次，手機據此判斷電腦有沒有在跑。"""
    data = json.loads(file_path.read_text(encoding="utf-8"))
    data.pop("updated", None)
    for h in data.get("hotels", []):
        h.pop("last", None)
    slot = datetime.now().hour // int(cfg.get("heartbeat_hours", 3))
    sig = hashlib.sha256(f"{date.today()}:{slot}{json.dumps(data, sort_keys=True)}".encode()).hexdigest()
    f = Path(cfg.get("data_dir", "data")) / "publish.sig"
    if f.exists() and f.read_text() == sig:
        print("[publish] 資料沒變，略過上傳")
        return False
    ok = publish(cfg, file_path)
    if ok:
        f.write_text(sig)
    return ok


def publish(cfg, file_path: Path, path=None, message="更新房價資料"):
    gh = cfg.get("github") or {}
    repo = gh.get("repo")
    token = _token(cfg)
    if not repo or not token:
        print("[publish] 未設定 github.repo 或 token，略過上傳")
        return False
    path = path or gh.get("data_path", "site/data.json")
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        sha = None
        r = requests.get(api, headers=h, params={"ref": gh.get("branch", "main")}, timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")
        body = {
            "message": message,
            "content": base64.b64encode(file_path.read_bytes()).decode(),
            "branch": gh.get("branch", "main"),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(api, headers=h, json=body, timeout=30)
        r.raise_for_status()
        print(f"[publish] 已上傳 {repo}/{path}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[publish] 上傳失敗：{e}")
        return False
