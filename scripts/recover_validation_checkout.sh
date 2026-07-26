#!/bin/sh
set -eu

# One-time recovery for files copied into the production checkout by validation
# before isolated /tmp validation became mandatory. Changes are stashed, not lost.
EXPECTED_PATHS="
frontend/assets/dashboard_lg_remote.js
install.sh
tests/test_epic_09_lg_tv_frontend.py
tests/test_hotfix_pack_21_runtime_config.py
tests/test_smart_control_01a_lg.py
"

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

if [ -n "$(git diff --cached --name-only)" ]; then
    echo "ERROR: staged changes exist; recovery made no changes." >&2
    exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "ERROR: untracked files exist; recovery made no changes." >&2
    exit 1
fi

CHANGED=$(git diff --name-only)
if [ -z "$CHANGED" ]; then
    echo "Checkout is already clean."
    exit 0
fi

for path in $CHANGED; do
    case "
$EXPECTED_PATHS" in
        *"
$path
"*) ;;
        *)
            echo "ERROR: unexpected modified path: $path; recovery made no changes." >&2
            exit 1
            ;;
    esac
done

git stash push -m "recover validation-contaminated production checkout" -- \
    frontend/assets/dashboard_lg_remote.js \
    install.sh \
    tests/test_epic_09_lg_tv_frontend.py \
    tests/test_hotfix_pack_21_runtime_config.py \
    tests/test_smart_control_01a_lg.py

if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: checkout is still dirty after recoverable stash." >&2
    exit 1
fi

echo "Known validation changes were saved in the Git stash."
echo "Checkout is clean and ready for git pull --ff-only."
