#!/usr/bin/env python3
"""守秘人手冊的靜態檢查。用法：python3 tests/check_bible.py src/bible3.md
只印出通過與否和規則名稱，不印手冊內容，可以放心在對話裡執行。"""
import re, sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
tpl = (root / "src" / "template.html").read_text(encoding="utf-8")
skills = set(re.findall(r'"([^"]+)":\d+', re.search(r"const BASE0=\{(.*?)\};", tpl).group(1)))
for extra in re.findall(r"extra:\{(.*?)\}", tpl):
    skills |= set(re.findall(r'"([^"]+)":\d+', extra))
bad = 0
def rule(ok, name):
    global bad
    print(("通過  " if ok else "沒過  ") + name)
    bad += 0 if ok else 1
for path in sys.argv[1:]:
    t = pathlib.Path(path).read_text(encoding="utf-8")
    print(f"== {path}")
    han = len(re.findall(r"[一-鿿]", t))
    rule(bool(re.search(r"## 真相\n([\s\S]*?)\n## ", t)), "有「## 真相」段落，後面接著下一個「## 」")
    codes = re.findall(r"^- (C\d+)｜.+$", t, re.M)
    rule(8 <= len(codes) <= 12, f"線索 8 到 12 條（現有 {len(codes)} 條）")
    rule(codes == [f"C{i}" for i in range(1, len(codes) + 1)], "線索編號從 C1 連續、不重複")
    sec = lambda h: (re.search(rf"## {h}[^\n]*\n([\s\S]*?)(?=\n## |\Z)", t) or [None, ""])[1]
    concl = [l for l in sec("核心結論").splitlines() if l.startswith("- ")]
    rule(len(concl) >= 2 and all(len(set(re.findall(r"C\d+", l.split("：")[-1]))) >= 3 for l in concl), "每個核心結論至少三條線索")
    rule(set(re.findall(r"C\d+", t)) <= set(codes), "提到的線索代號都在線索清單裡")
    rule(all(k in sec("結局條件") for k in ("best", "normal", "bad", "dead", "insane")), "五種結局條件齊全")
    for h in ("未來時鐘", "NPC", "地點與線索", "恐怖升級階梯"):
        rule(bool(sec(h).strip()), f"有「## {h}」")
    rule(len(re.findall(r"^- ", sec("未來時鐘"), re.M)) >= 4, "未來時鐘至少四個時間點")
    npcs = [l for l in sec("NPC").splitlines() if l.startswith("- ") and "說話：" in l]
    rule(5 <= len(npcs) <= 8 and all("示範：" in l for l in npcs), f"有名字的 NPC 五到八個，各有說話設定和示範句（現有 {len(npcs)} 個）")
    rule("語感：" in sec("NPC"), "NPC 一節開頭有語感設定")
    attrs = {"力量", "體質", "體型", "敏捷", "外貌", "智力", "靈感", "意志", "教育", "幸運"}
    known = skills | attrs
    stop = ("不需", "所有", "任何", "需要", "每個", "一次", "這個", "什麼", "的", "次", "或", "和", "、")
    unknown = [m for m in re.findall(r"([\u4e00-\u9fff（）／]{1,8})檢定", t) if not any(m.endswith(k) for k in known) and not m.endswith(stop)]
    rule(not unknown, "檢定用到的技能都在技能表裡" + ("" if not unknown else f"（{len(unknown)} 處對不上，請人工確認）"))
    rule(bool(re.search(r"命中|\d+%", t)) and bool(re.search(r"傷害 ?\d+d\d+", t)), "會動手的東西有命中率和傷害數值")
    rule(2800 <= han <= 4000, f"長度 2,800 到 4,000 個漢字（現有 {han}）")
sys.exit(1 if bad else 0)
