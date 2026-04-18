"""Account Performance IQ — Bedrock Agent action group handler.

Mock data lives in memory so the tutorial is self-contained — no Athena, no
S3, no Glue. The only production shape this skips is the SQL layer; the
Bedrock <-> Lambda event/response contract is the real thing.

Bedrock sends OpenAPI-schema action group events with ``apiPath`` +
``httpMethod`` and expects ``responseBody.application/json.body`` back. Get
that envelope wrong and Bedrock raises "APIPath in Lambda response doesn't
match input" at runtime.
"""

from __future__ import annotations

import json
from typing import Any


# Mock SaaS accounts. Numbers are representative, not real.
def _account(
    account_id: str,
    active_users: int,
    feature_adoption_pct: float,
    mrr_cad: float,
    health_score: float,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "active_users": active_users,
        "feature_adoption_pct": feature_adoption_pct,
        "mrr_cad": mrr_cad,
        "health_score": health_score,
    }


_ACCOUNTS: list[dict[str, Any]] = [
    _account("ACC-101", 412, 78.2, 12_400.0, 82.0),
    _account("ACC-102", 1_240, 64.5, 41_800.0, 88.0),
    _account("ACC-103", 58, 22.1, 1_950.0, 34.0),
    _account("ACC-104", 305, 55.0, 8_700.0, 67.0),
    _account("ACC-105", 2_015, 71.9, 58_200.0, 91.0),
    _account("ACC-106", 144, 40.3, 4_100.0, 52.0),
    _account("ACC-107", 89, 18.7, 2_250.0, 29.0),
    _account("ACC-108", 760, 68.4, 22_500.0, 79.0),
]

_ALLOWED_METRICS = {"mrr_cad", "active_users", "feature_adoption_pct", "health_score"}


def get_account_summary(params: dict[str, str]) -> str:
    account_id = params.get("account_id", "")
    match = next((a for a in _ACCOUNTS if a["account_id"] == account_id), None)
    if match is None:
        return json.dumps({"error": f"account not found: {account_id!r}"})
    return json.dumps({
        "account_id": match["account_id"],
        "window": {"start": params.get("start_date", ""), "end": params.get("end_date", "")},
        "active_users": match["active_users"],
        "feature_adoption_pct": match["feature_adoption_pct"],
        "mrr_cad": match["mrr_cad"],
        "health_score": match["health_score"],
    })


def get_top_accounts(params: dict[str, str]) -> str:
    metric = params.get("metric", "mrr_cad")
    if metric not in _ALLOWED_METRICS:
        metric = "mrr_cad"
    order = params.get("order", "desc").lower()
    reverse = order != "asc"
    try:
        limit = int(params.get("limit", "5"))
    except ValueError:
        limit = 5

    ranked = sorted(_ACCOUNTS, key=lambda a: a[metric], reverse=reverse)[:limit]
    return json.dumps({
        "metric": metric,
        "order": "desc" if reverse else "asc",
        "window": {"start": params.get("start_date", ""), "end": params.get("end_date", "")},
        "results": [
            {"account_id": a["account_id"], metric: a[metric]} for a in ranked
        ],
    })


_DISPATCH = {
    "get_account_summary": get_account_summary,
    "get_top_accounts": get_top_accounts,
}


def _parse_params(event: dict[str, Any]) -> dict[str, str]:
    """Flatten Bedrock's ``parameters`` + JSON ``requestBody`` into a flat dict."""
    parsed: dict[str, str] = {}
    for param in event.get("parameters", []) or []:
        name = param.get("name")
        if name:
            parsed[name] = str(param.get("value", ""))
    body = event.get("requestBody", {}) or {}
    json_body = (body.get("content", {}) or {}).get("application/json", {}) or {}
    for prop in json_body.get("properties", []) or []:
        name = prop.get("name")
        if name:
            parsed[name] = str(prop.get("value", ""))
    return parsed


def _response(event: dict[str, Any], body: str, status: int = 200) -> dict[str, Any]:
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "apiPath": event.get("apiPath", ""),
            "httpMethod": event.get("httpMethod", "POST"),
            "httpStatusCode": status,
            "responseBody": {"application/json": {"body": body}},
        },
    }


def lambda_handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    api_path = event.get("apiPath", "").lstrip("/")
    fn = _DISPATCH.get(api_path)
    if fn is None:
        return _response(event, json.dumps({"error": f"unknown apiPath: {api_path!r}"}), 404)
    return _response(event, fn(_parse_params(event)))
