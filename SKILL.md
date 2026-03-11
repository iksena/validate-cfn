---
name: validate_cfn
description: Validate a single CloudFormation YAML template through three sequential gates — YAML syntax (yamllint), schema correctness (cfn-lint), and live AWS deployment (boto3). Returns structured JSON output readable by other skills.
requires:
  binaries:
    - python3
    - cfn-lint
  env:
    - AWS_ACCESS_KEY_ID
    - AWS_SECRET_ACCESS_KEY
    - AWS_DEFAULT_REGION
os:
  - linux
  - macos
  - windows
---

# CloudFormation Validation Skill

Use this skill whenever the user wants to validate, check, lint, test deployability, or evaluate a CloudFormation YAML template.

## When to activate

- User says: "validate this template", "check my CloudFormation", "does this deploy?", "lint my template", "test my CFN"
- Another skill produces a `.yaml` template and needs a validation result before proceeding
- You need a structured JSON gate report for a template file

## Inputs

| Argument | Required | Description |
|---|---|---|
| `--template` | ✅ | Absolute or relative path to the `.yaml` template file |
| `--skip-deploy` | ❌ | Pass this flag to skip the live AWS deployment gate (faster, zero AWS cost) |
| `--output` | ❌ | Path to write the JSON result file (in addition to stdout) |
| `--pretty` | ❌ | Pretty-print the JSON output |

## How to invoke

Run the skill script directly with `python3`:

```bash
python3 ~/.openclaw/workspace/skills/validate-cfn/validate_cfn.py \
  --template <path_to_template.yaml> \
  [--skip-deploy] \
  [--output <result.json>] \
  [--pretty]
```

## Output
The script always prints a single JSON object to stdout. Capture it or read the --output file.

```json
{
  "template_path": "/abs/path/to/template.yaml",
  "overall_passed": true | false,
  "furthest_gate_reached": "yaml_validation" | "cfn_lint" | "deployment" | "deployment_skipped",
  "skipped_deploy": true | false,
  "resources": {
    "total_resources": 2,
    "unique_resource_types": 2,
    "resource_types": ["AWS::S3::Bucket", "AWS::S3::BucketPolicy"]
  },
  "gates": {
    "yaml_validation": { "gate": "yaml_validation", "passed": true | false, "error_count": 0, "errors": [] },
    "cfn_lint":        { "gate": "cfn_lint", "passed": true | false, "error_count": 0, "warning_count": 0, "errors": [], "warnings": [] },
    "deployment":      { "gate": "deployment", "passed": true | false, "stack_id": "...", "failed_resources": [], "completed_resources": [], "error_message": null }
  }
}
```
A gate value of null means it was never reached because an earlier gate failed.

## Exit codes
| Code | Meaning                                          |
| ---- | ------------------------------------------------ |
| 0    | All reached gates passed (overall_passed: true)  |
| 1    | At least one gate failed (overall_passed: false) |

## Gate behaviour
Gates run sequentially and stop on first failure. Downstream gates are skipped and their values are null in the output.

1. yaml_validation — yamllint checks raw YAML syntax. Fast, no AWS required.
2. cfn_lint — cfn-lint checks CloudFormation resource schema and rules. Fast, no AWS required.
3. deployment — Creates a real CloudFormation stack in your AWS account, polls until terminal status, then deletes the stack. Requires valid AWS credentials in environment. Skip with --skip-deploy for cost-free validation.

## Feeding results to another skill
Parse the JSON from stdout:
```python
import json, subprocess
result = json.loads(
    subprocess.check_output([
        "python3", "validate_cfn.py",
        "--template", "my_template.yaml",
        "--skip-deploy"
    ])
)
if not result["overall_passed"]:
    gate = result["furthest_gate_reached"]
    errors = result["gates"][gate]["errors"]
    # compose feedback from errors for an LLM or repair skill
```

## Setup
```bash
cd ~/.openclaw/workspace/skills/validate-cfn
pip install -r requirements.txt
```

`requirements.txt`:
```text
boto3>=1.34.0
botocore>=1.34.0
cfn-lint>=0.85.0
PyYAML>=6.0
yamllint>=1.32.0
```

AWS credentials must be set as environment variables or via ~/.aws/credentials:
```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=ap-southeast-2
```
