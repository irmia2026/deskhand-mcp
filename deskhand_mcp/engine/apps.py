"""Windows StartApps discovery and dispatch-only application launching."""

from __future__ import annotations

import json
import os
import subprocess


def list_start_apps() -> list[dict]:
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress",
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=15, check=False, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Get-StartApps 失败: {result.stderr.strip()}")
    raw = result.stdout.strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return [{"name": str(item.get("Name") or ""),
             "app_id": str(item.get("AppID") or "")}
            for item in data if item.get("AppID")]


def launch_app(app: str, args=None) -> dict:
    app = str(app or "").strip()
    if not app:
        raise ValueError("app 不能为空")
    if args is None:
        args = []
    if not isinstance(args, list) or any(not isinstance(v, str) for v in args):
        raise ValueError("args 必须是字符串数组")
    if "!" in app and not os.path.exists(app):
        command = ["explorer.exe", f"shell:AppsFolder\\{app}"]
        method = "startapps"
    else:
        command = [app, *args]
        method = "executable"
    process = subprocess.Popen(command, shell=False)
    return {"ok": True, "dispatched": True, "ready": None, "method": method,
            "command": command, "launcher_pid": process.pid}
