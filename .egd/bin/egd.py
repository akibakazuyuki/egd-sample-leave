#!/usr/bin/env python3
"""EGD Lite CLI — Example-Gated Development toolkit.

Agent-independent core: contract lock, lock verification, change-boundary
check, acceptance run, acceptance report, metrics, and an optional guard hook
for agents that support pre-tool hooks.

Standard library only. Python 3.8+.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "0.1.0"
EGD_DIR = ".egd"
TEMPLATES = Path(__file__).resolve().parent / "templates"
EX_ID_RE = re.compile(r"(?<![A-Za-z0-9])EX[-_](\d{3,})(?![0-9])")


def find_ex_ids(text: str) -> set:
    """Example ids in text. `EX_001` (test-function style) counts as `EX-001`."""
    return {f"EX-{n}" for n in EX_ID_RE.findall(text)}
CYCLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# Used by `egd new` in projects where only .egd/bin/egd.py is installed.
CONTRACT_TEMPLATE = """\
# EGD cycle contract (Example Contract Lite). Human approves this at Gate 1.
# After `egd lock`, the implementing agent cannot change it.
id: C-000
outcome: ここに、この開発で達成する成果を1文で書く
risk: L2               # L1 低 / L2 通常 / L3 高 / L4 致命的。AIが理由つきで提案する
risk_reason: ""
examples:
  - id: EX-001
    given: 前提条件
    when: 操作
    then: 期待結果
rules: []              # 具体例で表しきれない業務ルール
invariants: []         # 常に成り立つ条件（L3以上は必須）
open_questions: []     # Gate 1で人間が答える。{q, options, recommend, impact}
overrides: {}          # project.yaml を変えるときだけ書く
"""


class EgdError(Exception):
    pass


# ---------------------------------------------------------------- utilities

def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def root_dir(start: str | os.PathLike | None = None) -> Path:
    """Return the project root: the nearest ancestor holding .egd/config.json."""
    p = Path(start or os.getcwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / EGD_DIR / "config.json").is_file():
            return cand
    return p


def load_config(root: Path, config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else root / EGD_DIR / "config.json"
    if not path.is_file():
        raise EgdError(f"config not found: {path} (run `egd init` first)")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("acceptance_dir", "tests/acceptance")
    cfg.setdefault("acceptance_cmd", "")
    cfg.setdefault("regression_cmd", "")
    cfg.setdefault("change_boundary", ["src/**"])
    cfg.setdefault("protected", [])
    return cfg


def cycle_dir(root: Path, cycle: str) -> Path:
    if not CYCLE_ID.match(cycle):
        raise EgdError(f"invalid cycle id: {cycle!r}")
    return root / EGD_DIR / "cycles" / cycle


def rel(root: Path, p: Path) -> str:
    return p.resolve().relative_to(root.resolve()).as_posix()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def match_any(path: str, patterns) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def read_json(path: Path, default):
    if not path.is_file():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def git(root: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise EgdError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


# ------------------------------------------------------------ contract parse

def parse_examples(text: str) -> list[dict]:
    """Parse the `examples:` list of a contract.yaml written in the EGD format.

    Supports one level of `- id: EX-001` items with scalar keys
    (given / when / then / note). Not a general YAML parser on purpose:
    the contract format is fixed so humans can read it and tools can hash it.
    """
    examples: list[dict] = []
    in_examples = False
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if " #" in raw else raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0:
            in_examples = stripped.startswith("examples:")
            cur = None
            continue
        if not in_examples:
            continue
        m = re.match(r"-\s*(\w+):\s*(.*)$", stripped)
        if m:
            cur = {m.group(1): _unquote(m.group(2))}
            examples.append(cur)
            continue
        m = re.match(r"(\w+):\s*(.*)$", stripped)
        if m and cur is not None:
            cur[m.group(1)] = _unquote(m.group(2))
    return [e for e in examples if "id" in e]


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def contract_examples(root: Path, cycle: str) -> list[dict]:
    path = cycle_dir(root, cycle) / "contract.yaml"
    if not path.is_file():
        raise EgdError(f"contract not found: {rel(root, path)}")
    return parse_examples(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------- protected files

def cycle_protected_paths(root: Path, cfg: dict, cycle: str) -> list[str]:
    """Files whose content is fixed by the lock of this cycle."""
    files = [rel(root, cycle_dir(root, cycle) / "contract.yaml")]
    acc = root / cfg["acceptance_dir"] / cycle
    if acc.is_dir():
        files += sorted(rel(root, p) for p in acc.rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts)
    for pattern in cfg["protected"]:
        for p in sorted(root.glob(pattern)):
            if p.is_file() and "__pycache__" not in p.parts:
                files.append(rel(root, p))
    return sorted(set(files))


def locked_cycles(root: Path) -> list[str]:
    base = root / EGD_DIR / "cycles"
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if (d / "contract.lock").is_file())


def is_protected(root: Path, cfg: dict, path: str) -> str | None:
    """Return a reason string if `path` (relative, posix) is protected now."""
    if match_any(path, cfg["protected"]):
        return "project-level protected file"
    acc = cfg["acceptance_dir"].rstrip("/")
    for cycle in locked_cycles(root):
        cdir = f"{EGD_DIR}/cycles/{cycle}/"
        if path in (cdir + "contract.yaml", cdir + "contract.lock"):
            return f"locked contract of cycle {cycle}"
        if path.startswith(f"{acc}/{cycle}/"):
            return f"locked acceptance test of cycle {cycle}"
    return None


# --------------------------------------------------------------- coverage

def coverage(root: Path, cfg: dict, cycle: str) -> dict:
    """Map each example id to the acceptance test files that mention it."""
    ids = [e["id"] for e in contract_examples(root, cycle)]
    acc = root / cfg["acceptance_dir"] / cycle
    found: dict[str, list[str]] = {i: [] for i in ids}
    stray: set[str] = set()
    if acc.is_dir():
        for p in sorted(acc.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for m in find_ex_ids(text):
                if m in found:
                    found[m].append(rel(root, p))
                else:
                    stray.add(m)
    missing = [i for i, fs in found.items() if not fs]
    return {"examples": ids, "map": found, "missing": missing, "unknown_ids": sorted(stray)}


# ------------------------------------------------------------ junit results

def parse_junit(path: Path) -> dict[str, str]:
    """Return {EX-id: 'pass'|'fail'|'skip'} from a JUnit XML file.

    A test case belongs to an example when its name or classname contains the
    example id. Several test cases per example: any failure wins, then skip.
    """
    if not path.is_file():
        return {}
    tree = ET.parse(path)
    results: dict[str, str] = {}
    rank = {"pass": 0, "skip": 1, "fail": 2}
    for tc in tree.iter("testcase"):
        label = f"{tc.get('classname', '')} {tc.get('name', '')}"
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "fail"
        elif tc.find("skipped") is not None:
            status = "skip"
        else:
            status = "pass"
        for ex in find_ex_ids(label):
            prev = results.get(ex)
            if prev is None or rank[status] > rank[prev]:
                results[ex] = status
    return results


def run_cmd(root: Path, template: str, cycle: str, junit: Path) -> int:
    if not template:
        raise EgdError("command not configured in .egd/config.json")
    junit.parent.mkdir(parents=True, exist_ok=True)
    if junit.exists():
        junit.unlink()
    cmd = template.format(cycle=cycle, junit=str(junit))
    print(f"$ {cmd}", flush=True)
    return subprocess.run(cmd, shell=True, cwd=root).returncode


# ------------------------------------------------------------------ metrics

def record_event(root: Path, cycle: str, name: str, **extra) -> None:
    path = cycle_dir(root, cycle) / "metrics.json"
    data = read_json(path, {"cycle": cycle, "events": []})
    data["events"].append({"event": name, "at": now(), **extra})
    write_json(path, data)


def _duration(events, start, end) -> float | None:
    s = [e["at"] for e in events if e["event"] == start]
    t = [e["at"] for e in events if e["event"] == end]
    if not s or not t:
        return None
    a = _dt.datetime.fromisoformat(s[-1])
    b = _dt.datetime.fromisoformat(t[-1])
    return round((b - a).total_seconds() / 60, 1)


# ----------------------------------------------------------------- commands

def cmd_init(args) -> int:
    root = Path(args.target).resolve()
    adapters = set(args.adapter or [])
    created = []

    def put(src: Path, dst: Path, overwrite=False):
        if dst.exists() and not overwrite:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        created.append(rel(root, dst))

    put(TEMPLATES / "config.json", root / EGD_DIR / "config.json")
    put(TEMPLATES / "project.yaml", root / EGD_DIR / "project.yaml")
    put(Path(__file__).resolve(), root / EGD_DIR / "bin" / "egd.py", overwrite=True)
    put(TEMPLATES / "egd-verify.yml", root / ".github" / "workflows" / "egd-verify.yml")

    gi = root / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [x for x in (".egd/out/", ".egd/guard.log", "__pycache__/") if x not in lines]
    if add:
        gi.write_text("\n".join(lines + add) + "\n", encoding="utf-8")
        created.append(".gitignore")

    agents_md = root / "AGENTS.md"
    rules = (TEMPLATES / "AGENTS.egd.md").read_text(encoding="utf-8")
    marker = "<!-- egd:rules -->"
    if agents_md.exists():
        text = agents_md.read_text(encoding="utf-8")
        if marker not in text:
            agents_md.write_text(text.rstrip() + "\n\n" + rules, encoding="utf-8")
            created.append("AGENTS.md (appended)")
    else:
        agents_md.write_text(rules, encoding="utf-8")
        created.append("AGENTS.md")

    if "claude-code" in adapters:
        claude_md = root / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text("@AGENTS.md\n", encoding="utf-8")
            created.append("CLAUDE.md")
        settings = root / ".claude" / "settings.json"
        tmpl = json.loads((TEMPLATES / "claude" / "settings.json").read_text(encoding="utf-8"))
        cur = read_json(settings, {})
        for key in ("hooks", "permissions"):
            cur.setdefault(key, {})
            for sub, val in tmpl[key].items():
                if isinstance(val, list):
                    existing = cur[key].setdefault(sub, [])
                    for item in val:
                        if item not in existing:
                            existing.append(item)
                else:
                    cur[key].setdefault(sub, val)
        write_json(settings, cur)
        created.append(".claude/settings.json")
        for src in sorted((TEMPLATES / "claude" / "commands").glob("*.md")):
            put(src, root / ".claude" / "commands" / "egd" / src.name)
        put(TEMPLATES / "claude" / "egd-verifier.md", root / ".claude" / "agents" / "egd-verifier.md")

    print("EGD initialized. Created or updated:")
    for c in created:
        print(f"  {c}")
    print("Next: edit .egd/config.json (test commands, change boundary), then `egd new <ID>`.")
    return 0


def cmd_new(args) -> int:
    root = root_dir()
    cfg = load_config(root)
    d = cycle_dir(root, args.cycle)
    if (d / "contract.yaml").exists():
        raise EgdError(f"cycle {args.cycle} already exists")
    tmpl = TEMPLATES / "contract.yaml"
    text = tmpl.read_text(encoding="utf-8") if tmpl.is_file() else CONTRACT_TEMPLATE
    d.mkdir(parents=True, exist_ok=True)
    (d / "contract.yaml").write_text(text.replace("C-000", args.cycle), encoding="utf-8")
    write_json(d / "assumptions.json", [])
    (root / cfg["acceptance_dir"] / args.cycle).mkdir(parents=True, exist_ok=True)
    record_event(root, args.cycle, "cycle_started")
    print(f"Created {rel(root, d)}/contract.yaml")
    print(f"Work branch suggestion: egd/{args.cycle}")
    return 0


def cmd_coverage(args) -> int:
    root = root_dir()
    cfg = load_config(root)
    cov = coverage(root, cfg, args.cycle)
    for ex in cov["examples"]:
        files = ", ".join(cov["map"][ex]) or "MISSING"
        print(f"{ex}: {files}")
    if cov["unknown_ids"]:
        print("Tests mention ids not in the contract: " + ", ".join(cov["unknown_ids"]))
    if cov["missing"] or not cov["examples"]:
        print("NG: every example needs at least one acceptance test.")
        return 1
    print("OK: every example has an acceptance test.")
    return 0


def changed_examples(root: Path, cycle: str) -> list[str] | None:
    """Example ids that are new or changed against the committed contract.
    None when there is no committed contract to compare with."""
    path = rel(root, cycle_dir(root, cycle) / "contract.yaml")
    try:
        before = {e["id"]: e for e in parse_examples(git(root, "show", f"HEAD:{path}"))}
    except EgdError:
        return None
    return [e["id"] for e in contract_examples(root, cycle) if before.get(e["id"]) != e]


def red_check(root: Path, cfg: dict, cycle: str, only: list[str] | None = None) -> tuple[bool, dict]:
    """Acceptance tests must fail before implementation. On a re-seal, `only` limits
    the check to new or changed examples: the others are already implemented."""
    junit = root / EGD_DIR / "out" / f"{cycle}-red.xml"
    code = run_cmd(root, cfg["acceptance_cmd"], cycle, junit)
    results = parse_junit(junit)
    ids = [e["id"] for e in contract_examples(root, cycle)]
    if only is not None:
        ids = [i for i in ids if i in set(only)]
    passing = [i for i in ids if results.get(i) == "pass"]
    unseen = [i for i in ids if i not in results]
    ok = code != 0 and not passing and not unseen
    return ok, {"exit_code": code, "checked": ids, "passing_before_impl": passing, "not_run": unseen}


def cmd_red_check(args) -> int:
    root = root_dir()
    cfg = load_config(root)
    ok, info = red_check(root, cfg, args.cycle)
    if info["passing_before_impl"]:
        print("NG: these examples already pass before implementation "
              "(the test may check nothing): " + ", ".join(info["passing_before_impl"]))
    if info["not_run"]:
        print("NG: no test result for: " + ", ".join(info["not_run"]))
    print("OK: all acceptance tests fail before implementation." if ok else "Red check failed.")
    return 0 if ok else 1


def cmd_lock(args) -> int:
    root = root_dir()
    cfg = load_config(root)
    d = cycle_dir(root, args.cycle)
    cov = coverage(root, cfg, args.cycle)
    if cov["missing"] or not cov["examples"]:
        raise EgdError("coverage check failed; run `egd coverage` first")
    lock_path = d / "contract.lock"
    prev = read_json(lock_path, None)
    red = None
    if not args.skip_red:
        only = None
        promoted = []
        if prev and args.relock:
            only = changed_examples(root, args.cycle)
            if only == []:
                raise EgdError("re-seal has no new or changed examples")
            # An example that writes down an assumption the human approved at Gate 2
            # is already implemented, so it may pass. It must be linked explicitly.
            promoted = sorted({e for a in read_json(d / "assumptions.json", [])
                               if a.get("status") == "approved"
                               for e in a.get("promoted_to", [])} & set(only or []))
            only = [i for i in only if i not in promoted] if only is not None else None
        ok, red = red_check(root, cfg, args.cycle, only) if only != [] else (True, {"checked": []})
        red["promoted_from_assumptions"] = promoted
        if not ok:
            raise EgdError(f"red check failed: {red}")
    version = (prev["version"] + 1) if prev else 1
    if prev and not args.relock:
        raise EgdError("already sealed; re-sealing needs --reseal and must be done by a human")
    tag = f"egd/{args.cycle}/v{version}"
    if args.git and git(root, "tag", "--list", tag, check=False).strip():
        raise EgdError(f"tag {tag} already exists")
    files = {p: sha256_file(root / p) for p in cycle_protected_paths(root, cfg, args.cycle)}
    lock = {
        "cycle": args.cycle,
        "version": version,
        "locked_at": now(),
        "files": files,
        "examples": cov["examples"],
        "red_check": red,
        "history": (prev.get("history", []) + [{"version": prev["version"], "locked_at": prev["locked_at"]}]) if prev else [],
    }
    write_json(lock_path, lock)
    record_event(root, args.cycle, "gate1_locked", version=version)
    if args.git:
        paths = [rel(root, lock_path), *files.keys()]
        try:
            git(root, "add", "--", *paths)
            git(root, "commit", "-m", f"egd: seal acceptance criteria {args.cycle} v{version}")
        except EgdError:
            # Roll back so a failed commit does not leave a half-made lock.
            git(root, "reset", "-q", "--", *paths, check=False)
            if prev:
                write_json(lock_path, prev)
            else:
                lock_path.unlink()
            raise
        git(root, "tag", tag)
        branch = f"{SEAL_BRANCH}/{args.cycle}/v{version}"
        git(root, "branch", "-f", branch, "HEAD", check=False)
        print(f"Committed and tagged {tag}. Push with: git push origin {tag}\n"
              f"If tags cannot be pushed from here, push the seal branch instead: git push origin {branch}")
    else:
        print(f"Sealed {args.cycle} v{version}. Commit it, then: git tag {tag}")
    return 0


SEAL_BRANCH = "egd-seal"


def seal_refs(root: Path, cycle: str) -> dict:
    """Seal versions -> git ref. A seal is the tag egd/<ID>/v<N>, or, where tags
    cannot be pushed (some hosted agents only push branches), the branch
    egd-seal/<ID>/v<N>. If both exist for a version they must point to the same commit."""
    refs: dict = {}
    def ver(name):
        try:
            return int(name.rsplit("/v", 1)[1])
        except (IndexError, ValueError):
            return None
    out = git(root, "for-each-ref", "--format=%(refname)",
              f"refs/tags/egd/{cycle}/", f"refs/heads/{SEAL_BRANCH}/{cycle}/",
              f"refs/remotes/origin/{SEAL_BRANCH}/{cycle}/", check=False)
    for full in (l.strip() for l in out.splitlines()):
        v = ver(full)
        if v is None:
            continue
        short = full.split("/", 2)[2]  # tags/x -> x, heads/x -> x, remotes/origin/x -> origin/x
        sha = git(root, "rev-parse", f"{full}^{{commit}}").strip()
        if v in refs and refs[v][1] != sha:
            raise EgdError(f"seal v{v} of {cycle} points to different commits ({refs[v][0]}, {short})")
        if v not in refs or full.startswith("refs/tags/"):
            refs[v] = (short, sha)
    return {v: r for v, (r, _) in refs.items()}


def latest_tag(root: Path, cycle: str) -> str | None:
    refs = seal_refs(root, cycle)
    return refs[max(refs)] if refs else None


def check_lock(root: Path, cfg: dict, cycle: str, ref: str | None) -> dict:
    lock_rel = rel(root, cycle_dir(root, cycle) / "contract.lock")
    if ref:
        lock = json.loads(git(root, "show", f"{ref}:{lock_rel}"))
    else:
        lock = read_json(root / lock_rel, None)
        if lock is None:
            raise EgdError(f"no seal for cycle {cycle}")
    changed, missing = [], []
    for path, digest in lock["files"].items():
        p = root / path
        if not p.is_file():
            missing.append(path)
        elif sha256_file(p) != digest:
            changed.append(path)
    added = [p for p in cycle_protected_paths(root, cfg, cycle) if p not in lock["files"]]
    working_lock_changed = False
    if ref and (root / lock_rel).is_file():
        working_lock_changed = sha256_file(root / lock_rel) != sha256_bytes(
            subprocess.run(["git", "show", f"{ref}:{lock_rel}"], cwd=root,
                           capture_output=True).stdout)
    ok = not (changed or missing or added or working_lock_changed)
    return {"ok": ok, "version": lock["version"], "ref": ref, "changed": changed,
            "missing": missing, "added": added, "lock_file_changed": working_lock_changed}


def cmd_check_lock(args) -> int:
    root = root_dir()
    cfg = load_config(root, args.config)
    res = check_lock(root, cfg, args.cycle, args.ref)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


def boundary(root: Path, cfg: dict, cycle: str, base: str) -> dict:
    # With criss-cross history there can be several merge bases. A file counts as
    # changed by this branch only if it differs from every one of them.
    bases = git(root, "merge-base", "--all", base, "HEAD").split() or [base]
    files = None
    for b in bases:
        names = [f for f in git(root, "diff", "--name-only", b, "HEAD").splitlines() if f.strip()]
        files = names if files is None else [f for f in files if f in set(names)]
    cdir = f"{EGD_DIR}/cycles/{cycle}/"
    allowed_meta = {cdir + "assumptions.json", cdir + "metrics.json", cdir + "report.md",
                    cdir + "decisions.json", cdir + "verify.json"}
    lock_paths = set()
    try:
        lock = read_json(cycle_dir(root, cycle) / "contract.lock", {})
        lock_paths = set(lock.get("files", {})) | {cdir + "contract.lock"}
    except Exception:
        pass
    outside, protected = [], []
    for f in files:
        if f in allowed_meta or f in lock_paths or f.startswith(cdir + "evidence/"):
            # lock commit itself is part of Gate 1; integrity checked by check_lock
            continue
        if match_any(f, cfg["protected"]):
            protected.append(f)
        elif not match_any(f, cfg["change_boundary"]):
            outside.append(f)
    return {"ok": not outside and not protected, "changed": files,
            "outside_boundary": outside, "protected_touched": protected}


def cmd_boundary(args) -> int:
    root = root_dir()
    cfg = load_config(root, args.config)
    res = boundary(root, cfg, args.cycle, args.base)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


def verify(root: Path, cfg: dict, cycle: str, ref: str | None, base: str | None,
           restore: bool) -> dict:
    result: dict = {"cycle": cycle, "at": now(), "ref": ref}
    if ref:
        result["lock"] = check_lock(root, cfg, cycle, ref)
        if restore:
            lock = json.loads(git(root, "show", f"{ref}:{EGD_DIR}/cycles/{cycle}/contract.lock"))
            git(root, "checkout", ref, "--", *lock["files"].keys(),
                f"{EGD_DIR}/cycles/{cycle}/contract.lock")
            for extra in result["lock"]["added"]:
                (root / extra).unlink(missing_ok=True)
    else:
        result["lock"] = check_lock(root, cfg, cycle, None)
    result["boundary"] = boundary(root, cfg, cycle, base) if base else None
    junit = root / EGD_DIR / "out" / f"{cycle}-acceptance.xml"
    code = run_cmd(root, cfg["acceptance_cmd"], cycle, junit)
    per_ex = parse_junit(junit)
    ids = [e["id"] for e in contract_examples(root, cycle)]
    result["acceptance"] = {
        "exit_code": code,
        "results": {i: per_ex.get(i, "not_run") for i in ids},
    }
    result["acceptance"]["ok"] = code == 0 and all(v == "pass" for v in result["acceptance"]["results"].values())
    if cfg["regression_cmd"]:
        rj = root / EGD_DIR / "out" / f"{cycle}-regression.xml"
        rcode = run_cmd(root, cfg["regression_cmd"], cycle, rj)
        total = failed = 0
        if rj.is_file():
            for tc in ET.parse(rj).iter("testcase"):
                total += 1
                if tc.find("failure") is not None or tc.find("error") is not None:
                    failed += 1
        result["regression"] = {"exit_code": rcode, "total": total, "failed": failed, "ok": rcode == 0}
    else:
        result["regression"] = None
    parts = [result["lock"]["ok"], result["acceptance"]["ok"]]
    if result["boundary"]:
        parts.append(result["boundary"]["ok"])
    if result["regression"]:
        parts.append(result["regression"]["ok"])
    result["ok"] = all(parts)
    return result


def cmd_verify(args) -> int:
    root = root_dir()
    cfg = load_config(root, args.config)
    res = verify(root, cfg, args.cycle, args.ref, args.base, restore=False)
    write_json(cycle_dir(root, args.cycle) / "verify.json", res)
    record_event(root, args.cycle, "verified", ok=res["ok"])
    print(json.dumps({k: res[k] for k in ("ok", "lock", "acceptance")}, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


def cmd_ci(args) -> int:
    """Entry point for the CI judge. Runs with the base branch's egd.py and config."""
    root = Path(os.getcwd()).resolve()
    cfg = load_config(root, args.config)
    branch = args.branch
    if not branch.startswith("egd/"):
        print(f"branch {branch} is not an EGD cycle branch; nothing to verify")
        return 0
    cycle = branch.split("/", 2)[1]
    git(root, "fetch", "--tags", "--force", check=False)
    ref = latest_tag(root, cycle)
    if not ref:
        raise EgdError(f"no seal (tag egd/{cycle}/v* or branch {SEAL_BRANCH}/{cycle}/v*) found; Gate 1 not completed")
    res = verify(root, cfg, cycle, ref, args.base_sha, restore=True)
    res["tag_versions"] = [r for _, r in sorted(seal_refs(root, cycle).items())]
    out = root / EGD_DIR / "out" / f"{cycle}-verify.json"
    write_json(out, res)
    summary = render_summary(cycle, res)
    print(summary)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(summary + "\n")
    return 0 if res["ok"] else 1


def render_summary(cycle: str, res: dict) -> str:
    lock = res["lock"]
    acc = res["acceptance"]["results"]
    passed = sum(1 for v in acc.values() if v == "pass")
    lines = [f"## EGD verify: {cycle} — {'PASS' if res['ok'] else 'FAIL'}", ""]
    lines.append(f"- Seal {lock.get('ref') or 'working tree'} (v{lock['version']}): "
                 f"{'OK' if lock['ok'] else 'TAMPERED'}")
    for k in ("changed", "missing", "added"):
        if lock.get(k):
            lines.append(f"  - {k}: {', '.join(lock[k])}")
    if lock.get("lock_file_changed"):
        lines.append("  - lock file differs from the tag")
    if len(res.get("tag_versions", [])) > 1:
        lines.append(f"- Contract was re-locked: {', '.join(res['tag_versions'])} (human check required)")
    lines.append(f"- Examples: {passed}/{len(acc)} PASS")
    for ex, v in acc.items():
        if v != "pass":
            lines.append(f"  - {ex}: {v.upper()}")
    b = res.get("boundary")
    if b:
        lines.append(f"- Change boundary: {'OK' if b['ok'] else 'VIOLATION'}")
        for f in b["outside_boundary"]:
            lines.append(f"  - outside boundary: {f}")
        for f in b["protected_touched"]:
            lines.append(f"  - protected file changed: {f}")
    r = res.get("regression")
    if r:
        lines.append(f"- Regression: {r['total'] - r['failed']}/{r['total']} PASS")
    return "\n".join(lines)


def cmd_report(args) -> int:
    root = root_dir()
    load_config(root)
    d = cycle_dir(root, args.cycle)
    examples = contract_examples(root, args.cycle)
    res = read_json(d / "verify.json", None)
    if res is None:
        raise EgdError("no verify.json; run `egd verify` first")
    assumptions = read_json(d / "assumptions.json", [])
    metrics = read_json(d / "metrics.json", {"events": []})
    lock = read_json(d / "contract.lock", {})
    must = [a for a in assumptions if a.get("type") in ("C", "D") and a.get("status") != "approved"]
    acc = res["acceptance"]["results"]
    passed = sum(1 for v in acc.values() if v == "pass")
    title = args.title or args.cycle
    head = [f"# {args.cycle} {title} — {'受入判断待ち' if res['ok'] else '検証失敗'}", ""]
    head.append(f"受入基準 v{lock.get('version', '?')} · 検証 {res['at']} · "
                f"シール照合 {'OK' if res['lock']['ok'] else '不一致（改ざんの可能性）'}")
    if lock.get("history"):
        head.append(f"**注意：Gate 1以降に受入基準のシールが貼り直されています（v{lock['version']}）。変更内容を確認してください。**")
    head.append("")
    line = f"具体例 {passed}/{len(acc)} PASS"
    if res.get("regression"):
        r = res["regression"]
        line += f" · 回帰 {r['total'] - r['failed']}/{r['total']} PASS"
    if res.get("boundary"):
        line += " · 範囲外の変更 " + ("なし" if res["boundary"]["ok"] else "あり")
    head.append(line)
    head.append(f"要確認の仮定 {len(must)}件")
    head.append("")
    head.append("| ID | 前提 | 操作 | 期待 | 判定 |")
    head.append("| --- | --- | --- | --- | --- |")
    for e in examples:
        v = acc.get(e["id"], "not_run")
        head.append(f"| {e['id']} | {e.get('given', '')} | {e.get('when', '')} | "
                    f"{e.get('then', '')} | {v.upper()} |")
    head.append("")
    if must:
        head.append("**要確認の仮定**")
        head.append("")
        for a in must:
            head.append(f"- {a.get('id')}（Type {a.get('type')}、"
                        f"{'可逆' if a.get('reversible', True) else '不可逆'}）{a.get('decision', '')}")
        head.append("")
    head.append("判断：`egd decide " + args.cycle + " accept|reject|hold`")
    head.append("")
    detail = ["<details><summary>詳細</summary>", ""]
    other = [a for a in assumptions if a not in must]
    if other:
        detail.append("**その他の仮定**")
        detail += [f"- {a.get('id')}（Type {a.get('type')}）{a.get('decision', '')}" for a in other]
        detail.append("")
    b = res.get("boundary")
    if b:
        detail.append("**変更ファイル**")
        detail += [f"- {f}" for f in b["changed"]]
        detail.append("")
    ev = metrics.get("events", [])
    impl = _duration(ev, "gate1_locked", "verified")
    if impl is not None:
        detail.append(f"**所要時間**：シールから検証まで {impl} 分")
        detail.append("")
    evidence = d / "evidence"
    if evidence.is_dir():
        detail.append("**動作証拠**")
        detail += [f"- {rel(root, p)}" for p in sorted(evidence.rglob('*')) if p.is_file()]
        detail.append("")
    detail.append("</details>")
    text = "\n".join(head + detail) + "\n"
    (d / "report.md").write_text(text, encoding="utf-8")
    record_event(root, args.cycle, "report_generated")
    print(text)
    return 0


REJECT_TYPES = {
    "1": "implementation_failure",
    "2": "specification_gap",
    "3": "requirement_change",
    "4": "verification_failure",
    "5": "risk_discovery",
}


def cmd_decide(args) -> int:
    root = root_dir()
    d = cycle_dir(root, args.cycle)
    if args.decision == "reject" and args.type not in REJECT_TYPES:
        raise EgdError("reject needs --type 1..5 "
                       "(1 実装不備, 2 仕様の不足, 3 要求変更, 4 検証の誤り, 5 リスク発覚)")
    entry = {"decision": args.decision, "at": now(), "note": args.note or ""}
    if args.decision == "reject":
        entry["type"] = REJECT_TYPES[args.type]
    if args.review_minutes is not None:
        entry["review_minutes"] = args.review_minutes
    data = read_json(d / "decisions.json", [])
    data.append(entry)
    write_json(d / "decisions.json", data)
    record_event(root, args.cycle, f"gate2_{args.decision}", **{k: v for k, v in entry.items() if k != "at"})
    print(f"Recorded {args.decision} for {args.cycle}.")
    if args.decision == "accept":
        print("Merge to main is done by a human.")
    return 0


def cmd_event(args) -> int:
    root = root_dir()
    record_event(root, args.cycle, args.name)
    return 0


def cmd_status(args) -> int:
    root = root_dir()
    base = root / EGD_DIR / "cycles"
    if not base.is_dir():
        print("no cycles")
        return 0
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        lock = read_json(d / "contract.lock", None)
        ver = read_json(d / "verify.json", None)
        dec = read_json(d / "decisions.json", [])
        state = "draft"
        if lock:
            state = f"sealed v{lock['version']}"
        if ver:
            state += ", verify " + ("PASS" if ver["ok"] else "FAIL")
        if dec:
            state += ", " + dec[-1]["decision"].upper()
        print(f"{d.name}: {state}")
    return 0


# -------------------------------------------------------------------- guard

WRITE_HINTS = re.compile(
    r"(>>?|\btee\b|\bsed\s+-i|\bperl\s+-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bchmod\b|"
    r"\bgit\s+(checkout|restore|rm|mv)\b|\bln\b|\binstall\b|\bdd\b|write_text|open\()")
GIT_DANGER = [
    (re.compile(r"\bgit\s+push\b[^\n;&|]*\s(--force\b|-f\b|--force-with-lease\b)"), "force push"),
    (re.compile(r"\bgit\s+push\b[^\n;&|]*\b(origin\s+)?(HEAD:)?(refs/heads/)?main\b"), "push to main"),
    (re.compile(r"\bgit\s+push\b[^\n;&|]*--delete\b"), "remote delete"),
    (re.compile(r"\bgit\s+tag\s+(-d|-f|--delete|--force)\b"), "tag rewrite"),
    (re.compile(r"\bgit\s+(merge|rebase)\b[^\n;&|]*\bmain\b"), "merge involving main"),
]
RELOCK = re.compile(r"egd(\.py)?\s+(lock|seal)\b[^\n;&|]*--re(lock|seal)")


def guard_decision(root: Path, cfg: dict, payload: dict) -> str | None:
    """Return a refusal reason, or None to allow."""
    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input", {}) or {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        fp = tin.get("file_path") or tin.get("notebook_path") or ""
        if not fp:
            return None
        p = Path(fp)
        if not p.is_absolute():
            p = root / p
        try:
            r = rel(root, p)
        except ValueError:
            return None
        reason = is_protected(root, cfg, r)
        return f"{r} is protected ({reason})" if reason else None
    if tool == "Bash":
        cmd = tin.get("command", "")
        for pat, what in GIT_DANGER:
            if pat.search(cmd):
                return f"blocked git operation: {what}"
        if RELOCK.search(cmd):
            return "re-locking a contract must be done by a human"
        if WRITE_HINTS.search(cmd):
            candidates = set(re.findall(r"[\w./-]+", cmd))
            for c in candidates:
                c = c.lstrip("./")
                if not c:
                    continue
                reason = is_protected(root, cfg, c)
                if reason:
                    return f"command may modify {c} ({reason})"
                acc = cfg["acceptance_dir"].rstrip("/")
                for cycle in locked_cycles(root):
                    if c.rstrip("/") in (f"{acc}/{cycle}", f"{EGD_DIR}/cycles/{cycle}"):
                        return f"command may modify locked directory {c}"
    return None


def cmd_guard(args) -> int:
    """PreToolUse hook. Exit 2 blocks the call; stderr is shown to the agent."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    root = root_dir(payload.get("cwd") or None)
    try:
        cfg = load_config(root)
    except EgdError:
        return 0
    reason = guard_decision(root, cfg, payload)
    if reason:
        log = root / EGD_DIR / "guard.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now(), "tool": payload.get("tool_name"), "reason": reason},
                               ensure_ascii=False) + "\n")
        print(f"EGD guard: {reason}. Sealed acceptance criteria and protected files "
              f"cannot be changed by the implementing agent. If the acceptance criteria are wrong, "
              f"stop and ask the human (reject type 2 or 4).", file=sys.stderr)
        return 2
    return 0


# --------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="egd", description="EGD Lite toolkit")
    ap.add_argument("--version", action="version", version=VERSION)
    sp = ap.add_subparsers(dest="command", required=True)

    p = sp.add_parser("init", help="set up EGD in a project")
    p.add_argument("--target", default=".")
    p.add_argument("--adapter", action="append", choices=["claude-code"],
                   help="optional agent-specific extras; any agent works with AGENTS.md alone")
    p.set_defaults(func=cmd_init)

    p = sp.add_parser("new", help="start a cycle")
    p.add_argument("cycle")
    p.set_defaults(func=cmd_new)

    for name, func, help_ in (("coverage", cmd_coverage, "example-to-test mapping"),
                              ("red-check", cmd_red_check, "acceptance tests must fail before implementation")):
        p = sp.add_parser(name, help=help_)
        p.add_argument("cycle")
        p.set_defaults(func=func)

    p = sp.add_parser("seal", aliases=["lock"], help="Gate 1: seal the acceptance criteria (alias: lock)")
    p.add_argument("cycle")
    p.add_argument("--git", action="store_true", help="commit and tag")
    p.add_argument("--skip-red", action="store_true")
    p.add_argument("--reseal", "--relock", dest="relock", action="store_true",
                   help="human only: seal a new version")
    p.set_defaults(func=cmd_lock)

    p = sp.add_parser("check-seal", aliases=["check-lock"], help="check the seal (alias: check-lock)")
    p.add_argument("cycle")
    p.add_argument("--ref")
    p.add_argument("--config")
    p.set_defaults(func=cmd_check_lock)

    p = sp.add_parser("boundary")
    p.add_argument("cycle")
    p.add_argument("--base", required=True)
    p.add_argument("--config")
    p.set_defaults(func=cmd_boundary)

    p = sp.add_parser("verify", help="local independent verification")
    p.add_argument("cycle")
    p.add_argument("--ref")
    p.add_argument("--base")
    p.add_argument("--config")
    p.set_defaults(func=cmd_verify)

    p = sp.add_parser("ci", help="CI judge entry point")
    p.add_argument("--branch", required=True)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--config", required=True)
    p.set_defaults(func=cmd_ci)

    p = sp.add_parser("report", help="Gate 2: acceptance report")
    p.add_argument("cycle")
    p.add_argument("--title")
    p.set_defaults(func=cmd_report)

    p = sp.add_parser("decide", help="Gate 2: record the human decision")
    p.add_argument("cycle")
    p.add_argument("decision", choices=["accept", "reject", "hold"])
    p.add_argument("--type", help="reject type 1-5")
    p.add_argument("--note")
    p.add_argument("--review-minutes", type=float)
    p.set_defaults(func=cmd_decide)

    p = sp.add_parser("event", help="record a metrics event")
    p.add_argument("cycle")
    p.add_argument("name")
    p.set_defaults(func=cmd_event)

    p = sp.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sp.add_parser("guard", help="pre-tool hook (reads JSON on stdin)")
    p.set_defaults(func=cmd_guard)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except EgdError as e:
        print(f"egd: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
