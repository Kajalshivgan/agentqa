# Baselines

Saved reference screenshots for `Assert Screenshot Matches` (visual regression). A baseline is
created automatically the first time a test runs with no matching file here; every later run
diffs against it.

Committed to the repo — review a baseline change in your diff tool the same way you'd review a
code change. To intentionally refresh one after a real UI change, re-run with
`update_baselines=true` rather than deleting the file by hand.
