# devtool 0.3.5

- Fixed Android HUD Java compilation by handling the checked `JSONException` thrown by `new JSONArray(raw)`.
- Malformed `steps_json` now falls back to the current-operation step instead of failing compilation or crashing parsing.
- Updated the Android HUD app to version 1.5.4.
- Added a source regression test for the JSON exception handling path.
