"""Export only bank input documents/schema, never benchmark scripts or oracles."""
import argparse
import json
from pathlib import Path
import re

from .freeze import verify_freeze
from .generate import ROOT


def export_profile(manifest_path, profile_id, output_dir, workspace_root=None):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get("kind") != "offline": raise ValueError("only synthetic offline bank inputs may be exported")
    bank = next((p for p in manifest["profiles"] if p["profile_id"] == profile_id), None)
    if bank is None: raise ValueError("profile is absent or still reserved until candidate freeze")
    if profile_id.startswith("holdout"):
        if not manifest.get("freeze"): raise ValueError("holdout requires a verified freeze")
        verify_freeze(manifest["freeze"]["record"])
    from .paths import output_path, PROJECT
    output = output_path(output_dir, workspace_root or PROJECT)
    if output == ROOT or ROOT in output.parents:
        raise ValueError("compiler inputs must be exported outside the evaluator directory")
    output.mkdir(parents=True, exist_ok=True)
    briefs = []
    for name, text in bank["policy_documents"].items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name): raise ValueError("unsafe source document name")
        destination = output / (name + ".md")
        if destination.exists(): raise ValueError("refusing to overwrite existing compiler input")
        destination.write_text(text)
        briefs.append(str(destination))
    api = output / "bank.openapi.json"
    if api.exists(): raise ValueError("refusing to overwrite existing OpenAPI")
    api.write_text(json.dumps(bank["openapi"], indent=2) + "\n")
    return {"profile_id": profile_id, "brief_paths": briefs, "tool_api_path": str(api),
            "offline_only": True, "oracles_exported": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=str(ROOT / "fixtures/offline.json"))
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    print(json.dumps(export_profile(args.cases, args.profile, args.out), indent=2))


if __name__ == "__main__": main()
