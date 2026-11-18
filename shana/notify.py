"""Send a notification to the Mac and, when a topic is set, to the phone through ntfy."""
from __future__ import annotations

import urllib.request

from shana import config
from shana.util import append_jsonl, log_error, now, run

_MAC_SCRIPT = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
               "-e", "end run"]


def send(title: str, message: str, *, priority: str = "default", tags: str = "") -> None:
    cfg = config.load()["notify"]
    append_jsonl(config.path("data", "notifications.jsonl"), {"ts": now().isoformat(), "title": title, "message": message})
    if cfg.get("mac"):
        try:
            run(["osascript", *_MAC_SCRIPT, title, message], timeout=10)
        except Exception as e:  # noqa: BLE001 - a failed ping must never break the caller
            log_error("notify.mac", e)
    topic = cfg.get("ntfy_topic")
    if topic:
        try:
            req = urllib.request.Request(f"{cfg['ntfy_server'].rstrip('/')}/{topic}", data=message.encode(),
                                         headers={"Title": title.encode("utf-8").decode("latin-1", "ignore"),
                                                  "Priority": priority, "Tags": tags})
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:  # noqa: BLE001
            log_error("notify.ntfy", e)
