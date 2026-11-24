#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
root=$(pwd)
bundle="$root/build/MeMyselfAI.app"
contents="$bundle/Contents"
resources="$contents/Resources"
mkdir -p "$contents/MacOS" "$resources/ui" "$root/build/ModuleCache"
cp desktop/Info.plist "$contents/Info.plist"
cp desktop/MeMyselfAI.icns "$resources/"
cp desktop/launch_service.py server.py store.py engine.py suggestions.py desktop_chats.py bugflow.py connectors.py workspace_bridge.py google_workspace.py account_oauth.py github_signin.py signin.py worker.py "$resources/"
cp selfcheck.py workscope.py "$resources/"
mkdir -p "$resources/skills/task-interaction"
cp skills/task-interaction/SKILL.md "$resources/skills/task-interaction/"
mkdir -p "$resources/tests"
cp tests/test_bug_integration.py tests/test_http.py "$resources/tests/"
mkdir -p "$resources/vendor/google-workspace"
cp vendor/google-workspace/darwin-v0.0.8.tar.gz vendor/google-workspace/LICENSE vendor/google-workspace/account-email.cjs "$resources/vendor/google-workspace/"
sh scripts/bundle-google-runtime.sh "$root/build/runtime-cache" "$contents"
cp -R ui/. "$resources/ui/"
python3 - "$resources/oauth-clients.json" <<'PY'
import json
import sys
from pathlib import Path
from signin import CLIENTS_FILE, registration

target = Path(sys.argv[1])
if CLIENTS_FILE.exists():
    supplied = json.loads(CLIENTS_FILE.read_text())
    if not isinstance(supplied, dict) or set(supplied) - {"email", "slack"}:
        raise SystemExit("oauth-clients.json must contain email or slack desktop app registrations.")
    clients = {kind: registration(kind, fields) for kind, fields in supplied.items()}
    target.write_text(json.dumps(clients))
else:
    target.unlink(missing_ok=True)
PY
xcrun swiftc -target "$(uname -m)-apple-macosx12.0" -swift-version 5 -O -module-cache-path "$root/build/ModuleCache" \
    -framework AppKit -framework WebKit desktop/main.swift -o "$contents/MacOS/MeMyselfAI"
test -x "$contents/MacOS/MeMyselfAI"
plutil -lint "$contents/Info.plist" >/dev/null
codesign --force --sign - "$bundle"
codesign --verify "$bundle"
printf '%s\n' "$bundle"
