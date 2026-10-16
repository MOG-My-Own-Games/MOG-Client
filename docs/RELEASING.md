# Releasing

A push builds the AppImage and the Windows exe and attaches them, with `SHA256SUMS.txt`, to a release (see
`.github/workflows/release.yml`), published at once and marked as a pre-release on a branch other than `main`.

## The release notes

`scripts/release_notes.py` writes the body of the release from the commits since the previous release (the highest `vX.Y.Z` tag
reachable from the commit; none: the whole history). A line is the commit's scope and description, without its type, and its
hash at the end as a link to the commit:

- *Changes*: `feat` and breaking changes (`!` or a `BREAKING CHANGE` footer).
- *Fixes*: `fix`, `perf` and a subject that is not a conventional commit (`scripts/version.py` counts that as a patch).
- Left out: `chore`, `doc`, `docs`, `res`, `style`, `test`, `refactor` and any other type.

The body is `.github/release-header.md` (the beta and as-is notice, and that the Windows build is untested), the lists and
**See changes**, a link comparing the two releases. Edit the header file to change the notice. The same lists, without the
header, are written as `mog_client/_changelog.py` and bundled into the build for the Changelog tab (see `changelog.py`); a build
made by hand has none.

Publishing the release creates the tag, which is what the next release counts from.

## Signing the updates

The self-updater downloads the build named in the latest release and checks it against `SHA256SUMS.txt`. That alone catches a
damaged download, not a release somebody replaced on GitHub, since the checksums would be replaced with it. So the checksums are
signed (Ed25519) and the app checks the signature against a public key built into it.

One time, on a machine you trust:

1. `scripts/sign-release.py --generate` prints a secret seed and a public key.
2. Save the seed as the repository secret `MOG_RELEASE_SIGNING_KEY` (Settings, Secrets and variables, Actions). It is never
   committed and never leaves GitHub's secrets.
3. Put the public key in `RELEASE_PUBLIC_KEY` in `mog_client/updater.py` and release.

From then on the workflow writes `SHA256SUMS.txt.sig` next to the checksums, and an app that has the key refuses an update
that is unsigned or signed by another key. Builds made before the key was added keep checking the checksums only, so they
should be updated by hand once.

If the seed leaks, make a new pair, replace the secret and the constant, and release: old apps that trusted the leaked key
need a manual update, since an attacker holding it can sign what they accept.
