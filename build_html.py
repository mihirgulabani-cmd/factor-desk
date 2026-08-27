#!/usr/bin/env python3
"""build_html.py — embed model_output.json into the template → NSE-Factor-Desk.html

    python3 build_html.py --data ~/Downloads/screener_data [--template model_template.html]
"""
import os, json, argparse
ap = argparse.ArgumentParser()
ap.add_argument("--data", default=os.path.expanduser("~/Downloads/screener_data"))
ap.add_argument("--template", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_template.html"))
ap.add_argument("--out", default=None)
a = ap.parse_args()
with open(os.path.join(a.data, "model_output.json")) as f:
    data = f.read()
# guard against </script> inside embedded strings
data = data.replace("</", "<\\/")
with open(a.template) as f:
    tpl = f.read()
html = tpl.replace("__DATA__", data)
out = a.out or os.path.join(a.data, "NSE-Factor-Desk.html")
with open(out, "w") as f:
    f.write(html)
print(f"wrote {out} ({os.path.getsize(out)/1e6:.1f} MB)")
