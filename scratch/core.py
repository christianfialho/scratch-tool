"""scratch.core — shared storage logic for the scratch scratchpad.

This module holds the pure file/frontmatter logic plus structured operations
so that BOTH the CLI (cli.py) and the MCP server (scratch-mcp/server.py) share
one implementation and can never drift out of sync.

Store layout (one root per profile, passed via env):
    <root>/topics/<slug>.md   live pages (current state)
    <root>/cabinet/<slug>.md  archived pages (moved out of scratch)

Root resolution, in priority order:
    $SCRATCH_HOME   profile-scoped path set by the MCP server config
    $SCRATCH_DIR    dev/test override (used by the test suite)
    ~/.scratch      default for plain CLI use

Hierarchy ("topics that spawn from topics"):
  Every page knows only its DIRECT relations, tracked via frontmatter:
    parent:   <slug>       (empty when top-level)
    children: [slug, ...]  (direct descendants)
    siblings: [slug, ...]  (children of the same parent — a snapshot
                            refreshed on structural writes, so it always
                            matches "who the siblings were when written")
  The agent never manages ancestry — only `new` (auto-nests under the current
  focus, or an explicit parent) changes structure. `archive` moves a page +
  re-parents its children one level up; `restore` reverses that.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #


def store_root() -> Path:
    for var in ("SCRATCH_HOME", "SCRATCH_DIR"):
        env = os.environ.get(var)
        if env:
            return Path(env).expanduser()
    return Path.home() / ".scratch"


def topics_dir(root: Path) -> Path:
    return root / "topics"


def cabinet_dir(root: Path) -> Path:
    return root / "cabinet"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


def slugify(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "untitled"


# --------------------------------------------------------------------------- #
# Frontmatter helpers (tiny on purpose — no external yaml dep)
# --------------------------------------------------------------------------- #


def read_page(path: Path):
    """Return (frontmatter_dict, body_str). Missing keys default to ''.

    Link list fields (children/siblings) and tags are left as raw strings
    here; parse them with _parse_list() at the point of use.
    """
    text = path.read_text(encoding="utf-8")
    fm: dict = {}
    body = text
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == 3:
            raw, body = parts[1], parts[2].lstrip("\n")
            for line in raw.strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
    return fm, body


def render_page(fm: dict, body: str) -> str:
    def clean(v):
        if isinstance(v, list):
            return "[" + ", ".join(str(x) for x in v) + "]"
        return str(v)

    lines = ["---"]
    order = ["title", "tags", "summary", "created", "updated",
             "parent", "children", "siblings"]
    for k in order:
        if k in fm and fm[k]:
            lines.append(f"{k}: {clean(fm[k])}")
    for k, v in fm.items():
        if k not in order and v:
            lines.append(f"{k}: {clean(v)}")
    lines.append("---")
    body = body.strip("\n")
    return "\n".join(lines) + ("\n\n" + body if body else "\n")


def _bump(path: Path, fm: dict, body: str):
    fm["updated"] = now_iso()
    path.write_text(render_page(fm, body), encoding="utf-8")


def find_slug(root: Path, slug: str) -> Path:
    return topics_dir(root) / (slug + ".md")


def corpus_tags(fm: dict):
    raw = fm.get("tags", "")
    if not raw:
        return []
    return _parse_list(raw)


def index_rows(root: Path):
    """Yield (slug, fm, body, mtime) for every page, sorted by mtime desc."""
    rows = []
    td = topics_dir(root)
    if td.exists():
        for f in sorted(td.glob("*.md")):
            try:
                fm, body = read_page(f)
            except Exception:
                continue
            rows.append((f.stem, fm, body, f.stat().st_mtime))
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Link-field accessors (single source of truth for the hierarchy)
# --------------------------------------------------------------------------- #


def _parse_list(v):
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    if not v.strip():
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _parent(fm: dict) -> str:
    return (fm.get("parent") or "").strip()


def _children(fm: dict) -> list:
    return _parse_list(fm.get("children", ""))


def _siblings(fm: dict) -> list:
    return _parse_list(fm.get("siblings", ""))


def _read(root: Path, slug: str):
    path = find_slug(root, slug)
    if not path.exists():
        return None
    fm, body = read_page(path)
    return path, fm, body


def _refresh_siblings(root: Path, slugs: set):
    """Recompute the stored siblings snapshot for every node around `slugs`.

    Siblings is defined as "children of my parent, minus me". Because it is a
    stored snapshot (per the spec), after any structural write we refresh the
    affected neighbourhood so it can never drift.
    """
    to_check = set(slugs) if slugs else set()
    for s in list(to_check):
        r = _read(root, s)
        if r:
            _p, fm, _b = r
            p = _parent(fm)
            if p:
                to_check.add(p)
            to_check.update(_children(fm))
    for s in to_check:
        r = _read(root, s)
        if not r:
            continue
        path, fm, body = r
        p = _parent(fm)
        sibs = []
        if p:
            pr = _read(root, p)
            if pr:
                sibs = [x for x in _children(pr[1]) if x != s]
        if _siblings(fm) != sibs:
            fm["siblings"] = sibs
            _bump(path, fm, body)


# --------------------------------------------------------------------------- #
# Structured operations (shared by CLI and MCP server)
# --------------------------------------------------------------------------- #


def _page_meta(slug: str, fm: dict, body: str) -> dict:
    return {
        "slug": slug,
        "title": fm.get("title") or slug,
        "tags": corpus_tags(fm),
        "summary": fm.get("summary", ""),
        "created": fm.get("created", ""),
        "updated": fm.get("updated", ""),
        "parent": _parent(fm),
        "children": _children(fm),
        "siblings": _siblings(fm),
        "body": body,
    }


def op_new(title: str, parent: str | None = None) -> dict:
    root = store_root()
    slug = slugify(title)
    path = find_slug(root, slug)
    if path.exists():
        # Never clobber and never add a wrong edge: return the existing page.
        return {"ok": True, "created": False, "slug": slug, "path": str(path)}
    # Validate parent up front — never create an orphan against a bad parent.
    if parent and _read(root, parent) is None:
        return {"ok": False, "created": False, "slug": slug,
                "error": f"parent '{parent}' not found"}
    td = topics_dir(root)
    td.mkdir(parents=True, exist_ok=True)
    now = now_iso()
    fm = {"title": title.strip(), "tags": [], "summary": "",
          "created": now, "parent": parent or "", "children": []}
    path.write_text(render_page(fm, ""), encoding="utf-8")
    if parent:
        pr = _read(root, parent)
        ppath, pfm, pbody = pr
        pc = _children(pfm)
        if slug not in pc:
            pc.append(slug)
            pfm["children"] = pc
            _bump(ppath, pfm, pbody)
        _refresh_siblings(root, {slug, parent})
    return {"ok": True, "created": True, "slug": slug, "path": str(path)}


def op_get(slug: str):
    root = store_root()
    path = find_slug(root, slug)
    if not path.exists():
        return None
    fm, body = read_page(path)
    return _page_meta(slug, fm, body)


def op_list(tag: str | None = None) -> list[dict]:
    rows = index_rows(store_root())
    if tag:
        need = {t.strip().lower() for t in tag.split(",")}
        rows = [r for r in rows if need <= {t.lower() for t in corpus_tags(r[1])}]
    out = []
    for slug, fm, _body, mtime in rows:
        item = _page_meta(slug, fm, "")
        item.pop("body", None)
        item["updated"] = (
            fm.get("updated") or datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        )
        if item["updated"]:
            item["mtime"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        out.append(item)
    return out


def _snippet(body: str, q: str) -> str:
    idx = body.lower().find(q.lower())
    if idx < 0:
        return ""
    start = max(0, idx - 40)
    end = min(len(body), idx + len(q) + 60)
    return body[start:end].strip()


def op_search(query: str) -> list[dict]:
    q = query.lower()
    hits = []
    for slug, fm, body, mtime in index_rows(store_root()):
        title = (fm.get("title") or slug).lower()
        summary = (fm.get("summary") or "").lower()
        if q in title or q in summary or q in body.lower():
            item = _page_meta(slug, fm, body)
            item["snippet"] = _snippet(body, q)
            item["mtime"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            hits.append(item)
    return hits


def _ensure_readable(slug: str):
    path = find_slug(store_root(), slug)
    if not path.exists():
        return None
    return path


def op_set(slug: str, content: str) -> dict:
    root = store_root()
    path = find_slug(root, slug)
    now = now_iso()
    if not path.exists():  # auto-create on set — cheap create is core
        topics_dir(root).mkdir(parents=True, exist_ok=True)
        fm = {"title": slug, "tags": [], "summary": "",
              "created": now, "updated": now, "parent": "", "children": []}
        path.write_text(render_page(fm, content), encoding="utf-8")
        return {"ok": True, "created": True, "slug": slug}
    fm, _old = read_page(path)
    _bump(path, fm, content)
    return {"ok": True, "created": False, "slug": slug}


def op_summarize(slug: str, summary: str) -> dict:
    path = _ensure_readable(slug)
    if not path:
        return {"ok": False, "slug": slug, "error": f"no page '{slug}'"}
    fm, body = read_page(path)
    fm["summary"] = summary
    _bump(path, fm, body)
    return {"ok": True, "slug": slug}


def op_tag(slug: str, tags) -> dict:
    path = _ensure_readable(slug)
    if not path:
        return {"ok": False, "slug": slug, "error": f"no page '{slug}'"}
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    fm, body = read_page(path)
    fm["tags"] = list(tags)
    _bump(path, fm, body)
    return {"ok": True, "slug": slug, "tags": list(tags)}


def op_archive(slug: str) -> dict:
    """Remove slug from the active list; re-parent its children one level up.

    The page's own frontmatter (parent/children) is preserved in the archive so
    `restore` can reverse the move. Children stay active and move up to the
    archived page's parent (or to top-level if there was none).
    """
    root = store_root()
    src = _ensure_readable(slug)
    if not src:
        return {"ok": False, "slug": slug, "error": f"no page '{slug}'"}
    fm, body = read_page(src)
    parent = _parent(fm)
    children = _children(fm)
    touched = {slug}

    # 1. drop slug from its parent's children
    if parent:
        pr = _read(root, parent)
        if pr:
            ppath, pfm, pbody = pr
            pc = [x for x in _children(pfm) if x != slug]
            pfm["children"] = pc
            _bump(ppath, pfm, pbody)
            touched.add(parent)

    # 2. re-parent each child one level up
    for child in children:
        cr = _read(root, child)
        if cr is None:
            continue
        cpath, cfm, cbody = cr
        cfm["parent"] = parent or ""
        _bump(cpath, cfm, cbody)
        touched.add(child)
        if parent:
            pr = _read(root, parent)
            if pr:
                ppath, pfm, pbody = pr
                pc = _children(pfm)
                if child not in pc:
                    pc.append(child)
                    pfm["children"] = pc
                    _bump(ppath, pfm, pbody)

    # 3. move to cabinet (frontmatter intact so restore can re-link)
    dst = cabinet_dir(root)
    dst.mkdir(parents=True, exist_ok=True)
    target = dst / src.name
    src.replace(target)

    _refresh_siblings(root, touched | set(children))
    return {"ok": True, "slug": slug, "path": str(target), "reparented": children}


def op_restore(slug: str) -> dict:
    """Move slug back from cabinet to active and re-link parent/children."""
    root = store_root()
    src = cabinet_dir(root) / (slug + ".md")
    if not src.exists():
        return {"ok": False, "slug": slug, "error": f"no archived page '{slug}'"}

    fm, body = read_page(src)
    parent = _parent(fm)
    children = _children(fm)
    touched = {slug}

    td = topics_dir(root)
    td.mkdir(parents=True, exist_ok=True)
    target = td / (slug + ".md")
    src.replace(target)

    # 1. re-link under its original parent (if that parent is still active)
    if parent and _read(root, parent):
        pr = _read(root, parent)
        ppath, pfm, pbody = pr
        pc = _children(pfm)
        if slug not in pc:
            pc.append(slug)
            pfm["children"] = pc
            _bump(ppath, pfm, pbody)
        touched.add(parent)

    # 2. re-attach children that are still active
    for child in children:
        cr = _read(root, child)
        if cr is None:
            continue
        cpath, cfm, cbody = cr
        cparent = _parent(cfm)
        if cparent and cparent != slug:
            pr = _read(root, cparent)
            if pr:
                ppath, pfm, pbody = pr
                pc = [x for x in _children(pfm) if x != child]
                pfm["children"] = pc
                _bump(ppath, pfm, pbody)
                touched.add(cparent)
        cfm["parent"] = slug
        _bump(cpath, cfm, cbody)
        sr = _read(root, slug)
        if sr:
            spath, sfm, sbody = sr
            sc = _children(sfm)
            if child not in sc:
                sc.append(child)
                sfm["children"] = sc
                _bump(spath, sfm, sbody)
        touched.add(child)

    _refresh_siblings(root, touched | set(children))
    return {"ok": True, "slug": slug, "path": str(target), "restored": children}


def op_archived() -> list[dict]:
    """List cabinet contents (never part of the default `list`)."""
    root = store_root()
    cd = cabinet_dir(root)
    out = []
    if cd.exists():
        for f in sorted(cd.glob("*.md")):
            try:
                fm, _body = read_page(f)
            except Exception:
                continue
            out.append({"slug": f.stem, "title": fm.get("title") or f.stem})
    return out


def op_touch(slug: str) -> dict:
    """(Re)create an empty page or bump mtime of an existing one."""
    root = store_root()
    path = find_slug(root, slug)
    now = now_iso()
    if path.exists():
        fm, body = read_page(path)
        fm["updated"] = now
        path.write_text(render_page(fm, body), encoding="utf-8")
        return {"ok": True, "created": False, "slug": slug}
    topics_dir(root).mkdir(parents=True, exist_ok=True)
    fm = {"title": slug, "tags": [], "summary": "",
          "created": now, "updated": now, "parent": "", "children": []}
    path.write_text(render_page(fm, ""), encoding="utf-8")
    return {"ok": True, "created": True, "slug": slug}


def op_info() -> dict:
    root = store_root()
    return {
        "store": str(root),
        "pages": len(index_rows(root)),
        "archived": len(op_archived()),
        "scratch_home": os.environ.get("SCRATCH_HOME", ""),
        "scratch_dir": os.environ.get("SCRATCH_DIR", ""),
    }