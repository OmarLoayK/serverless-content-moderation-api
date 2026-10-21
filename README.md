# Serverless Content Moderation API

An HTTP API that classifies user-submitted text against content policy categories using an LLM, and logs every verdict for audit. Built on AWS Lambda, API Gateway, Amazon Bedrock, and DynamoDB.

```
POST /moderate
      │
      ▼
┌─────────────────┐
│  API Gateway    │  HTTP API, Lambda proxy integration
└────────┬────────┘
         ▼
┌─────────────────┐     ┌──────────────────────────┐
│  Lambda         │────▶│  Bedrock (Claude Sonnet)  │  classification
│  (Python 3.14)  │     └──────────────────────────┘
└────────┬────────┘
         ▼
┌─────────────────┐
│  DynamoDB       │  audit log, one row per request
└─────────────────┘
```

## What it does

Send it text, get back a structured verdict: whether the content is flagged, which policy categories it falls under, a severity rating, and a one-sentence rationale. Every request is written to DynamoDB with a UUID so decisions can be audited after the fact.

Categories: `harassment`, `hate_speech`, `sexual_content`, `violence`, `self_harm`, `spam`, `none`.

## Usage

```bash
curl -X POST https://<api-id>.execute-api.us-east-1.amazonaws.com/moderate \
  -H "Content-Type: application/json" \
  -d '{"text": "your text here"}'
```

**Flagged content:**

```json
{
  "request_id": "d5cd78e9-1e70-4253-bfd9-f598cbc5c5ee",
  "flagged": true,
  "categories": ["harassment"],
  "severity": "low",
  "reason": "The message contains a personal insult and an expression of ill will toward the recipient."
}
```

**Benign content:**

```json
{
  "request_id": "5bd7b908-a633-4d54-a2ae-d30e75b8d4c2",
  "flagged": false,
  "categories": ["none"],
  "severity": "low",
  "reason": "The text is a polite expression of gratitude with no harmful content."
}
```

## Response codes

| Code | Meaning |
|------|---------|
| 200 | Classification succeeded |
| 400 | Missing `text` field, malformed JSON, or text over 5000 characters |
| 502 | Model returned output that could not be parsed as valid JSON |
| 503 | Bedrock unavailable or returned an error |

## Design decisions

**`temperature: 0`.** Moderation needs to be reproducible. The same input must produce the same verdict every time — a classifier that returns different answers on re-run is not auditable.

**Defensive JSON parsing.** The model is instructed to return bare JSON, but LLMs sometimes wrap output in markdown fences regardless. The handler strips fences before parsing and returns a 502 rather than a 500 if the output is still unparseable, distinguishing "the model misbehaved" from "the service broke."

**Logging failures don't fail the request.** A DynamoDB write error is logged to CloudWatch but does not prevent the caller from receiving their verdict. The audit trail is important; blocking moderation on it would be worse than a gap in the log.

**Only a 200-character preview is stored.** Full submitted text is never persisted. The log retains enough context to audit a decision without becoming a copy of everything users wrote — deliberate data minimization.

**Model ID is an environment variable.** Swapping models requires no code change or redeploy. This mattered in practice: the project was built against one model and switched to another when the first turned out to be unavailable on this account.

**Inference profile over bare model ID.** Uses `global.anthropic.claude-sonnet-4-6`, a cross-region inference profile, rather than a region-pinned model ID — better availability under load.

## Known limitations

- **IAM is over-permissioned.** The execution role uses `AmazonBedrockFullAccess` and `AmazonDynamoDBFullAccess` to move quickly. Production would scope to `bedrock:InvokeModel` on the specific inference profile ARN and `dynamodb:PutItem` on the specific table ARN.
- **No authentication.** The endpoint is public. A real deployment would require an API key, IAM auth, or a Lambda authorizer, plus rate limiting.
- **Single-model classification.** No ensemble, no confidence threshold tuning, no human review queue for borderline cases — all of which a production moderation system would need.
- **No evaluation set.** Correctness was verified on hand-picked positive and negative cases, not measured against a labeled dataset. Real precision and recall numbers are unknown.

## Stack

AWS Lambda · API Gateway · Amazon Bedrock · DynamoDB · IAM · Python 3.14 · boto3
