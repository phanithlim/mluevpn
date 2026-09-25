# Releasing a new version

Step-by-step for shipping a new MLUEVPN version to GitHub and the AUR.
Follow it top to bottom; the order matters in two places, and both are
called out where they bite.

## What you are updating

Three separate things carry a version, and only the first is authoritative.

| Where | What it is | Who updates it |
|---|---|---|
| `pyproject.toml` | The one true version | **You**, by hand |
| `packaging/PKGBUILD` | Local test builds (`build.sh`), `sha256sums=SKIP` | `build.sh`, automatically |
| `aur-mluevpn/PKGBUILD` | What AUR users actually build | **You**, by hand |

`aur-mluevpn/` is a *separate git clone* of
`ssh://aur@aur.archlinux.org/mluevpn.git` that happens to sit inside this
directory. It is in `.gitignore` and must never be committed to the GitHub
repo — it once went in as a gitlink and gave every fresh clone a broken empty
folder.

There is also `packaging/aur/PKGBUILD`, an older copy that nothing reads.
Ignore it, or delete it; do not edit it and expect anything to happen.

## Before you start

- The change you are releasing must actually work. Test from source:

  ```bash
  cd ~/Projects/myvpn && PYTHONPATH=src python3 -m mluevpn
  ```

  This runs the working tree, not the installed `/usr/bin/mluevpn`. Quit any
  running copy first or GTK will just raise the old window instead of starting
  a new one.

- `git status` is clean, and your AUR SSH key is registered on your AUR account.

---

## 1. Bump the version

Only `pyproject.toml`. The app reads its own version from installed metadata,
so nothing in `src/` needs touching.

```bash
sed -i '3s/version = ".*"/version = "0.1.3"/' pyproject.toml
grep '^version' pyproject.toml     # confirm
```

## 2. Commit and push to GitHub

```bash
git add -A
git commit -m "Short description of the change"
git push origin main
```

## 3. Tag it — use `-a`

```bash
git tag -a v0.1.3 -m "v0.1.3"
git push origin v0.1.3
```

**Use `-a`.** A plain `git tag v0.1.3` makes a *lightweight* tag, and
`git push --follow-tags` silently skips those — you get a green push and no
tag on GitHub. Check which kind you made:

```bash
git cat-file -t v0.1.3      # "tag" = annotated (good), "commit" = lightweight
```

Pushing the tag by name, as above, works for either kind.

## 4. Wait for the tarball to exist

**This is the order that matters.** The PKGBUILD downloads
`$url/archive/refs/tags/v$pkgver.tar.gz`, which GitHub generates on demand
from the tag. Until the tag is pushed, that URL is a 404 and step 6 aborts.

```bash
curl -sIL -o /dev/null -w "%{http_code}\n" \
  https://github.com/phanithlim/mluevpn/archive/refs/tags/v0.1.3.tar.gz
```

Wait for `200`. Use `-L`: without it you get a `302` redirect to codeload and
cannot tell success from failure.

## 5. Point the AUR PKGBUILD at the new version

```bash
cd aur-mluevpn
sed -i 's/^pkgver=.*/pkgver=0.1.3/; s/^pkgrel=.*/pkgrel=1/' PKGBUILD
```

`pkgrel` goes back to `1` on every new `pkgver`. You only bump `pkgrel`
(1 → 2) when you fix the *packaging* while upstream stays the same.

## 6. Refresh the checksum

```bash
updpkgsums
```

This downloads the new tarball and rewrites `sha256sums`. Verify it actually
changed — this is the single most common way to ship a broken AUR package:

```bash
grep sha256sums PKGBUILD
```

If it still shows the previous release's hash, `updpkgsums` failed (almost
always a 404 from step 4) and **everyone's build will fail**.

## 7. Regenerate `.SRCINFO` — after step 6, never before

```bash
makepkg --printsrcinfo > .SRCINFO
```

`.SRCINFO` is a frozen snapshot of the PKGBUILD that the AUR web front end
reads. Generate it before `updpkgsums` and you bake in the stale checksum.
The AUR rejects a push whose `.SRCINFO` disagrees with its PKGBUILD.

## 8. Build and check it locally

```bash
makepkg -f
namcap mluevpn-0.1.3-1-any.pkg.tar.zst
tar -tf mluevpn-0.1.3-1-any.pkg.tar.zst | grep -c '^home/'   # must print 0
```

That last check catches the virtualenv trap described in
[design.md](design.md): if a venv is active, `python -m installer` lays files
out under the venv prefix instead of `/usr`. Never run `makepkg` with sudo.

## 9. Push to the AUR

```bash
git add PKGBUILD .SRCINFO
git commit -m "Update to 0.1.3"
git push origin master
```

Two traps here:

- The AUR branch is **`master`**, not `main`.
- **Name the two files.** `makepkg` just dropped `src/`, `pkg/` and a
  `.pkg.tar.zst` in this directory, and this repo has no `.gitignore`, so
  `git add -A` would commit all of it.

## 10. Install and verify

```bash
sudo pacman -U mluevpn-0.1.3-1-any.pkg.tar.zst
pacman -Q mluevpn
```

Close and reopen the app — a running copy keeps the old code.

---

## Final check

```bash
cd ~/Projects/myvpn && git rev-list --left-right --count origin/main...main
cd aur-mluevpn      && git rev-list --left-right --count origin/master...master
```

Both should print `0  0`. Anything else means something never left your machine.

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `curl: (22) ... error: 404` from `updpkgsums` | Tag not on GitHub yet | `git push origin v<ver>`, wait for 200, retry |
| Tag missing after `push --follow-tags` | Lightweight tag | `git push origin v<ver>`; use `git tag -a` next time |
| AUR rejects the push | `.SRCINFO` stale | Rerun step 7, amend, push |
| Users report a checksum mismatch | `.SRCINFO` built before `updpkgsums` | Redo steps 6–7, bump `pkgrel`, push |
| `aur-mluevpn` shows up in `git status` of the main repo | Tracked as a gitlink | `git rm --cached aur-mluevpn` — `.gitignore` alone will not help a tracked path |
