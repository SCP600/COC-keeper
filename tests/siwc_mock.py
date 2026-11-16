#!/usr/bin/env python3
"""測試用的假 OpenAI：Sign in with ChatGPT 的授權伺服器加上 /v1/models、/v1/responses。
照 developers.openai.com/siwc 的開源工具流程實作，會檢查啟動器送來的參數。
只給 tests/siwc_test.py 用；需要 cryptography 套件來簽 ID token。"""
import base64, hashlib, http.server, json, secrets, sys, threading, time, urllib.parse
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB = KEY.public_key().public_numbers()
b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
ib = lambda n: n.to_bytes((n.bit_length() + 7) // 8, "big")

S = {"mode": "ok", "log": [], "codes": {}, "access": set(), "refresh": set(), "issued": "oaiapp_test123"}
FORBIDDEN = {"background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation", "multi_agent", "prompt",
             "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs", "top_p", "truncation", "user", "previous_response_id"}


def jwt(claims):
    h = b64(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
    p = b64(json.dumps(claims).encode())
    sig = KEY.sign((h + "." + p).encode(), padding.PKCS1v15(), hashes.SHA256())
    return h + "." + p + "." + b64(sig)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def base(self): return "http://" + self.headers["Host"]

    def j(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/.well-known/openid-configuration":
            return self.j(200, {"issuer": self.base(), "jwks_uri": self.base() + "/jwks", "revocation_endpoint": self.base() + "/revoke"})
        if u.path == "/jwks":
            return self.j(200, {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256", "use": "sig", "n": b64(ib(PUB.n)), "e": b64(ib(PUB.e))}]})
        if u.path == "/__mode":
            S["mode"] = q.get("m", "ok"); return self.j(200, {"mode": S["mode"]})
        if u.path == "/__log":
            return self.j(200, S["log"])
        if u.path == "/api/accounts/authorize":
            S["log"].append({"ev": "authorize", **q})
            ok = (q.get("response_type") == "code" and q.get("redirect_uri", "").startswith("http://127.0.0.1:") and q.get("redirect_uri", "").endswith("/auth/callback")
                  and q.get("code_challenge_method") == "S256" and q.get("code_challenge") and q.get("resource") == "https://api.openai.com/v1"
                  and "chatgpt.tokens.use.direct" in q.get("scope", "") and q.get("ext_agent_host_id", "").startswith("urn:uuid:") and q.get("state") and q.get("nonce"))
            if not ok:
                return self.j(400, {"error": "invalid_request", "got": q})
            ret = {"state": q["state"]}
            if S["mode"] == "deny":
                ret["error"] = "access_denied"
            else:
                code = secrets.token_urlsafe(16)
                S["codes"][code] = q
                ret["code"] = code
                if q["client_id"] == "dynamic_agent_client":
                    ret["client_id"] = S["issued"]
            self.send_response(302); self.send_header("Location", q["redirect_uri"] + "?" + urllib.parse.urlencode(ret)); self.end_headers(); return
        if u.path == "/v1/models":
            if not self.authed(): return self.j(401, {"error": {"code": "invalid_api_key", "message": "bad token"}})
            return self.j(200, {"data": [{"id": "gpt-test-main", "display_name": "GPT 測試主力", "visibility": "list"},
                                         {"id": "gpt-test-mini", "display_name": "GPT 測試輕量", "visibility": "list"},
                                         {"id": "gpt-hidden", "display_name": "不該出現", "visibility": "hide"}]})
        self.j(404, {})

    def authed(self):
        a = self.headers.get("Authorization", "")
        return a.startswith("Bearer ") and a[7:] in S["access"]

    def tokens(self, client_id, nonce, plan=True):
        at, rt = "at_" + secrets.token_urlsafe(12), "rt_" + secrets.token_urlsafe(12)
        S["access"] = {at}; S["refresh"] = {rt}
        scope = "openid profile email offline_access resource.invoke" + (" chatgpt.tokens.use.direct" if plan else "")
        out = {"access_token": at, "refresh_token": rt, "token_type": "Bearer", "expires_in": 3600, "scope": scope}
        if nonce is not None:
            out["id_token"] = jwt({"iss": self.base(), "aud": client_id, "sub": "user_abc", "email": "player@example.com", "nonce": nonce, "exp": int(time.time()) + 3600, "iat": int(time.time())})
        return out

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/api/accounts/oauth/token":
            f = self.form(); S["log"].append({"ev": "token", **{k: v for k, v in f.items() if k not in ("code_verifier",)}})
            if f.get("grant_type") == "authorization_code":
                q = S["codes"].pop(f.get("code", ""), None)
                if not q: return self.j(400, {"error": "invalid_grant"})
                chal = b64(hashlib.sha256(f.get("code_verifier", "").encode()).digest())
                if chal != q["code_challenge"] or f.get("redirect_uri") != q["redirect_uri"] or f.get("client_id") != S["issued"] or f.get("resource") != "https://api.openai.com/v1":
                    return self.j(400, {"error": "invalid_grant", "why": "pkce/redirect/client mismatch"})
                return self.j(200, self.tokens(S["issued"], q["nonce"], plan=S["mode"] != "noplan"))
            if f.get("grant_type") == "refresh_token":
                if f.get("refresh_token") not in S["refresh"] or f.get("client_id") != S["issued"] or f.get("resource") != "https://api.openai.com/v1" or "scope" in f:
                    return self.j(400, {"error": "invalid_grant"})
                return self.j(200, self.tokens(S["issued"], None))
            return self.j(400, {"error": "unsupported_grant_type"})
        if u.path == "/revoke":
            f = self.form(); S["log"].append({"ev": "revoke", **f}); S["refresh"].discard(f.get("token")); S["access"] = set()
            self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers(); return
        if u.path == "/v1/responses":
            n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n))
            S["log"].append({"ev": "responses", "keys": sorted(body), "model": body.get("model"), "roles": [m.get("role") for m in body.get("input", [])]})
            if not self.authed(): return self.j(401, {"error": {"code": "invalid_token", "message": "expired"}})
            bad = [k for k in body if k in FORBIDDEN]
            if body.get("store") is not False or body.get("stream") is not True or bad or any(m.get("role") == "system" for m in body.get("input", [])) or not body.get("instructions"):
                return self.j(400, {"error": {"code": "subscription_sharing_unsupported_capability", "param": ",".join(bad) or "store/stream/system", "message": "rejected"}})
            if S["mode"] == "ineligible":
                return self.j(403, {"error": {"code": "subscription_sharing_user_not_eligible", "message": "not eligible"}})
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            def ev(o):
                self.wfile.write(("event: " + o["type"] + "\ndata: " + json.dumps(o, ensure_ascii=False) + "\n\n").encode()); self.wfile.flush(); time.sleep(0.02)
            ev({"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}})
            if S["mode"] == "limit":
                ev({"type": "response.failed", "response": {"id": "resp_1", "status": "failed", "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}); return
            last = body["input"][-1]["content"] if body.get("input") else ""
            text = "收到" if "收到" in last else "潮水退得很遠。房東在門口等你。 [[TIME:第1天 09:10]] [[AMB:sea]]"
            for i in range(0, len(text), 6):
                ev({"type": "response.output_text.delta", "delta": text[i:i + 6]})
            ev({"type": "response.completed", "response": {"id": "resp_1", "status": "completed", "usage": {"input_tokens": 7012, "output_tokens": 95}}})
            return
        self.j(404, {})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    print("mock on", port, flush=True)
    srv.serve_forever()
