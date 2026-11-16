#!/usr/bin/env python3
"""Sign in with ChatGPT 本機版的端對端測試：play.py 對著 tests/siwc_mock.py 跑完整流程。
用法：python3 build.py && python3 tests/siwc_test.py
需要 playwright 與 cryptography。只驗證流程與安全檢查；真的 OpenAI 要真人用帳號試。"""
import asyncio, json, os, stat, subprocess, sys, tempfile, time, pathlib, urllib.request, urllib.error
from playwright.async_api import async_playwright

root = pathlib.Path(__file__).resolve().parent.parent
MOCK, GAME = 18899, 18455
tmp = tempfile.mkdtemp(); conf = os.path.join(tmp, "chatgpt.json")
fails = []
def check(ok, name):
    print(("通過  " if ok else "沒過  ") + name)
    if not ok: fails.append(name)
def mock(path): return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{MOCK}{path}").read())
def raw(method, path, headers=None, body=b"{}"):
    req = urllib.request.Request(f"http://127.0.0.1:{GAME}{path}", data=body if method == "POST" else None, method=method, headers=headers or {})
    try: return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e: return e.code

env = dict(os.environ, COC_PORT=str(GAME), COC_SIWC_AUTH=f"http://127.0.0.1:{MOCK}", COC_SIWC_API=f"http://127.0.0.1:{MOCK}/v1", COC_SIWC_CONF=conf, COC_NO_BROWSER="1", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
pm = subprocess.Popen([sys.executable, str(root / "tests" / "siwc_mock.py"), str(MOCK)], stdout=subprocess.DEVNULL, env=env)
pg_ = subprocess.Popen([sys.executable, str(root / "play.py")], stdout=subprocess.DEVNULL, env=env)
time.sleep(1.5)

async def login(page, ctx):
    async with ctx.expect_page() as pop:
        await page.click('[data-siwc="login"]')
    p2 = await pop.value
    await p2.wait_for_load_state()
    return p2

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); ctx = await b.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page(); errs = []; page.on("pageerror", lambda e: errs.append(str(e)))
        await page.route("**/three.min.js", lambda r: r.fulfill(body="", content_type="text/javascript"))
        await page.add_init_script("localStorage.setItem('coc-dice-prefs',JSON.stringify({anim:false}))")
        await page.goto(f"http://127.0.0.1:{GAME}/"); await page.wait_for_selector("#shelf .folder"); await page.wait_for_timeout(500)
        check(await page.evaluate("LLM.SIWC.launcher===true&&!LLM.SIWC.signedIn"), "頁面偵測到啟動器，尚未登入")
        await page.click("#btnSetHome"); await page.wait_for_timeout(200)
        check(await page.input_value("#llmProv") == "chatgpt", "模型服務預設選到 ChatGPT 方案")
        check(await page.locator("#llmKey").count() == 0, "ChatGPT 方案不顯示 API 金鑰欄位")
        check((await page.inner_text('[data-siwc="login"]')).strip() == "Continue with ChatGPT", "登入按鈕文字是 Continue with ChatGPT")
        p2 = await login(page, ctx)
        check("登入完成" in await p2.inner_text("body"), "OAuth 走完，回呼頁顯示登入完成")
        await page.wait_for_timeout(1500)
        a = [e for e in mock("/__log") if e["ev"] == "authorize"][0]
        check(a["client_id"] == "dynamic_agent_client" and a.get("agent_name_hint") == "COC Keeper" and "id_token_hint" not in a, "第一次登入用 dynamic_agent_client 註冊並帶 agent_name_hint")
        t = [e for e in mock("/__log") if e["ev"] == "token"][0]
        check(t["client_id"] == "oaiapp_test123" and t["grant_type"] == "authorization_code", "用核發的 client_id 加 PKCE 換權杖（假伺服器驗過 verifier）")
        check(await page.is_visible("#siwcWelcome"), "第一次登入後顯示「你正在使用 ChatGPT 方案」說明")
        await page.click("#siwcGotIt")
        check("player@example.com" in await page.inner_text("#setCard") and "使用 ChatGPT 方案" in await page.inner_text("#setCard"), "設定顯示登入帳號與「使用 ChatGPT 方案」")
        opts = await page.eval_on_selector_all("#siwcModel option", "e=>e.map(o=>o.value+'|'+o.textContent)")
        check(opts == ["gpt-test-main|GPT 測試主力", "gpt-test-mini|GPT 測試輕量"], "模型清單只列 visibility=list，顯示 display_name")
        d = json.load(open(conf))
        check(stat.S_IMODE(os.stat(conf).st_mode) == 0o600 and d["tokens"]["access_token"].startswith("at_") and d["host_id"].startswith("urn:uuid:"), "憑證寫在本機設定檔，權限 0600，有 host ID")
        ls = await page.evaluate("JSON.stringify(localStorage)")
        check("at_" not in ls and "rt_" not in ls and "id_token" not in ls, "瀏覽器的 localStorage 裡沒有任何權杖")
        await page.click("#setClose")
        await page.click("#btnSetHome"); await page.wait_for_timeout(200); await page.click("#setClose")
        check(not await page.is_visible("#siwcWelcome"), "說明只顯示一次")
        # 遊戲
        await page.click('[data-new="ebb9"]'); await page.fill("#pcName", "林"); await page.click("#occs [data-occ]"); await page.click("#btnAttr"); await page.wait_for_timeout(400)
        await page.click('[data-mode="free"]'); await page.click("#btnStart"); await page.wait_for_timeout(3000)
        check("潮水退得很遠" in await page.inner_text("#log") and await page.evaluate("S.time") == "第1天 09:10", "開場由 ChatGPT 方案回應，標記照常生效")
        r = [e for e in mock("/__log") if e["ev"] == "responses"][-1]
        check("instructions" in r["keys"] and "store" in r["keys"] and "system" not in r["roles"] and r["model"] == "gpt-test-main", "請求帶 instructions、store:false，沒有 system 角色")
        st = await page.evaluate("S.stats||S.usage")
        check(st and st.get("inTok") == 7012 and st.get("real"), "觀察面板顯示回報的實際 token")
        check(await page.is_visible("#planNote"), "輸入框下方顯示「使用 ChatGPT 方案 · 管理用量」")
        # 權杖到期 → 自動更新
        d = json.load(open(conf)); d["tokens"]["expires_at"] = 0; json.dump(d, open(conf, "w"))
        await page.fill("#say", "看看四周"); await page.click("#btnSend"); await page.wait_for_timeout(2500)
        rf = [e for e in mock("/__log") if e["ev"] == "token" and e["grant_type"] == "refresh_token"]
        check(rf and rf[-1]["client_id"] == "oaiapp_test123" and rf[-1]["resource"] == "https://api.openai.com/v1" and "scope" not in rf[-1], "權杖到期時啟動器自動更新（client_id、resource 正確、不帶 scope）")
        check(json.load(open(conf))["tokens"]["refresh_token"] != d["tokens"]["refresh_token"], "refresh token 已輪替並存下新的")
        # 用量上限
        mock("/__mode?m=limit"); await page.fill("#say", "敲門"); await page.click("#btnSend"); await page.wait_for_timeout(2500)
        txt = await page.inner_text("#log")
        check("用量到上限" in txt and await page.locator('#log a[href="https://chatgpt.com/settings/usage"]').count() >= 1, "用量上限時說明原因，主要動作是「管理用量」")
        mock("/__mode?m=ineligible"); await page.locator("#log .rollcard button", has_text="重試").last.click(); await page.wait_for_timeout(2500)
        check("不能把方案用在這個遊戲" in await page.inner_text("#log"), "帳號不符資格時顯示說明")
        mock("/__mode?m=ok")
        # 安全檢查
        check(raw("POST", "/siwc/responses", {"Content-Type": "application/json"}) == 403, "沒有 Origin 的 POST 被拒")
        check(raw("POST", "/siwc/responses", {"Content-Type": "application/json", "Origin": "https://evil.example"}) == 403, "其他網站的 Origin 被拒")
        check(raw("GET", "/siwc/status", {"Host": f"evil.example:{GAME}"}) == 403, "Host 不是 127.0.0.1 被拒（防 DNS rebinding）")
        # 登出 → 撤銷 → 重新登入沿用 client_id
        await page.click("#btnSet"); await page.wait_for_timeout(200); await page.click('[data-siwc="logout"]'); await page.wait_for_timeout(800)
        rv = [e for e in mock("/__log") if e["ev"] == "revoke"]
        check(rv and rv[-1]["token_type_hint"] == "refresh_token" and rv[-1]["client_id"] == "oaiapp_test123", "登出時向 OpenAI 撤銷 refresh token")
        check("已登出" in await page.inner_text("#llmMsg") and not json.load(open(conf)).get("tokens") and json.load(open(conf)).get("client_id") == "oaiapp_test123", "登出清掉權杖，保留 client_id 與 host ID")
        p3 = await login(page, ctx); await page.wait_for_timeout(1500)
        a2 = [e for e in mock("/__log") if e["ev"] == "authorize"][-1]
        check(a2["client_id"] == "oaiapp_test123" and "agent_name_hint" not in a2 and a2.get("id_token_hint") and a2.get("login_hint") == "player@example.com", "重新登入沿用核發的 client_id，帶 id_token_hint，不帶 agent_name_hint")
        check(not await page.is_visible("#siwcWelcome"), "同一個帳號重新登入不再顯示說明")
        # 拒絕同意、沒給方案權限
        await page.click('[data-siwc="logout"]'); await page.wait_for_timeout(600)
        mock("/__mode?m=deny"); p4 = await login(page, ctx)
        check("登入取消了" in await p4.inner_text("body") and "免費方案" in await p4.inner_text("body"), "使用者在 OpenAI 按取消時顯示說明，不換權杖")
        mock("/__mode?m=noplan")
        p5 = await login(page, ctx); body5 = await p5.inner_text("body"); await page.wait_for_timeout(1500)
        check("沒有開啟方案用量" in body5 and "沒有同意讓這個遊戲使用" in await page.inner_text("#setCard") and await page.evaluate("LLM.current()") != "chatgpt", "沒給方案權限時保留登入但不拿來主持，並說明怎麼補")
        check(not errs, "頁面沒有執行錯誤" + ("" if not errs else "：" + errs[0][:120]))
        await b.close()
try:
    asyncio.run(main())
finally:
    pg_.terminate(); pm.terminate()
sys.exit(1 if fails else 0)
