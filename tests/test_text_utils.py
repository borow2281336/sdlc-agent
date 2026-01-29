from sdlc_agent.text_utils import extract_first_json, extract_unified_diff


def test_extract_first_json_codeblock():
    txt = """hello
```json
{"a": 1, "b": "x"}
```
"""
    obj = extract_first_json(txt)
    assert obj["a"] == 1
    assert obj["b"] == "x"


def test_extract_unified_diff_from_codeblock():
    txt = """plan
```diff
diff --git a/foo.txt b/foo.txt
index 111..222 100644
--- a/foo.txt
+++ b/foo.txt
@@ -1 +1 @@
-hello
+hi
```
"""
    diff = extract_unified_diff(txt)
    assert "diff --git" in diff
