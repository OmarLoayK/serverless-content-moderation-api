import json
import os
import uuid
import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# ─── Config ───────────────────────────────────────────────
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
TABLE_NAME = os.environ.get("TABLE_NAME", "moderation-logs")

CATEGORIES = ["harassment", "hate_speech", "sexual_content",
              "violence", "self_harm", "spam", "none"]

bedrock = boto3.client("bedrock-runtime")
table = boto3.resource("dynamodb").Table(TABLE_NAME)

SYSTEM_PROMPT = f"""You are a content moderation classifier.
Classify the user text against these categories: {", ".join(CATEGORIES)}.

Respond with ONLY a JSON object, no preamble, no markdown fences:
{{"flagged": true/false, "categories": ["..."], "severity": "low"|"medium"|"high", "reason": "one sentence"}}

Use "none" in categories and flagged=false if the text is benign."""


def classify(text):
    resp = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 400,
            "temperature": 0,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": text}],
        }),
    )
    payload = json.loads(resp["body"].read())
    raw = payload["content"][0]["text"].strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def lambda_handler(event, context):
    try:
        body = event.get("body")
        body = json.loads(body) if isinstance(body, str) else (body or event)
        text = (body.get("text") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        return _response(400, {"error": "Malformed request body"})

    if not text:
        return _response(400, {"error": "Field 'text' is required"})
    if len(text) > 5000:
        return _response(400, {"error": "Text exceeds 5000 characters"})

    request_id = str(uuid.uuid4())

    try:
        result = classify(text)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"Bedrock error {code}: {e}")
        return _response(503, {"error": "Classifier unavailable", "code": code})
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"Unparseable model output: {e}")
        return _response(502, {"error": "Invalid classifier response"})

    record = {
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "text_preview": text[:200],
        "text_length": len(text),
        "flagged": result.get("flagged", False),
        "categories": result.get("categories", []),
        "severity": result.get("severity", "low"),
        "reason": result.get("reason", ""),
    }

    try:
        table.put_item(Item=record)
    except ClientError as e:
        print(f"DynamoDB write failed: {e}")  # log but still return the verdict

    return _response(200, {"request_id": request_id, **{k: record[k] for k in
                     ("flagged", "categories", "severity", "reason")}})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
