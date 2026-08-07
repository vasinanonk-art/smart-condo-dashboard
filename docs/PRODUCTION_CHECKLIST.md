# v1.0.1 Production Checklist

Verification date: **2026-08-07 Asia/Bangkok**

Release commit: the commit referenced by annotated tag `v1.0.1`

Immediate rollback commit:
`0886e10fc0911505933ac577f9c942a8fa060591`

## Completed

- [x] Complete TinkerBoard suite passes with no failures.
- [x] Node-backed chart and frontend behavior tests pass locally.
- [x] JavaScript syntax and `git diff --check` pass.
- [x] Runtime-only deployment completed through the exclusive deployment lock.
- [x] Persistent runtime configuration protection remains enabled.
- [x] Service is active after deployment.
- [x] Unauthenticated `GET /` returns 303.
- [x] `GET /api/auth/status` returns 200.
- [x] No new startup errors were found in the deployment journal.
- [x] PM2.5, temperature, humidity, and electricity endpoints were physically
  verified on an iPad with deterministic preview data.
- [x] Authentication and CSRF regression coverage passes.
- [x] EPIC 17 TP-Link APIs remain authenticated, read-only, redacted, and
  fail-closed.
- [x] EPIC 18 control-center assets are deployed.
- [x] No destructive state or history migration is part of v1.0.1.
- [x] Tapo snapshot and on-demand live routes remain authenticated proxies.
- [x] go2rtc v1.9.14 ARM artifact and SHA-256 are pinned and independently
  verified.
- [x] go2rtc rollback, idempotency, permissions, listener, and redaction tests
  pass.

## Outstanding verification before tagging

- [x] Run and retain the final Python compilation report.
- [x] Run and retain the final shell syntax report.
- [ ] Run `sudo ./install.sh --dry-run` against the tag candidate.
- [ ] Confirm a current backup of `/root/.smart-condo-dashboard`.
- [ ] Confirm a current backup of `/etc/default/smart-condo-dashboard`.
- [ ] Record backup location and checksum without printing secret contents.
- [ ] Complete a current Safari, Chrome, Edge, and Firefox smoke test.
- [ ] Complete keyboard-only and VoiceOver checks.
- [ ] Confirm the production browser console has no uncaught errors or failed
  managed asset requests.
- [ ] Verify idle CPU, memory, task count, and restart count after ten minutes.
- [x] Review and approve the final changelog and release notes.
- [x] Create the local annotated `v1.0.1` tag after required repository checks.
- [ ] Push the approved release commit and annotated tag.
- [ ] Deploy and complete production smoke/soak verification.

EPIC 19 device-health milestones and the verified EPIC 20–22 camera and IR
foundations are included in the v1.0.1 tag candidate.

## Functional release checklist

| Area | Status | Required final check |
|---|---|---|
| Authentication | Complete | Login, logout, expiry, and CSRF rejection |
| Dashboard | Complete | Navigate every page |
| Device cards | Complete | Confirm provider state matches displayed state |
| TP-Link provider | Complete | Read-only status and safe diagnostics |
| Charts | Complete | Real and preview endpoint selection |
| Notifications | Complete | Read, mark, clear, Escape, outside click |
| Responsive layout | Complete | iPad portrait/landscape and desktop |
| Dark theme | Complete | v1.0.1 is explicitly dark-only |
| Light theme | Deferred | Planned for EPIC 20 |
| Error/loading states | Complete | One safe unavailable-provider check |
| Browser matrix | Pending | Current Safari, Chrome, Edge, Firefox |
| Accessibility | Pending | Keyboard and VoiceOver |
| Runtime deployment | Complete | Guarded runtime-only installer |
| Backup/restore | Pending final backup | Archive and verify persistent files |
| Release notes | Complete | Final stakeholder approval pending |
| Git tag | Complete locally | Annotated `v1.0.1`; push pending |

## Backup

As root, stop writes or take a consistent filesystem snapshot, then archive:

- `/root/.smart-condo-dashboard`
- `/etc/default/smart-condo-dashboard`
- production commit and managed-runtime identification

Store the archive outside both source and runtime directories with root
ownership and mode 0600. Do not print, inspect in chat, or commit its secret
contents.

## Release verification command

```sh
cd /opt/smart-condo-dashboard
git worktree add /opt/smart-condo-dashboard-v1.0.1 \
  v1.0.1
cd /opt/smart-condo-dashboard-v1.0.1
test -z "$(git status --short)" &&
test "$(git rev-parse HEAD)" = "$(git rev-list -n 1 v1.0.1)" &&
test "$(cat VERSION)" = "1.0.1" &&
git diff --check &&
/opt/smart-condo-dashboard-run/venv/bin/python -m pytest -q &&
find backend -name '*.py' -print0 | \
  xargs -0 /opt/smart-condo-dashboard-run/venv/bin/python -m py_compile &&
find frontend -name '*.js' -print0 | xargs -0 -n1 node --check &&
sh -n install.sh scripts/*.sh &&
sudo ./install.sh --dry-run
```

## Post-deployment read-only checks

```sh
sudo systemctl status smart-condo-dashboard.service --no-pager -l
curl -sS -o /dev/null -w "home=%{http_code}\n" http://127.0.0.1:8090/
curl -sS -o /dev/null -w "auth=%{http_code}\n" \
  http://127.0.0.1:8090/api/auth/status
sudo journalctl -u smart-condo-dashboard.service -n 100 --no-pager
sudo systemctl show smart-condo-dashboard.service \
  -p MainPID -p MemoryCurrent -p TasksCurrent -p NRestarts
```

Expected results:

- service is active
- `home=303`
- `auth=200`
- no new startup exception or restart loop
- authenticated health contains no secret or filesystem-path exposure
- camera absence is shown as configuration unavailable, not falsely offline
- background task count remains stable

## Rollback procedure

Do not reset or overwrite a dirty production checkout.

```sh
cd /opt/smart-condo-dashboard
git status --short
git worktree add /opt/smart-condo-dashboard-rollback \
  0886e10fc0911505933ac577f9c942a8fa060591
cd /opt/smart-condo-dashboard-rollback
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
sudo systemctl status smart-condo-dashboard.service --no-pager -l
curl -sS -o /dev/null -w "home=%{http_code}\n" http://127.0.0.1:8090/
curl -sS -o /dev/null -w "auth=%{http_code}\n" \
  http://127.0.0.1:8090/api/auth/status
```

If the installer reports a retained backup directory, stop and inspect it
before another deployment. Restore persistent state only when its integrity is
verified and a state rollback is necessary. v1.0.1 does not require a
persistent-data rollback.

## Tag procedure

After outstanding release checks are approved:

```sh
git tag -a v1.0.1 \
  -m "Smart Condo Dashboard v1.0.1" \
  HEAD
git show --stat --oneline v1.0.1
git push origin v1.0.1
```

Do not move or recreate the tag after publication.
