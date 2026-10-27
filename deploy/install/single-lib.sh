# shellcheck shell=bash
# The single-node edition's platform settings (MVP 0.2.0 P2): sourced by al2023.sh when install.sh runs it (SINGLE_NODE).

single_node_access() {  # single_node_access PLATFORM_TOML [CF_DOMAIN]: who may reach the API
  # With CloudFront the API listens for it and serves only what carries the origin secret; without (--no-cloudfront)
  # there is no secret to check, so it listens on loopback only, where the SSM port forward reaches it. Rerunning
  # either way switches between the two.
  "$PYTHON" - "$1" "${2:-}" <<'KC_ACCESS_PY'
import re, sys
path, cloudfront = sys.argv[1:]
text = open(path).read()
def put(key, value):
    global text
    line = f"{key} = {value}"
    if re.search(rf"(?m)^{key}[ \t]*=", text):
        text = re.sub(rf"(?m)^{key}[ \t]*=.*$", lambda _: line, text, count=1)
    else:
        table = re.search(r"(?m)^\[", text)
        text = text[:table.start()] + line + "\n" + text[table.start():] if table else text.rstrip("\n") + "\n" + line + "\n"
if cloudfront:
    put("api_host", '"0.0.0.0"')
    put("origin_secret_file", '"/etc/kirocrew-on/origin-secret"')
else:
    put("api_host", '"127.0.0.1"')
    text = re.sub(r"(?m)^origin_secret_file[ \t]*=.*\n?", "", text)
open(path, "w").write(text)
KC_ACCESS_PY
}
