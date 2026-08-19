# Vendored automation payload

The Play Console page flows are published here by the release process in the
private `google-play-automation` repo, so this public action needs no access to
it at runtime — which is the whole point of the split.

Until a release has run, this directory holds only this file and the action will
report that it cannot find the page flows.

Never edit these files here. They are overwritten wholesale on the next release;
change them in `google-play-automation` and cut a release.
