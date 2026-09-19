# Public release procedure

This folder can contain private Claude session exports. They are ignored by
Git. Before committing and pushing this repository, run:

```sh
python scripts/public_release_audit.py --check
```

The audit checks what Git would include from this folder against an explicit
list of code, tests, documentation, the synthetic JSONL fixture, and media
listed by SHA256 in `docs/media/manifest.json`. It rejects private home paths,
session identifiers, email addresses, likely credentials, files outside the
list, altered vendor bundles, and missing or changed media. It also catches
ignored private files that were force added to the Git index.
Source input hashes use LF line endings so the same manifest verifies on
Windows and Linux checkouts; `.gitattributes` enforces LF for published text.
The original `claudeinside` icon was generated for this repository. Its PNG
container's `caBX` provenance chunk was removed before publication without
changing the decoded pixels (RGBA SHA256
`a691fb5eba579b7c44f11695d7234414e014e2c4ae501ccd4f1d8e8be6402d92`).
The release audit pins the resulting icon file hash separately from the
synthetic session media manifest.

Review Git's staged file list and the video visually before pushing. Automated checks
cannot prove that every pixel is safe or erase content from an existing remote
or Git history. If anything private was committed previously, inspect history
and remove it from the remote separately before release.
