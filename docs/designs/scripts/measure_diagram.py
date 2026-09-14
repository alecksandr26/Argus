#!/usr/bin/env python3
"""Render a Mermaid diagram via mermaid.ink and measure how much of its
bounding box is actually covered by content (clusters + top-level nodes),
so 'empty space' complaints about a diagram like
docs/designs/semantic-design-overview.md can be checked against a real
number instead of guessed at.

mermaid.ink's HTTP API 403s on Python's default User-Agent — it needs a
browser-looking one, which is why render_svg() sets one explicitly.

Usage: python3 measure_diagram.py diagram.mmd [out.svg]

Where diagram.mmd is the raw Mermaid source (e.g. everything between the
```mermaid fences in semantic-design-overview.md, %%{init...}%% line
included).
"""
import sys
import re
import base64
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
NS = "{http://www.w3.org/2000/svg}"
TRANSLATE_RE = re.compile(r"translate\(([-.\d]+)[, ]+([-.\d]+)\)")


def render_svg(mmd_path: str) -> str:
    code = open(mmd_path, encoding="utf-8").read()
    b64 = base64.urlsafe_b64encode(code.encode()).decode()
    url = f"https://mermaid.ink/svg/{b64}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def own_translate(el):
    m = TRANSLATE_RE.search(el.get("transform") or "")
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def walk(el, dx, dy, clusters, nodes):
    odx, ody = own_translate(el)
    dx, dy = dx + odx, dy + ody

    classes = (el.get("class") or "").split()
    if "cluster" in classes:
        rect = el.find(f"{NS}rect")
        if rect is not None and rect.get("width"):
            rx, ry = float(rect.get("x", 0)), float(rect.get("y", 0))
            clusters.append({
                "id": el.get("id"), "x": dx + rx, "y": dy + ry,
                "w": float(rect.get("width")), "h": float(rect.get("height")),
            })
    elif "node" in classes and el.get("id"):
        # node g's own transform translate is its center point
        w = h = None
        rect = el.find(f"{NS}rect")
        if rect is not None and rect.get("width"):
            w, h = float(rect.get("width")), float(rect.get("height"))
        else:
            circ = el.find(f"{NS}circle")
            if circ is not None and circ.get("r"):
                r = float(circ.get("r"))
                w = h = 2 * r
        if w and h:
            nodes.append({"id": el.get("id"), "x": dx - w / 2, "y": dy - h / 2, "w": w, "h": h})

    for child in el:
        walk(child, dx, dy, clusters, nodes)


def is_nested(c, others):
    for o in others:
        if o is c:
            continue
        if (o["x"] <= c["x"] + 0.5 and o["y"] <= c["y"] + 0.5
                and o["x"] + o["w"] >= c["x"] + c["w"] - 0.5
                and o["y"] + o["h"] >= c["y"] + c["h"] - 0.5
                and (o["w"] * o["h"]) > (c["w"] * c["h"])):
            return True
    return False


def main():
    mmd_path = sys.argv[1]
    out_svg = sys.argv[2] if len(sys.argv) > 2 else "diagram_render.svg"
    svg_text = render_svg(mmd_path)
    open(out_svg, "w", encoding="utf-8").write(svg_text)

    root = ET.fromstring(svg_text)
    vb = root.get("viewBox")
    _, _, canvas_w, canvas_h = (float(x) for x in vb.split())
    canvas_area = canvas_w * canvas_h
    print(f"Canvas: {canvas_w:.0f} x {canvas_h:.0f}  (area {canvas_area:,.0f})")
    print()

    clusters, nodes = [], []
    walk(root, 0.0, 0.0, clusters, nodes)

    top_clusters = [c for c in clusters if not is_nested(c, clusters)]
    # a node counts as "free" if it's not inside any top-level cluster's bbox
    def inside_any(n, cs):
        cx, cy = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
        return any(c["x"] <= cx <= c["x"] + c["w"] and c["y"] <= cy <= c["y"] + c["h"] for c in cs)
    free_nodes = [n for n in nodes if not inside_any(n, top_clusters)]

    content_area = sum(c["w"] * c["h"] for c in top_clusters) + sum(n["w"] * n["h"] for n in free_nodes)
    fill_ratio = content_area / canvas_area if canvas_area else 0.0

    print("Top-level clusters:")
    for c in sorted(top_clusters, key=lambda c: c["x"]):
        print(f"  {c['id']!r:26s} x=[{c['x']:.0f},{c['x']+c['w']:.0f}]"
              f" y=[{c['y']:.0f},{c['y']+c['h']:.0f}]  {c['w']:.0f}x{c['h']:.0f}"
              f"  area={c['w']*c['h']:,.0f}")
    print(f"  + {len(free_nodes)} free top-level node(s) outside any cluster:")
    for n in sorted(free_nodes, key=lambda n: n["x"]):
        print(f"     {n['id']!r:26s} x=[{n['x']:.0f},{n['x']+n['w']:.0f}]"
              f" y=[{n['y']:.0f},{n['y']+n['h']:.0f}]")
    print()
    print(f"Content fill ratio (content area / canvas area): {fill_ratio:.1%}")
    print(f"  -> empty space: {1 - fill_ratio:.1%} of the canvas")
    print()

    ordered = sorted(top_clusters, key=lambda c: c["x"])
    for a, b in zip(ordered, ordered[1:]):
        gap = b["x"] - (a["x"] + a["w"])
        print(f"Horizontal gap between {a['id']!r} and {b['id']!r}: {gap:.0f}px")
    if ordered:
        print(f"Left margin (canvas edge -> first cluster): {ordered[0]['x']:.0f}px")
        print(f"Right margin (last cluster -> canvas edge): {canvas_w - (ordered[-1]['x']+ordered[-1]['w']):.0f}px")
        top_y = min(c["y"] for c in ordered)
        bot_y = max(c["y"] + c["h"] for c in ordered)
        print(f"Top margin: {top_y:.0f}px   Bottom margin: {canvas_h - bot_y:.0f}px")

    # Inside each top-level cluster, how full is IT (its own children vs its own box)?
    print()
    print("Per-cluster internal fill (nested clusters' area / parent cluster area):")
    for c in ordered:
        nested = [o for o in clusters if o is not c and o["x"] >= c["x"] - 1 and o["y"] >= c["y"] - 1
                  and o["x"] + o["w"] <= c["x"] + c["w"] + 1 and o["y"] + o["h"] <= c["y"] + c["h"] + 1
                  and (o["w"] * o["h"]) < (c["w"] * c["h"])]
        # only direct-ish children: drop any nested-in-nested duplicates by keeping maximal ones
        direct = [n for n in nested if not is_nested(n, nested)]
        nested_area = sum(n["w"] * n["h"] for n in direct)
        own_area = c["w"] * c["h"]
        ratio = nested_area / own_area if own_area else 0
        names = ", ".join(n["id"].replace("mermaid-svg-", "") for n in direct) or "(no nested clusters)"
        print(f"  {c['id']!r:26s} {ratio:.1%} filled by [{names}]")


if __name__ == "__main__":
    main()
