"""Valida diagnóstico real em casos sintéticos e sela a coleção antes/depois."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def snapshot(container: str) -> dict:
    code = """
from app import sdk
import hashlib, json
c=sdk.qdrant_client(); records=[]; offset=None
while True:
    items,offset=c.scroll('dfe_kb',offset=offset,limit=128,with_payload=True,with_vectors=True)
    records.extend(item.model_dump(mode='json') for item in items)
    if offset is None: break
encoded=json.dumps(sorted(records,key=lambda p:str(p['id'])),sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'count':len(records),'sha256':hashlib.sha256(encoded).hexdigest()}))
"""
    result = subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def main(env_file: Path, report: Path) -> None:
    values = dotenv_values(env_file)
    container = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "packages/dfe/compose.existing.yaml"),
            "ps",
            "--quiet",
            "dfe",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert container, "DF-e existente não está executando"
    before = snapshot(container)
    cases = [
        json.loads(line)
        for line in (ROOT / "packages/dfe/data/eval/cases_v1.jsonl").read_text().splitlines()
    ]
    selected = [
        next(c for c in cases if c["case_id"] == "UC01-H1-01"),
        next(c for c in cases if c["split"] == "dev" and "G-204" in c["expected_finding_ids"]),
    ]
    results = []
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for case in selected:
            assert case["synthetic"] and case["reviewed"] and case["split"] == "dev"
            payload = {key: case[key] for key in ("description", "payload", "domain_context")}
            payload["locale"] = "pt-BR"
            request = urllib.request.Request(
                f"http://127.0.0.1:{values.get('DFE_PORT', '8002')}/diagnose",
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": values["DFE_API_KEY"],
                    "X-Core-Contract-Version": "1.0",
                },
            )
            with client.open(request, timeout=220) as response:
                body = json.load(response)
            findings = {item["id"]: item for item in body["findings"]}
            refs = findings.get("F-kb", {}).get("evidence_refs", [])
            evidence = {item["source_id"]: item for item in body["evidence"]}
            assert set(case["expected_finding_ids"]) | {"F-kb"} <= findings.keys()
            assert refs and set(refs) <= evidence.keys()
            assert all(
                evidence[ref]["trust_level"] in ("rule_reference", "retrieved_document")
                for ref in refs
            )
            assert body["llm_provider_used"] == "ollama"
            assert body["prompt_version"] == "dfe-1.2.0"
            assert body["cause_status"] != "confirmed"
            assert "nao esta entre os documentos recuperados" not in body["probable_root_cause"]
            results.append(
                {
                    "case_id": case["case_id"],
                    "findings": list(findings),
                    "references": refs,
                    "provider": body["llm_provider_used"],
                    "model": body["llm_model"],
                    "prompt_version": body["prompt_version"],
                    "prompt_digest": body["prompt_digest"],
                    "cause_status": body["cause_status"],
                    "matched_source": body["matched_source"],
                }
            )
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    finally:
        after = snapshot(container)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {"before": before, "after": after, "cases": results}, ensure_ascii=False, indent=2
            )
            + "\n"
        )
        assert before == after, "A coleção fiscal mudou durante a validação"
    print("Coleção preservada:", before["count"], before["sha256"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / "packages/dfe/.env")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "packages/dfe/.runtime/validation.json"
    )
    args = parser.parse_args()
    main(args.env_file, args.report)
