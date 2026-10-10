"""Export only bank input documents/schema, never benchmark scripts or oracles."""
import argparse
import json
from pathlib import Path
import re

from .freeze import verify_holdout_manifest
from .generate import ROOT


def export_profile(manifest_path, profile_id, output_dir, workspace_root=None):
    from builder.safety import read_json
    manifest = read_json(Path(manifest_path))
    if manifest.get("kind") != "offline": raise ValueError("only synthetic offline bank inputs may be exported")
    bank = next((p for p in manifest["profiles"] if p["profile_id"] == profile_id), None)
    if bank is None: raise ValueError("profile is absent or still reserved until candidate freeze")
    held_out_profile = any(row.get("split") == "holdout" and row.get("profile_id") == profile_id
                           for row in manifest.get("cases", []))
    if held_out_profile or profile_id.startswith("holdout"):
        verify_holdout_manifest(manifest)
    from .input_api import validate_input_api
    validation = validate_input_api(bank["openapi"])
    from .paths import output_path, PROJECT
    output = output_path(output_dir, workspace_root or PROJECT)
    if output == ROOT or ROOT in output.parents:
        raise ValueError("compiler inputs must be exported outside the evaluator directory")
    pending = []
    for name, text in bank["policy_documents"].items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name): raise ValueError("unsafe source document name")
        destination = output / (name + ".md")
        pending.append((destination, text))
    api = output / "bank.openapi.json"
    pending.append((api, json.dumps(bank["openapi"], indent=2) + "\n"))
    if any(path.exists() for path, text in pending):
        raise ValueError("refusing to overwrite existing compiler input")
    output.mkdir(parents=True, exist_ok=True)
    for destination, text in pending:
        with destination.open("x") as handle:
            handle.write(text)
    briefs = [str(path) for path, text in pending[:-1]]
    from builder.safety import digest
    return {"profile_id": profile_id, "brief_paths": briefs, "tool_api_path": str(api),
            "offline_only": True, "oracles_exported": False,
            "openapi_sha256": digest(bank["openapi"]), "api_version": bank["openapi"].get("info", {}).get("version"),
            "input_contract_checks": validation}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=str(ROOT / "fixtures/offline.json"))
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    print(json.dumps(export_profile(args.cases, args.profile, args.out), indent=2))


if __name__ == "__main__": main()
