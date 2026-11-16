#!/usr/bin/env python3
"""守秘人檔案櫃：本機啟動器（用 ChatGPT 方案玩）

用法：python3 play.py        （Windows 可以用 py play.py）
會在這台電腦開一個只給自己用的小伺服器，並打開瀏覽器進入遊戲。
在遊戲的「設定 → 守秘人模型」選「ChatGPT 方案」，按 Continue with ChatGPT 登入。

設計重點
- 只聽 127.0.0.1，並檢查 Host 與 Origin，其他網站不能借用你的登入。
- ChatGPT 的登入憑證只存在這台電腦的設定檔（權限 0600），不交給網頁、不寫進瀏覽器。
- 網頁送來的模型請求由這裡加上憑證再轉給 OpenAI；權杖快到期時在這裡自動更新。
- 只用 Python 標準函式庫，不需要安裝任何套件。
流程依據 OpenAI「Sign in with ChatGPT」開源工具的文件（developers.openai.com/siwc）。
"""
import base64, hashlib, http.server, json, os, secrets, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid, webbrowser

VERSION = "1"
APP_NAME = "COC Keeper"
ROOT = os.path.dirname(os.path.abspath(__file__))
AUTH = os.environ.get("COC_SIWC_AUTH", "https://auth.openai.com").rstrip("/")
API = os.environ.get("COC_SIWC_API", "https://api.openai.com/v1").rstrip("/")
RESOURCE = "https://api.openai.com/v1"          # 權杖的 audience，固定不變
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
CALLBACK_PATH = "/auth/callback"
PORTS = [int(os.environ["COC_PORT"])] if os.environ.get("COC_PORT") else [1455, 1456, 1457, 1458, 54321]
# Responses API 在這條路上不接受的欄位（見 preview limitations）
FORBIDDEN = {"background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation", "multi_agent", "prompt",
             "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs", "top_p", "truncation", "user", "previous_response_id"}
TERMINAL_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}


def config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "coc-keeper")
    return os.path.join(os.path.expanduser("~"), ".coc-keeper")


CONF = os.environ.get("COC_SIWC_CONF") or os.path.join(config_dir(), "chatgpt.json")
LOCK = threading.Lock()          # 設定檔與權杖更新序列化，避免兩個請求同時用掉輪替中的 refresh token
PENDING = {}                     # state -> {verifier, nonce, client_id, created}
STATE = {"port": None}


# ---------- 設定檔 ----------
def load():
    try:
        with open(CONF, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if not d.get("host_id"):
        d["host_id"] = "urn:uuid:" + str(uuid.uuid4())   # 每台電腦一個，登出、換帳號都不變
        save(d)
    return d


def save(d):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    tmp = CONF + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONF)
    try:
        os.chmod(CONF, 0o600)
    except OSError:
        pass


# ---------- HTTP 小工具 ----------
def call(method, url, data=None, headers=None, form=False, timeout=30):
    body = None
    h = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jload(raw):
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return {"detail": raw.decode("utf-8", "replace")[:300]}


def err_code(j):
    e = j.get("error")
    if isinstance(e, dict):
        return e.get("code") or e.get("type") or ""
    return e if isinstance(e, str) else ""


_OIDC = {}


def oidc():
    if not _OIDC:
        s, raw = call("GET", AUTH + "/.well-known/openid-configuration")
        if s != 200:
            raise RuntimeError("無法讀取 OpenAI 的登入設定（HTTP %s）" % s)
        _OIDC.update(jload(raw))
    return _OIDC


# ---------- ID token 驗證（RS256，純 Python） ----------
def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def rsa_verify(n, e, msg, sig):
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    t = SHA256_PREFIX + hashlib.sha256(msg).digest()
    expect = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return secrets.compare_digest(em, expect)


def verify_id_token(tok, client_id, nonce):
    h64, p64, s64 = tok.split(".")
    head, claims = json.loads(b64d(h64)), json.loads(b64d(p64))
    if head.get("alg") != "RS256":
        raise ValueError("不支援的簽章演算法")
    conf = oidc()
    s, raw = call("GET", conf["jwks_uri"])
    keys = jload(raw).get("keys", [])
    key = next((k for k in keys if k.get("kid") == head.get("kid")), None) or (keys[0] if len(keys) == 1 else None)
    if not key:
        raise ValueError("找不到簽章金鑰")
    n, e = int.from_bytes(b64d(key["n"]), "big"), int.from_bytes(b64d(key["e"]), "big")
    if not rsa_verify(n, e, (h64 + "." + p64).encode(), b64d(s64)):
        raise ValueError("ID token 簽章不正確")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if claims.get("iss") != conf.get("issuer"):
        raise ValueError("ID token 的簽發者不符")
    if client_id not in auds:
        raise ValueError("ID token 的 audience 不符")
    if claims.get("exp", 0) < time.time() - 60:
        raise ValueError("ID token 已過期")
    if claims.get("nonce") != nonce:
        raise ValueError("ID token 的 nonce 不符")
    return claims


# ---------- 權杖 ----------
def store_tokens(d, tj):
    t = d.setdefault("tokens", {})
    t["access_token"] = tj["access_token"]
    if tj.get("refresh_token"):
        t["refresh_token"] = tj["refresh_token"]
    t["expires_at"] = time.time() + int(tj.get("expires_in") or 3600)
    if tj.get("scope"):
        t["scopes"] = tj["scope"].split()


def access_token(force=False):
    """回傳可用的 access token；需要時更新。失敗時丟出 (code, message)。"""
    with LOCK:
        d = load()
        t = d.get("tokens") or {}
        if not t.get("access_token"):
            raise PageError(401, "signed_out", "尚未登入 ChatGPT")
        if not force and t.get("expires_at", 0) - 120 > time.time():
            return t["access_token"]
        if not t.get("refresh_token"):
            d.pop("tokens", None); save(d)
            raise PageError(401, "signed_out", "登入已過期，請重新登入")
        s, raw = call("POST", AUTH + "/api/accounts/oauth/token", form=True, data={
            "grant_type": "refresh_token", "client_id": d["client_id"], "refresh_token": t["refresh_token"], "resource": RESOURCE})
        j = jload(raw)
        if s == 200 and j.get("access_token"):
            store_tokens(d, j); save(d)
            return d["tokens"]["access_token"]
        code = err_code(j) or j.get("error", "")
        if code in TERMINAL_REFRESH or s in (400, 401):
            d.pop("tokens", None); save(d)
            raise PageError(401, "signed_out", "ChatGPT 的登入已失效，請重新登入")
        raise PageError(503, "plan_unavailable", "更新登入時連不上 OpenAI（HTTP %s）" % s)


class PageError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def status():
    d = load()
    t = d.get("tokens") or {}
    return {"launcher": True, "version": VERSION, "signedIn": bool(t.get("access_token")),
            "email": d.get("email", ""), "planUsage": PLAN_SCOPE in (t.get("scopes") or []),
            "welcomed": bool(d.get("welcomed"))}


# ---------- 伺服器 ----------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "COCKeeper/" + VERSION

    def log_message(self, fmt, *args):      # 不印出網址，避免把授權參數寫進終端機紀錄
        pass

    # 只接受 127.0.0.1:<port>，擋掉 DNS rebinding；寫入類請求另外檢查 Origin
    def host_ok(self):
        return self.headers.get("Host") == "127.0.0.1:%d" % STATE["port"]

    def origin_ok(self):
        return self.headers.get("Origin") == "http://127.0.0.1:%d" % STATE["port"]

    def send_json(self, status_code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_html(self, status_code, html):
        b = html.encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(b)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4_000_000:
            raise PageError(413, "bad_request", "請求太大")
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_GET(self):
        if not self.host_ok():
            return self.send_error(403)
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    b = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b)
            elif path == "/siwc/status":
                self.send_json(200, status())
            elif path == "/siwc/login":
                self.start_login()
            elif path == CALLBACK_PATH:
                self.finish_login()
            elif path == "/siwc/models":
                self.models()
            else:
                self.send_error(404)
        except PageError as e:
            self.send_json(e.status, {"code": e.code, "message": e.message})
        except Exception as e:  # noqa: BLE001
            self.send_json(502, {"code": "upstream_error", "message": str(e)[:300]})

    def do_POST(self):
        if not (self.host_ok() and self.origin_ok()):
            return self.send_json(403, {"code": "forbidden", "message": "只接受來自本機遊戲頁面的請求"})
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/siwc/responses":
                self.proxy_responses()
            elif path == "/siwc/logout":
                self.logout()
            elif path == "/siwc/welcomed":
                with LOCK:
                    d = load(); d["welcomed"] = True; save(d)
                self.send_json(200, {"ok": True})
            else:
                self.send_error(404)
        except PageError as e:
            self.send_json(e.status, {"code": e.code, "message": e.message})
        except Exception as e:  # noqa: BLE001
            self.send_json(502, {"code": "upstream_error", "message": str(e)[:300]})

    # ----- 登入 -----
    def start_login(self):
        d = load()
        verifier = b64e(secrets.token_bytes(48))
        state, nonce = b64e(secrets.token_bytes(24)), b64e(secrets.token_bytes(24))
        client_id = d.get("client_id") or "dynamic_agent_client"
        q = {"response_type": "code", "client_id": client_id, "redirect_uri": self.redirect_uri(), "scope": SCOPE,
             "resource": RESOURCE, "state": state, "nonce": nonce, "code_challenge_method": "S256",
             "code_challenge": b64e(hashlib.sha256(verifier.encode()).digest()), "ext_agent_host_id": d["host_id"]}
        if client_id == "dynamic_agent_client":
            q["agent_name_hint"] = APP_NAME
        else:
            if d.get("id_token"):
                q["id_token_hint"] = d["id_token"]
            if d.get("email"):
                q["login_hint"] = d["email"]
        now = time.time()
        for k in [k for k, v in PENDING.items() if now - v["created"] > 900]:
            PENDING.pop(k, None)
        PENDING[state] = {"verifier": verifier, "nonce": nonce, "client_id": client_id, "created": now}
        self.send_response(302)
        self.send_header("Location", AUTH + "/api/accounts/authorize?" + urllib.parse.urlencode(q))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def redirect_uri(self):
        return "http://127.0.0.1:%d%s" % (STATE["port"], CALLBACK_PATH)

    def finish_login(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        p = PENDING.pop(q.get("state", ""), None)
        if not p:
            return self.send_html(400, page("登入沒有完成", "這個登入連結已失效或不是從遊戲發出的。請回到遊戲重新按 Continue with ChatGPT。", False))
        if q.get("error"):
            msg = ("登入取消了。如果同意畫面寫著「無法取得必要權限」，代表這個 ChatGPT 帳號的方案不能用在這個遊戲（免費方案就是這樣），請回到遊戲改用 API 金鑰。否則可以回到遊戲再試一次。"
                   if q["error"] == "access_denied" else "OpenAI 回報錯誤：" + q.get("error", "") + "。可以回到遊戲再試一次。")
            return self.send_html(400, page("登入沒有完成", msg, False))
        d = load()
        issued = q.get("client_id")
        if p["client_id"] == "dynamic_agent_client":
            if not issued:
                return self.send_html(400, page("登入沒有完成", "註冊沒有完成（沒有收到用戶端代號）。請回到遊戲再試一次。", False))
            client_id = issued
        else:
            if issued and issued != p["client_id"]:
                return self.send_html(400, page("登入沒有完成", "OpenAI 回傳的用戶端代號和之前不同，為了安全已中止。", False))
            client_id = p["client_id"]
        s, raw = call("POST", AUTH + "/api/accounts/oauth/token", form=True, data={
            "grant_type": "authorization_code", "client_id": client_id, "code": q.get("code", ""),
            "code_verifier": p["verifier"], "redirect_uri": self.redirect_uri(), "resource": RESOURCE})
        tj = jload(raw)
        if s != 200 or not tj.get("access_token"):
            if client_id != "dynamic_agent_client":
                with LOCK:
                    d = load(); d["client_id"] = client_id; save(d)
            return self.send_html(400, page("登入沒有完成", "換取權杖失敗（%s）。請回到遊戲再試一次。" % (err_code(tj) or s), False))
        try:
            claims = verify_id_token(tj.get("id_token", ""), client_id, p["nonce"])
        except Exception as e:  # noqa: BLE001
            return self.send_html(400, page("登入沒有完成", "身分驗證失敗：%s" % e, False))
        with LOCK:
            d = load()
            if d.get("sub") and d["sub"] != claims.get("sub"):
                d.pop("welcomed", None)        # 換了帳號，重新顯示一次說明
            d.update({"client_id": client_id, "sub": claims.get("sub"), "email": claims.get("email", ""), "id_token": tj.get("id_token")})
            d["tokens"] = {}
            store_tokens(d, tj)
            save(d)
            plan = PLAN_SCOPE in d["tokens"].get("scopes", [])
        if plan:
            self.send_html(200, page("登入完成", "已經用 %s 登入，遊戲會使用你的 ChatGPT 方案。這個分頁可以關掉，回到遊戲。" % (claims.get("email") or "你的 ChatGPT 帳號"), True))
        else:
            self.send_html(200, page("已登入，但沒有開啟方案用量", "登入時沒有同意讓這個遊戲使用你的 ChatGPT 方案，所以守秘人還不能用它。回到遊戲按登出後重新登入，在同意畫面允許使用方案即可。", True))

    def logout(self):
        with LOCK:
            d = load()
            t = d.get("tokens") or {}
            revoked = False
            if t.get("refresh_token"):
                try:
                    ep = oidc().get("revocation_endpoint")
                    if ep:
                        for _ in range(3):
                            s, _raw = call("POST", ep, form=True, data={"token": t["refresh_token"], "token_type_hint": "refresh_token", "client_id": d.get("client_id", "")})
                            if s == 200:
                                revoked = True; break
                            if s < 500:
                                break
                            time.sleep(1)
                except Exception:  # noqa: BLE001
                    revoked = False
            d.pop("tokens", None)          # 保留 client_id 與 host_id，下次登入沿用
            save(d)
        self.send_json(200, {"ok": True, "revoked": revoked})

    # ----- 模型 -----
    def models(self):
        tok = access_token()
        s, raw = call("GET", API + "/models", headers={"Authorization": "Bearer " + tok})
        if s == 401:
            tok = access_token(force=True)
            s, raw = call("GET", API + "/models", headers={"Authorization": "Bearer " + tok})
        j = jload(raw)
        if s != 200:
            raise PageError(s, map_error(s, j), j.get("error", {}).get("message") if isinstance(j.get("error"), dict) else str(j.get("detail", ""))[:300])
        out = []
        for m in j.get("data", []):
            if m.get("visibility", "list") != "list":
                continue
            out.append({"id": m.get("id"), "name": m.get("display_name") or m.get("id")})
        self.send_json(200, {"models": out})

    # ----- 推論：轉送並串流回網頁 -----
    def proxy_responses(self):
        body = self.body_json()
        body = {k: v for k, v in body.items() if k not in FORBIDDEN}
        body["store"], body["stream"] = False, True
        for attempt in (0, 1):
            tok = access_token(force=attempt == 1)
            req = urllib.request.Request(API + "/responses", data=json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "Accept": "text/event-stream"})
            try:
                r = urllib.request.urlopen(req, timeout=300)
            except urllib.error.HTTPError as e:
                j = jload(e.read())
                if e.code == 401 and attempt == 0 and err_code(j) != "subscription_sharing_invalid_user":
                    continue
                e_obj = j.get("error") if isinstance(j.get("error"), dict) else {}
                raise PageError(e.code, map_error(e.code, j), (e_obj.get("message") or str(j.get("detail", "")))[:300])
            break
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                chunk = r.read1(8192) if hasattr(r, "read1") else r.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                            # 玩家按了取消或關掉頁面
        finally:
            r.close()
        self.close_connection = True


def map_error(status_code, j):
    c = err_code(j)
    return {"subscription_sharing_usage_limit_exceeded": "plan_limit", "subscription_sharing_usage_unavailable": "plan_unavailable",
            "subscription_sharing_user_unavailable": "plan_unavailable", "subscription_sharing_user_not_eligible": "plan_not_eligible",
            "subscription_sharing_unsupported_capability": "bad_request", "subscription_sharing_invalid_user": "signed_out",
            "chatpass_v2_scope_not_authorized": "plan_scope", "chatpass_v2_invalid_authorization_context": "plan_scope"}.get(c) or (
        "signed_out" if status_code == 401 else "plan_not_eligible" if status_code == 403 else "plan_limit" if status_code == 429
        else "plan_unavailable" if status_code >= 500 else "bad_request")


def page(title, msg, ok):
    color = "#d7a445" if ok else "#c96b5b"
    return """<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1214;color:#e8e1d3;font-family:system-ui,"Noto Sans TC",sans-serif}
main{max-width:30em;padding:32px;border-top:2px solid %s;background:#141b1e}h1{font-size:20px;margin:0 0 12px}p{line-height:1.8;margin:0}</style></head>
<body><main><h1>%s</h1><p>%s</p></main><script>try{if(window.opener){window.opener.postMessage("siwc-done",location.origin);setTimeout(()=>window.close(),1500)}}catch(e){}</script></body></html>""" % (
        title, color, title, msg)


def main():
    if not os.path.exists(os.path.join(ROOT, "index.html")):
        sys.exit("找不到 index.html。請把 play.py 和 index.html 放在同一個資料夾。")
    srv = None
    for p in PORTS:
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", p), Handler)
            STATE["port"] = p
            break
        except OSError:
            continue
    if not srv:
        sys.exit("連接埠 %s 都被占用了，請關掉其他程式再試。" % ", ".join(map(str, PORTS)))
    load()
    url = "http://127.0.0.1:%d/" % STATE["port"]
    print("守秘人檔案櫃已在這台電腦啟動：" + url)
    print("在瀏覽器裡玩；要結束時回到這個視窗按 Ctrl+C。")
    if not os.environ.get("COC_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已關閉。")


if __name__ == "__main__":
    main()
