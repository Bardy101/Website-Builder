"""Contact sheet — the primary cull mechanism.

Brief: docs/briefs/brief-visual-quality-signal.md, part 2.2.

The score orders; the eye judges. This renders every candidate as a
screenshot tile with its evidence, and lets the operator keep or cull each
one with a reason code. Decisions persist to localStorage and export as
decisions.json, which cull.py imports.

One self-contained HTML file: thumbnails are base64-inlined so it opens with
no network and can be sent anywhere. No server, no framework.
"""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
from typing import Optional

from .cull import REASON_CODES

THUMB_WIDTH = 400


def encode_thumbnail(path: Optional[str], width: int = THUMB_WIDTH) -> Optional[str]:
    """Resize to ~400px wide and return a data URI, or None if unusable."""
    if not path:
        return None
    source = Path(path)
    if not source.is_file():
        return None
    try:
        from PIL import Image

        with Image.open(source) as img:
            img = img.convert("RGB")
            if img.width > width:
                height = max(1, round(img.height * width / img.width))
                img = img.resize((width, height), Image.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=78, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        # A broken image must not take the whole sheet down.
        return None


def _cell_data(row: dict, businesses: dict) -> dict:
    place_id = row.get("place_id", "")
    business = businesses.get(place_id, {})
    desktop = business.get("screenshot_desktop") or ""
    return {
        "place_id": place_id,
        "name": row.get("name", ""),
        "town": row.get("town", ""),
        "lead_score": row.get("lead_score", ""),
        "site_verdict": row.get("site_verdict", ""),
        "staleness_points": row.get("staleness_points", ""),
        "website": row.get("website_url") or row.get("website") or "",
        "mobile_thumb": encode_thumbnail(row.get("screenshot_path")),
        "desktop_thumb": encode_thumbnail(desktop, width=1000),
    }


def build_cells(rows: list[dict], businesses: dict, *,
                sort: str = "score", min_score: Optional[int] = None) -> list[dict]:
    def score_of(row):
        try:
            return int(float(row.get("lead_score") or 0))
        except (TypeError, ValueError):
            return 0

    kept = rows
    if min_score is not None:
        kept = [r for r in kept if score_of(r) >= min_score]
    if sort == "name":
        kept = sorted(kept, key=lambda r: (r.get("name") or "").lower())
    else:
        kept = sorted(kept, key=score_of, reverse=True)
    return [_cell_data(r, businesses) for r in kept]


def render(cells: list[dict], *, batch_name: str) -> str:
    """The whole sheet as one string. Data goes in as JSON, not markup, so a
    business name containing quotes or angle brackets cannot break the page."""
    payload = json.dumps(
        {"batch": batch_name, "codes": list(REASON_CODES), "cells": cells}
    ).replace("</", "<\\/")
    title = html.escape(f"Contact sheet — {batch_name}")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #f6f6f4; --card: #fff; --ink: #1a1a1a; --muted: #666;
    --line: #e2e2df; --keep: #1f7a3d; --cull: #a32020; --accent: #1a4f8a;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  header {{ position: sticky; top: 0; z-index: 5; background: var(--card);
    border-bottom: 1px solid var(--line); padding: 14px 20px;
    display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }}
  h1 {{ font-size: 16px; margin: 0; font-weight: 650; }}
  .count {{ color: var(--muted); }}
  .spacer {{ flex: 1; }}
  button {{ font: inherit; cursor: pointer; border-radius: 6px;
    border: 1px solid var(--line); background: var(--card); padding: 7px 12px; }}
  button.primary {{ background: var(--accent); border-color: var(--accent);
    color: #fff; font-weight: 600; }}
  main {{ display: grid; gap: 16px; padding: 20px;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }}
  .cell {{ background: var(--card); border: 1px solid var(--line);
    border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }}
  .cell.keep {{ outline: 3px solid var(--keep); }}
  .cell.cull {{ outline: 3px solid var(--cull); opacity: .55; }}
  .shot {{ width: 100%; aspect-ratio: 390/500; object-fit: cover;
    object-position: top center; display: block; background: #eee;
    border-bottom: 1px solid var(--line); cursor: zoom-in; }}
  .noshot {{ display: flex; align-items: center; justify-content: center;
    aspect-ratio: 390/500; background: repeating-linear-gradient(45deg,
      #ececea, #ececea 12px, #e3e3e0 12px, #e3e3e0 24px);
    color: var(--muted); font-weight: 650; letter-spacing: .08em; }}
  .body {{ padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;
    flex: 1; }}
  .name {{ font-weight: 650; }}
  .meta {{ color: var(--muted); font-size: 13px; }}
  .badges {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .badge {{ font-size: 12px; padding: 2px 7px; border-radius: 999px;
    background: #eef1f5; color: #33507a; }}
  .badge.score {{ background: #1a4f8a; color: #fff; font-weight: 650; }}
  .badge.v-dated {{ background: #f6e0d8; color: #8a3a1a; }}
  .badge.v-poor {{ background: #f8eeda; color: #7a5a10; }}
  .badge.v-none, .badge.v-social_only {{ background: #dceadd; color: #1f5c33; }}
  a {{ color: var(--accent); }}
  .controls {{ display: flex; gap: 6px; margin-top: auto; padding-top: 6px; }}
  .controls button {{ flex: 1; }}
  .controls button.on-keep {{ background: var(--keep); color: #fff;
    border-color: var(--keep); }}
  .controls button.on-cull {{ background: var(--cull); color: #fff;
    border-color: var(--cull); }}
  select {{ font: inherit; width: 100%; padding: 6px; border-radius: 6px;
    border: 1px solid var(--line); }}
  select[hidden] {{ display: none; }}
  #lightbox {{ position: fixed; inset: 0; background: rgba(0,0,0,.82);
    display: none; align-items: center; justify-content: center; z-index: 10;
    padding: 24px; }}
  #lightbox.open {{ display: flex; }}
  #lightbox img {{ max-width: 100%; max-height: 100%; border-radius: 6px; }}
  @media print {{ header {{ position: static; }} .controls, select {{ display: none; }} }}
</style>
</head>
<body>
<header>
  <h1>Contact sheet</h1>
  <span class="count" id="summary"></span>
  <span class="spacer"></span>
  <button id="clear">Reset decisions</button>
  <button class="primary" id="export">Export decisions</button>
</header>
<main id="grid"></main>
<div id="lightbox"><img alt="Desktop screenshot"></div>
<script id="data" type="application/json">{payload}</script>
<script>
(function () {{
  var data = JSON.parse(document.getElementById("data").textContent);
  var storeKey = "contactsheet:" + data.batch;
  var state = {{}};
  try {{ state = JSON.parse(localStorage.getItem(storeKey)) || {{}}; }} catch (e) {{ state = {{}}; }}

  function save() {{
    try {{ localStorage.setItem(storeKey, JSON.stringify(state)); }} catch (e) {{}}
    summarise();
  }}

  function summarise() {{
    var keep = 0, cull = 0;
    Object.keys(state).forEach(function (id) {{
      if (state[id].decision === "keep") keep++;
      if (state[id].decision === "cull") cull++;
    }});
    document.getElementById("summary").textContent =
      data.cells.length + " candidates · " + keep + " keep · " + cull + " cull · " +
      (data.cells.length - keep - cull) + " undecided";
  }}

  var grid = document.getElementById("grid");
  var lightbox = document.getElementById("lightbox");
  var lightboxImg = lightbox.querySelector("img");

  data.cells.forEach(function (cell) {{
    var el = document.createElement("section");
    el.className = "cell";
    el.dataset.id = cell.place_id;

    if (cell.mobile_thumb) {{
      var img = document.createElement("img");
      img.className = "shot";
      img.loading = "lazy";
      img.src = cell.mobile_thumb;
      img.alt = cell.name;
      if (cell.desktop_thumb) {{
        img.addEventListener("click", function () {{
          lightboxImg.src = cell.desktop_thumb;
          lightbox.classList.add("open");
        }});
      }}
      el.appendChild(img);
    }} else {{
      var ph = document.createElement("div");
      ph.className = "noshot";
      ph.textContent = cell.site_verdict === "none" ? "NO WEBSITE" : "NO SCREENSHOT";
      el.appendChild(ph);
    }}

    var body = document.createElement("div");
    body.className = "body";

    var name = document.createElement("div");
    name.className = "name";
    name.textContent = cell.name;
    body.appendChild(name);

    var meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = cell.town;
    body.appendChild(meta);

    var badges = document.createElement("div");
    badges.className = "badges";
    var score = document.createElement("span");
    score.className = "badge score";
    score.textContent = "score " + cell.lead_score;
    badges.appendChild(score);
    if (cell.site_verdict) {{
      var v = document.createElement("span");
      v.className = "badge v-" + cell.site_verdict;
      v.textContent = cell.site_verdict;
      badges.appendChild(v);
    }}
    if (cell.staleness_points !== "" && cell.staleness_points !== null) {{
      var sp = document.createElement("span");
      sp.className = "badge";
      sp.textContent = "stale " + cell.staleness_points;
      badges.appendChild(sp);
    }}
    body.appendChild(badges);

    if (cell.website) {{
      var link = document.createElement("a");
      link.href = cell.website;
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      link.textContent = cell.website.replace(/^https?:\\/\\//, "").slice(0, 40);
      body.appendChild(link);
    }}

    var controls = document.createElement("div");
    controls.className = "controls";
    var keepBtn = document.createElement("button");
    keepBtn.textContent = "Keep";
    var cullBtn = document.createElement("button");
    cullBtn.textContent = "Cull";
    controls.appendChild(keepBtn);
    controls.appendChild(cullBtn);
    body.appendChild(controls);

    var reason = document.createElement("select");
    reason.hidden = true;
    var blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "reason…";
    reason.appendChild(blank);
    data.codes.forEach(function (code) {{
      var opt = document.createElement("option");
      opt.value = code;
      opt.textContent = code;
      reason.appendChild(opt);
    }});
    body.appendChild(reason);

    function paint() {{
      var entry = state[cell.place_id] || {{}};
      el.classList.toggle("keep", entry.decision === "keep");
      el.classList.toggle("cull", entry.decision === "cull");
      keepBtn.classList.toggle("on-keep", entry.decision === "keep");
      cullBtn.classList.toggle("on-cull", entry.decision === "cull");
      reason.hidden = entry.decision !== "cull";
      reason.value = entry.reason || "";
    }}

    keepBtn.addEventListener("click", function () {{
      var current = (state[cell.place_id] || {{}}).decision;
      if (current === "keep") delete state[cell.place_id];
      else state[cell.place_id] = {{ decision: "keep" }};
      paint(); save();
    }});
    cullBtn.addEventListener("click", function () {{
      var current = (state[cell.place_id] || {{}}).decision;
      if (current === "cull") delete state[cell.place_id];
      else state[cell.place_id] = {{ decision: "cull",
        reason: (state[cell.place_id] || {{}}).reason || "" }};
      paint(); save();
    }});
    reason.addEventListener("change", function () {{
      state[cell.place_id] = {{ decision: "cull", reason: reason.value }};
      paint(); save();
    }});

    el.appendChild(body);
    grid.appendChild(el);
    paint();
  }});

  lightbox.addEventListener("click", function () {{ lightbox.classList.remove("open"); }});
  document.addEventListener("keydown", function (e) {{
    if (e.key === "Escape") lightbox.classList.remove("open");
  }});

  document.getElementById("clear").addEventListener("click", function () {{
    if (!confirm("Clear every decision on this sheet?")) return;
    state = {{}};
    try {{ localStorage.removeItem(storeKey); }} catch (e) {{}}
    document.querySelectorAll(".cell").forEach(function (el) {{
      el.classList.remove("keep", "cull");
      el.querySelectorAll("button").forEach(function (b) {{
        b.classList.remove("on-keep", "on-cull");
      }});
      var sel = el.querySelector("select");
      if (sel) {{ sel.hidden = true; sel.value = ""; }}
    }});
    summarise();
  }});

  document.getElementById("export").addEventListener("click", function () {{
    var out = data.cells.filter(function (c) {{ return state[c.place_id]; }})
      .map(function (c) {{
        return {{ place_id: c.place_id, name: c.name,
                 decision: state[c.place_id].decision,
                 reason: state[c.place_id].reason || "" }};
      }});
    var blob = new Blob([JSON.stringify(out, null, 2)], {{ type: "application/json" }});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "decisions.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () {{ URL.revokeObjectURL(a.href); }}, 1000);
  }});

  summarise();
}})();
</script>
</body>
</html>
"""
