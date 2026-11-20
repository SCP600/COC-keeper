#!/usr/bin/env python3
"""把 src/template.html 和各份守秘人手冊組成 index.html。
用法：python3 build.py             → index.html（公開版）
      python3 build.py --private   → private/index.html（公開版＋private/ 裡的私人劇本）
手冊會以 base64 編碼嵌進網頁（只是避免不小心看到，不是加密）。
private/ 不進 git：放的是非原創、只供自己玩的劇本。格式見 SPEC 第 7.5 節。"""
import base64, pathlib, re, sys
root = pathlib.Path(__file__).parent
b64 = lambda p: base64.b64encode(p.read_bytes()).decode()
html = (root / "src" / "template.html").read_text(encoding="utf-8")
for mark, name in (("__BIBLE1__", "bible.md"), ("__BIBLE2__", "bible2.md"), ("__BIBLE3__", "bible3.md")):
    assert html.count(mark) == 1, f"template.html 裡找不到唯一的 {mark}"
    html = html.replace(mark, b64(root / "src" / name))
SLOT = "/*__PRIVATE_SCENARIOS__*/"
assert html.count(SLOT) == 1, "template.html 裡找不到私人劇本的插槽"
if "--private" in sys.argv:
    pdir = root / "private"
    js = (pdir / "scenarios.js").read_text(encoding="utf-8").strip().rstrip(",")
    # 私人手冊的佔位符寫成 __PBIBLE:檔名__
    for name in sorted(set(re.findall(r"__PBIBLE:([\w.-]+)__", js))):
        js = js.replace(f"__PBIBLE:{name}__", b64(pdir / name))
    out = pdir / "index.html"
    out.write_text(html.replace(SLOT, "," + js), encoding="utf-8")
    print("built private/index.html", out.stat().st_size, "bytes")
else:
    html = html.replace(SLOT, "")
    (root / "index.html").write_text(html, encoding="utf-8")
    print("built index.html", len(html), "bytes")
