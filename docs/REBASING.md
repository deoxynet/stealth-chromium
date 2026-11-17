# Rebasing Stealth Chromium onto a new Chromium version

The maintenance loop for this repo. Budget an hour or two per major
version bump; most bumps only need minutes because patches are small and
independent.

## 0. See what breaks (no checkout needed)

```bash
tools/verify.sh 147.0.XXXX.YYY
```

This fetches only the upstream files the series touches (a few hundred KB)
and dry-runs every patch in order. Output is a per-patch PASS/FAIL table.
Everything green? Bump the pinned `BASE_TAG` in `tools/verify.sh` and
`docs/patch-status.md`, update `X-Spec` notes if paths moved, ship.

## 1. If patches fail

Each failing patch fails for one of three reasons:

1. **A touched file moved** (most common lately — e.g. at 146
   `chrome_main_delegate.cc` moved `chrome/browser/` → `chrome/app/`,
   `time_zone_controller.cc` moved to `core/timezone/`, and
   `ProfileNetworkContextService` moved to `chrome/browser/net/`).
   Fix: re-target the paths in the patch (and in the `X-Files` header if
   the path is baked in), keeping the same hunks.
2. **Context drifted** (the function you hook changed nearby lines).
   Fix: refresh the context lines of the hunk. The hook code itself rarely
   changes — resist improving it while rebasing.
3. **The surface itself changed upstream** (function renamed/removed, new
   parameter). Fix in a real checkout; see step 2.

## 2. Fix for real, in a checkout

```bash
# in your chromium/src checkout at the new version:
git checkout -b stealth-chromium-rebase
for p in ~/stealth-chromium/series/*.patch; do
  git am -3 "$p" || break
done
# fix the failing commit: edit files, then
git add -A && git am --continue     # or: git am --abort to retry
```

`git am -3` gives real 3-way merges. When a patch is beyond saving:
`git am --abort`, apply it manually, and re-commit with the same subject
plus `X-Spec` header.

## 3. Regenerate and verify

If you worked in a checkout, export the series back:

```bash
git format-patch <base>..HEAD -o ~/stealth-chromium/series/
```

(keep the numbering prefix, e.g. `git format-patch -N` then rename), or
edit the patch files directly for small fixes. Then:

```bash
tools/verify.sh            # against the pinned tag
tools/verify.sh <new-tag>  # confirm the new base is green
```

## 4. Update the bookkeeping

- `BASE_TAG` in `tools/verify.sh`
- `docs/patch-status.md` (base tag column, any path changes)
- CI schedule/matrix in `.github/workflows/verify.yml` if you track
  specific versions

## House rules that keep rebases cheap

- Logic lives in **new files**; keep existing-file edits to the minimum
  hunks (see `docs/ARCHITECTURE.md`).
- Never reformat upstream context you don't need to touch.
- One patch per surface — if a surface needs a real rewrite, do it in its
  own patch, not while fixing an unrelated one.
- Prefer appending to tables/arrays over inserting mid-table when both are
  valid — appends rarely conflict.
