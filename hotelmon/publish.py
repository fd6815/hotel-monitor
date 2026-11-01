"""把日曆頁推到 GitHub repo 的 site/index.html，GitHub Actions 再部署到 Pages 讓手機看。

需要 GitHub fine-grained token（只給該 repo 的 Contents: Read and write），
放在 config.yaml 的 github.token，或環境變數 GITHUB_TOKEN，或檔案 data/github_token.txt。
"""
import base64
import os
from pathlib import Path

import requests


def _token(cfg):
    gh = cfg.get("github") or {}
    t = gh.get("token") or os.environ.get("GITHUB_TOKEN")
    f = Path(cfg.get("data_dir", "data")) / "github_token.txt"
    if not t and f.exists():
        t = f.read_text(encoding="utf-8").strip()
    return t


def publish(cfg, html_path: Path):
    gh = cfg.get("github") or {}
    repo = gh.get("repo")
    token = _token(cfg)
    if not repo or not token:
        print("[publish] 未設定 github.repo 或 token，略過上傳")
        return False
    path = gh.get("path", "site/index.html")
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        sha = None
        r = requests.get(api, headers=h, params={"ref": gh.get("branch", "main")}, timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")
        body = {
            "message": "更新房價日曆",
            "content": base64.b64encode(html_path.read_bytes()).decode(),
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
