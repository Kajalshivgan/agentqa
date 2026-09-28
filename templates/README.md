# Templates

Reference images for `Assert Image Visible` / `Tap On Image` — one small cropped PNG per named
element that has no usable text, resource-id, or content-desc (a map preview, a video thumbnail,
a custom-drawn icon). A crop taken at a different screen resolution will not match reliably.

Capture one with:
```bash
python -m agents.recorder_agent capture-template --name <name> --text "<label>"    # by locator
python -m agents.recorder_agent capture-template --name <name> --bounds l,t,r,b    # by raw pixels
```
It rejects a crop that's nearly a single flat color — matching against one is unreliable (see
`engine/image_match.py`'s `MIN_TEMPLATE_STDDEV`) — so pick a region with real visual detail.

Committed to the repo like source code — these are part of the test suite.
