#!/usr/bin/env python3
"""把 src/template.html 和兩份守秘人手冊組成可部署的 index.html。
用法：python3 build.py
手冊會以 base64 編碼嵌進網頁（只是避免不小心看到，不是加密）。"""
import base64, pathlib
root = pathlib.Path(__file__).parent
html = (root / "src" / "template.html").read_text(encoding="utf-8")
for mark, name in (("__BIBLE1__", "bible.md"), ("__BIBLE2__", "bible2.md")):
    assert html.count(mark) == 1, f"template.html 裡找不到唯一的 {mark}"
    html = html.replace(mark, base64.b64encode((root / "src" / name).read_bytes()).decode())
(root / "index.html").write_text(html, encoding="utf-8")
print("built index.html", len(html), "bytes")
