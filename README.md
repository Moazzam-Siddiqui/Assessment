# Kesar & Co. AI Order-Support Copilot

A Python implementation of the take-home brief. It classifies incoming emails with an LLM, validates output against the provided JSON Schema, and drafts (never sends) replies using an AI agent with tool calling. The agent can choose between two tools: `get_order` and `search_faq`.

## Run locally

Python 3.10+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Set OPENAI_API_KEY in your shell; never commit it.
python app.py --batch data/test_emails.json --version v2
```

The implementation expects the assignment's fictional fixture files in `data/` and schema at `schemas/llm_output.schema.json`. It writes `audit_log.csv` and `tickets.csv` locally. Running a batch appends audit rows; remove the output CSVs before a clean run. All replies remain drafts; no email delivery integration exists.

## Requirements mapped

- LLM classification/extraction returns schema-validated JSON. If API, parsing, or validation fails, a deterministic fallback emits schema-valid output and lowers confidence to route for human review.
- Agent step uses OpenAI-compatible tool calling. Model chooses `get_order` and/or `search_faq`; tool results constrain the draft.
- Order lookup uses extracted order ID, otherwise sender email. Order details are withheld unless sender matches order email.
- Returns/refunds and complaints create tickets; open tickets for the same order are updated with the follow-up email ID.
- Every email gets one audit row. Low confidence (<0.75), fallback, privacy mismatches, and medical/safety topics are routed to human.
- Customer email content is treated as untrusted input; drafts are never sent.

## Design notes / limitations

This is a local runnable implementation rather than a hosted n8n/Make/Zapier flow. It uses CSV files as a Google Sheets-compatible local audit/ticket store; credentials and sheet IDs are intentionally not embedded. A production deployment should replace the CSV adapter with Google Sheets API, add idempotency/locking for concurrent runs, retry/backoff, monitoring, and a review UI. The model's tool-call path requires an OpenAI-compatible provider that supports function calling.

## Product thinking (brief)

1. **Auto-send vs draft:** I would not auto-send during the pilot. Low-risk order status and FAQ drafts could become auto-send candidates only after sustained audited accuracy. Keep returns, complaints, privacy mismatches, health/safety, low confidence (<0.75), and all fallback classifications human-reviewed.
2. **Cost:** Use measured token counts and the chosen model's current rate rather than guess. Formula: (input tokens × input price + output tokens × output price) / 1,000,000. At 1,000 emails/day, multiply measured per-email cost by 30, then add tool/storage overhead. Token counts and live prices are not available in this local implementation.
3. **Success metric:** Track the percentage of drafts approved without substantive edits, sampled and segmented by intent. Pause if privacy leakage occurs or the audited error rate exceeds the team's safety threshold.
4. **Failure experience:** Customer receives no automated email; the internal draft is held for review. This limits harm from incorrect policy or order details. The support team should follow up within the FAQ's stated 24 business hours.
5. **Don't automate:** Final approval of refunds or sensitive/medical cases should remain manual because policy exceptions and safety judgment require accountable human review.

## AI usage

AI assistance was used to help implement and document the workflow. Human judgment is needed to verify the policy mapping, test outcomes, privacy boundaries, and production readiness.

## Test report

See [TEST_REPORT.md](TEST_REPORT.md). Run the test suite with `pytest`.
