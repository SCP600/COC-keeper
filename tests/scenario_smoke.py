#!/usr/bin/env python3
"""劇本接線的瀏覽器冒煙測試（假的守秘人回應）。
用法：python3 build.py && python3 tests/scenario_smoke.py <劇本 id> [截圖資料夾]
需要 playwright（pip install playwright && playwright install chromium）。
只檢查「接得進去、跑得起來」，不印手冊內容。主持品質、聲音、動畫要真人試玩。"""
import asyncio, subprocess, sys, time, pathlib
from playwright.async_api import async_playwright
root = pathlib.Path(__file__).resolve().parent.parent
sid = sys.argv[1]; shots = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
PORT = 8791
INIT = """
localStorage.setItem('coc-dice-prefs',JSON.stringify({anim:false}));
window.__q=[];window.__sent=[];
window.claude={use:async()=>async(ms,o)=>{window.__sent.push(ms);const text=window.__q.shift()||'（沒有腳本）';o.onText({text});return {text,modelTierApplied:'test'}}};
"""
fails = []
def check(ok, name):
    print(("通過  " if ok else "沒過  ") + name)
    if not ok: fails.append(name)
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.route("**/three.min.js", lambda r: r.fulfill(body="", content_type="text/javascript"))
        await pg.add_init_script(INIT); await pg.goto(f"http://localhost:{PORT}/index.html"); await pg.wait_for_selector("#shelf .folder")
        check(await pg.locator(f'[data-new="{sid}"]').count() == 1, "檔案櫃有這個劇本的資料夾")
        if shots: await pg.screenshot(path=str(shots / "shelf.png"), full_page=True)
        await pg.click(f'[data-new="{sid}"]')
        info = await pg.evaluate("""()=>({era:SC.eraKey,inEra:!!ERA[SC.eraKey],occs:OCCS.length,
          missing:OCCS.flatMap(o=>o.skills).filter(k=>BASE[k]===undefined).length,eight:OCCS.every(o=>o.skills.length===8),
          amb:!!Snd.AMBS[SC.amb0],ends:['best','normal','bad','dead','insane'].every(k=>SC.ends[k]&&SC.ends[k].length===2),
          clues:[...BIBLE.matchAll(/^- (C\\d+)｜/gm)].map(m=>m[1]),truth:(/## 真相\\n([\\s\\S]*?)\\n## /.exec(BIBLE)||['',''])[1].length,
          placeholder:/^__BIBLE/.test(BIBLE),open:[...SC.opening.matchAll(/\\[\\[ITEM:\\+([^\\]]+)\\]\\]/g)].map(m=>m[1]),
          fallback:OCCS.flatMap(o=>o.items).filter(n=>icon(n).includes(FALLBACK)).length,
          hours:SC.hours,start:SC.start,unit:SC.unit})""")
        check(info["inEra"], "年代已登記在 ERA")
        check(info["occs"] == 6 and info["eight"], "六個職業、各八項本職技能")
        check(info["missing"] == 0, "本職技能都在技能表裡")
        check(info["fallback"] == 0, "起始物品都配得到圖示")
        check(info["amb"], "開場環境音在清單裡")
        check(info["ends"], "五種結局都有標題和一句話")
        check(not info["placeholder"] and info["truth"] > 100 and len(info["clues"]) >= 8, "手冊已嵌入，讀得到真相段落和線索清單")
        await pg.fill("#pcName", "測試員"); await pg.click("#occs [data-occ]"); await pg.click("#btnAttr"); await pg.wait_for_timeout(600)
        await pg.click('[data-mode="guide"]')
        items = "".join(f" [[ITEM:+{n}]]" for n in info["open"])
        await pg.evaluate("t=>window.__q.push(t)", f"開場敘述。 [[TIME:{info['start']}]]{items} [[AMB:{await pg.evaluate('SC.amb0')}]] [[OPT:甲]] [[OPT:乙]] [[OPT:丙]]")
        await pg.click("#btnStart"); await pg.wait_for_timeout(3500)
        sent = await pg.evaluate("JSON.stringify(window.__sent[0]||'')")
        dl = await pg.evaluate("SC.deadline")
        check(dl in sent.replace("\\n", ""), "送給模型的內容帶著期限文字")
        st = await pg.evaluate("({items:S.pc.items,opts:S.opts.length,amb:S.amb,cash:S.pc.cash})")
        check(all(n in st["items"] for n in info["open"]), "開場物品進了物品格")
        check(st["opts"] == 3, "引導模式的三個提示有出現")
        c1 = info["clues"][0]
        await pg.evaluate("t=>window.__q.push(t)", f"第二段敘述。 [[CLUE:{c1}|測試線索]] [[CASH:-1|測試]] [[SFX:{await pg.evaluate('Object.keys(Snd.FXS).pop()')}]]")
        await pg.fill("#say", "測試行動"); await pg.click("#btnSend"); await pg.wait_for_timeout(3500)
        st2 = await pg.evaluate("({clues:Object.keys(S.clues),cash:S.pc.cash})")
        check(c1 in st2["clues"] and st2["cash"] == max(0, st["cash"] - 1), "線索記入筆記、現金有扣")
        if shots: await pg.screenshot(path=str(shots / "game.png"))
        await pg.evaluate("window.__q.push('結尾。 [[END:best]]')")
        await pg.fill("#say", "收尾"); await pg.click("#btnSend"); await pg.wait_for_timeout(4000)
        check((await pg.evaluate("SC.ends.best[0]")) in await pg.inner_text("#endTitle"), "END 標記顯示出結局畫面")
        await pg.click("#btnTruth")
        check(await pg.locator("#truth li").count() == len(info["clues"]) and await pg.locator("#truth li.got").count() == 1, "揭曉真相列出全部線索並標出找到的")
        ok = True
        for k in ["normal", "bad", "dead", "insane"]:
            await pg.evaluate(f"S.end=null;finish('{k}')"); await pg.wait_for_timeout(150)
            ok = ok and (await pg.evaluate(f"SC.ends['{k}'][0]")) in await pg.inner_text("#endTitle")
        check(ok, "其餘四種結局的標題都顯示得出來")
        await pg.click("#btnHomeEnd"); await pg.wait_for_timeout(300)
        vols = await pg.evaluate("Object.keys(ERA).map(k=>[k,loadRoster().filter(x=>x.era===k).length])")
        check(all((n == 1) == (k == info["era"]) for k, n in vols), "新調查員只進了自己年代的名冊")
        check(not errs, "頁面沒有執行錯誤" + ("" if not errs else "：" + errs[0][:120]))
        await b.close()
srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--directory", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(1)
try: asyncio.run(main())
finally: srv.terminate()
sys.exit(1 if fails else 0)
