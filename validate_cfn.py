#!/usr/bin/env python3
"""
validate_cfn.py — CloudFormation Template Validation Skill

Validates a single CloudFormation YAML template through three sequential gates:
  Gate 1: YAML syntax (yamllint)
  Gate 2: CloudFormation schema (cfn-lint)
  Gate 3: Live AWS deployment (boto3 CloudFormation)

Output: JSON written to stdout (and optionally a file) readable by other skills.

Usage:
  python validate_cfn.py --template path/to/template.yaml
  python validate_cfn.py --template path/to/template.yaml --skip-deploy
  python validate_cfn.py --template path/to/template.yaml --output result.json
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
import datetime

import boto3
import yaml
from botocore.exceptions import ClientError
from yamllint import linter
from yamllint.config import YamlLintConfig


# ──────────────────────────────────────────────
# Gate 1 — YAML Syntax Validation
# ──────────────────────────────────────────────

def validate_yaml(template_path: str) -> dict:
    """
    Validates raw YAML syntax using yamllint.
    Returns a gate result dict.
    """
    yaml_config = YamlLintConfig("""
        extends: default
        rules:
            document-start: disable
            line-length: disable
            trailing-spaces: disable
            new-line-at-end-of-file: disable
            indentation:
                spaces: consistent
                indent-sequences: consistent
            truthy:
                allowed-values: ['true', 'false', 'yes', 'no']
    """)

    try:
        with open(template_path, "r") as f:
            content = f.read()

        problems = list(linter.run(content, yaml_config))
        errors = [
            {"line": p.line, "column": p.column, "message": p.desc}
            for p in problems
            if p.level == "error"
        ]

        return {
            "gate": "yaml_validation",
            "passed": len(errors) == 0,
            "error_count": len(errors),
            "errors": errors,
        }

    except FileNotFoundError:
        return {
            "gate": "yaml_validation",
            "passed": False,
            "error_count": 1,
            "errors": [{"line": None, "column": None, "message": f"File not found: {template_path}"}],
        }
    except Exception as e:
        return {
            "gate": "yaml_validation",
            "passed": False,
            "error_count": 1,
            "errors": [{"line": None, "column": None, "message": str(e)}],
        }


# ──────────────────────────────────────────────
# Gate 2 — cfn-lint Schema Validation
# ──────────────────────────────────────────────

def validate_cfn_lint(template_path: str) -> dict:
    """
    Validates CloudFormation resource schema and rules using cfn-lint.
    Returns a gate result dict.
    """
    try:
        result = subprocess.run(
            ["cfn-lint", "-f", "json", template_path],
            capture_output=True,
            text=True,
        )

        raw_errors = json.loads(result.stdout) if result.stdout.strip() else []

        severity_map = {"Error": "error", "Warning": "warning", "Informational": "informational"}
        errors = []
        for e in raw_errors:
            path = e.get("Location", {}).get("Path", [])
            errors.append({
                "severity": severity_map.get(e.get("Level"), e.get("Level", "unknown")),
                "resource": path[1] if len(path) > 1 else None,
                "message": e.get("Message", ""),
                "rule_id": e.get("Rule", {}).get("Id", ""),
                "rule_description": e.get("Rule", {}).get("Description", ""),
                "documentation": e.get("Rule", {}).get("Source", ""),
                "line": e.get("Location", {}).get("Start", {}).get("LineNumber"),
            })

        hard_errors = [e for e in errors if e["severity"] == "error"]
        warnings = [e for e in errors if e["severity"] == "warning"]

        return {
            "gate": "cfn_lint",
            "passed": len(hard_errors) == 0,
            "error_count": len(hard_errors),
            "warning_count": len(warnings),
            "errors": hard_errors,
            "warnings": warnings,
        }

    except FileNotFoundError:
        return {
            "gate": "cfn_lint",
            "passed": False,
            "error_count": 1,
            "warning_count": 0,
            "errors": [{"severity": "error", "resource": None, "message": "cfn-lint not found. Install with: pip install cfn-lint", "rule_id": "", "rule_description": "", "documentation": "", "line": None}],
            "warnings": [],
        }
    except Exception as e:
        return {
            "gate": "cfn_lint",
            "passed": False,
            "error_count": 1,
            "warning_count": 0,
            "errors": [{"severity": "error", "resource": None, "message": str(e), "rule_id": "", "rule_description": "", "documentation": "", "line": None}],
            "warnings": [],
        }


# ──────────────────────────────────────────────
# Gate 3 — Live AWS Deployment Validation
# ──────────────────────────────────────────────

def validate_deployment(template_path: str) -> dict:
    """
    Attempts a real CloudFormation stack deployment to validate deployability.
    Stack is automatically deleted after success or failure.
    Returns a gate result dict.
    """
    cfn = boto3.client("cloudformation")
    stack_name = f"cfn-validate-{uuid.uuid4().hex[:8]}"

    try:
        with open(template_path, "r") as f:
            template_body = f.read()

        cfn.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            OnFailure="DELETE",
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )

        stack_id = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]["StackId"]

        seen_event_ids = set()
        failed_resources = []
        completed_resources = []
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=2)

        while True:
            # Poll events
            events = cfn.describe_stack_events(StackName=stack_id)["StackEvents"]
            for event in sorted(events, key=lambda x: x["Timestamp"]):
                eid = event["EventId"]
                if eid in seen_event_ids or event["Timestamp"] <= cutoff:
                    continue
                seen_event_ids.add(eid)

                rid = event["LogicalResourceId"]
                status = event["ResourceStatus"]
                reason = event.get("ResourceStatusReason", "")

                if status == "CREATE_FAILED":
                    failed_resources.append({"resource": rid, "reason": reason})
                elif status == "CREATE_COMPLETE" and rid not in completed_resources:
                    completed_resources.append(rid)

            # Check terminal stack status
            stack_status = cfn.describe_stacks(StackName=stack_id)["Stacks"][0]["StackStatus"]

            if stack_status == "CREATE_COMPLETE":
                cfn.delete_stack(StackName=stack_name)
                return {
                    "gate": "deployment",
                    "passed": True,
                    "stack_id": stack_id,
                    "failed_resources": [],
                    "completed_resources": completed_resources,
                    "error_message": None,
                }
            elif stack_status in ("CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "DELETE_COMPLETE"):
                return {
                    "gate": "deployment",
                    "passed": False,
                    "stack_id": stack_id,
                    "failed_resources": failed_resources,
                    "completed_resources": completed_resources,
                    "error_message": failed_resources[0]["reason"] if failed_resources else "Unknown deployment error",
                }

            time.sleep(3)

    except ClientError as e:
        return {
            "gate": "deployment",
            "passed": False,
            "stack_id": None,
            "failed_resources": [],
            "completed_resources": [],
            "error_message": str(e),
        }
    except Exception as e:
        return {
            "gate": "deployment",
            "passed": False,
            "stack_id": None,
            "failed_resources": [],
            "completed_resources": [],
            "error_message": f"Unexpected error: {str(e)}",
        }


# ──────────────────────────────────────────────
# Resource Inventory (no ground truth needed)
# ──────────────────────────────────────────────

def extract_resources(template_path: str) -> dict:
    """
    Parses the template and returns a summary of resource types present.
    Works standalone — no ground truth template required.
    """
    # Register CloudFormation intrinsic function tags so PyYAML doesn't choke
    class CfnLoader(yaml.SafeLoader):
        pass

    def cfn_tag_constructor(loader, node):
        if isinstance(node, yaml.ScalarNode):
            return node.value
        elif isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    for tag in ["!Ref", "!Sub", "!GetAtt", "!Join", "!Select", "!Split", "!If",
                "!Equals", "!FindInMap", "!GetAZs", "!Base64", "!Cidr",
                "!ImportValue", "!Not", "!And", "!Or", "!Transform", "!Condition"]:
        CfnLoader.add_constructor(tag, cfn_tag_constructor)

    try:
        with open(template_path, "r") as f:
            template = yaml.load(f, Loader=CfnLoader)

        resources = template.get("Resources", {})
        resource_types = [v["Type"] for v in resources.values() if "Type" in v]

        return {
            "total_resources": len(resource_types),
            "unique_resource_types": len(set(resource_types)),
            "resource_types": resource_types,
        }
    except Exception as e:
        return {
            "total_resources": 0,
            "unique_resource_types": 0,
            "resource_types": [],
            "parse_error": str(e),
        }


# ──────────────────────────────────────────────
# Main Orchestrator
# ──────────────────────────────────────────────

def validate(template_path: str, skip_deploy: bool = False) -> dict:
    """
    Runs all validation gates sequentially.
    Stops at the first failed gate (downstream gates are skipped).

    Returns a structured dict with:
      - overall_passed (bool)
      - furthest_gate_reached (str)
      - gates (dict of gate results)
      - resources (dict of resource inventory)
      - template_path (str)
    """
    result = {
        "template_path": os.path.abspath(template_path),
        "overall_passed": False,
        "furthest_gate_reached": None,
        "skipped_deploy": skip_deploy,
        "resources": {},
        "gates": {
            "yaml_validation": None,
            "cfn_lint": None,
            "deployment": None,
        },
    }

    # Always extract resource inventory (best-effort, even if YAML is broken)
    result["resources"] = extract_resources(template_path)

    # Gate 1
    yaml_result = validate_yaml(template_path)
    result["gates"]["yaml_validation"] = yaml_result
    result["furthest_gate_reached"] = "yaml_validation"
    if not yaml_result["passed"]:
        return result

    # Gate 2
    lint_result = validate_cfn_lint(template_path)
    result["gates"]["cfn_lint"] = lint_result
    result["furthest_gate_reached"] = "cfn_lint"
    if not lint_result["passed"]:
        return result

    # Gate 3
    if skip_deploy:
        result["gates"]["deployment"] = {
            "gate": "deployment",
            "passed": None,
            "skipped": True,
            "stack_id": None,
            "failed_resources": [],
            "completed_resources": [],
            "error_message": None,
        }
        result["overall_passed"] = True   # passed gates 1 and 2
        result["furthest_gate_reached"] = "deployment_skipped"
        return result

    deploy_result = validate_deployment(template_path)
    result["gates"]["deployment"] = deploy_result
    result["furthest_gate_reached"] = "deployment"
    result["overall_passed"] = deploy_result["passed"]

    return result


# ──────────────────────────────────────────────
# CLI Entry Point
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validate a CloudFormation YAML template through YAML, cfn-lint, and AWS deployment gates."
    )
    parser.add_argument("--template", required=True, help="Path to the CloudFormation YAML template")
    parser.add_argument("--skip-deploy", action="store_true", help="Skip the live AWS deployment gate")
    parser.add_argument("--output", default=None, help="Optional path to write JSON output file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    result = validate(args.template, skip_deploy=args.skip_deploy)

    indent = 2 if args.pretty else None
    output_json = json.dumps(result, indent=indent, default=str)

    # Always print to stdout for piping to other skills
    print(output_json)

    # Optionally write to file
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"\n[validate_cfn] Result written to {args.output}", file=sys.stderr)

    # Exit code reflects overall result
    sys.exit(0 if result["overall_passed"] else 1)


if __name__ == "__main__":
    main()
