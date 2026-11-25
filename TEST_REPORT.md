# Test Report - Kesar & Co. Support Copilot

**Candidate name:** Moazzam Siddiqui
**Platform used:** Python reference implementation (`code/copilot.py`) plus an untested n8n blueprint (`workflow/`)
**LLM(s) used:** none in the results below. The classifier in this run is a rule-based stand-in; the Anthropic API path exists in the code but was not exercised.

**Scope note:** this report documents the offline run of `code/copilot.py` only. It does not validate the separate Groq-based `app.py` prototype, and no Groq API execution is claimed.

> **Read this first.** The numbers below come from running the deterministic pipeline (sanitizer, order lookup,
> sender check, policy checks, ticket dedup, reply templates) over all 25 emails. The classifier was a hand-written
> rule set that I tuned while looking at these same 25 emails, so **25/25 here is a sanity check of the pipeline,
> not evidence of how an LLM classifier or unseen emails would score.** No v1 was run and no real LLM calls were made.

---

## 1. How I decided what "correct" means

I wrote the expected result for each email (`code/expected.json`) from `data/faq.md` and `data/orders.json` before running anything. Judgment calls:

- "Today" is the email's `received_at` date. Return window = 7 days after `delivered_date`; damage/wrong/missing window = 48 hours, approximated as <= 2 calendar days because delivery times are not in the data.
- Eligible returns (sealed, within 7 days) get a ticket and a draft and do not need a human. Denials, damage claims, angry customers, privacy mismatches, missing orders, medical questions, FAQ gaps and injection attempts set `needs_human`.
- Every return/refund and complaint with a matching order gets a ticket; a follow-up on the same order (E21) updates the open ticket instead of creating a new one.
- Confidence threshold for `needs_human` is 0.70.
- E17 (partnership) is `other` and gets a short redirect draft because the FAQ lists the partnerships address.

## 2. Results per email

v1 was not run, so v1 columns are `-`.

| Email | Expected intent | Expected action | v1 intent | v1 action | v1 ok | v2 intent | v2 action | v2 ok | Notes |
|---|---|---|---|---|---|---|---|---|---|
| E01 | order_status | Draft reply with ETA 21 Nov + tracking link | - | - | - | order_status | draft_reply | yes |  |
| E02 | product_query | Draft reply from FAQ (all skin types incl. oily, vegan) | - | - | - | product_query | draft_reply | yes |  |
| E03 | return_refund | Eligible (4 days, sealed): ticket + draft, prepaid refund 5-7 days after QC | - | - | - | return_refund | ticket_created + draft_reply (TKT-001) | yes |  |
| E04 | complaint | Within 48h: ticket (High) + draft asking for photos, human verifies | - | - | - | complaint | ticket_created + draft_reply + needs_human (TKT-002) | yes |  |
| E05 | order_status | Draft in Hinglish with ETA 23 Nov + tracking, no hard promise | - | - | - | order_status | draft_reply | yes | Hinglish reply template. |
| E06 | other | Log only, no reply | - | - | - | other | log_only | yes |  |
| E07 | order_status | Sender is not the order owner: share nothing, human review | - | - | - | order_status | draft_reply + needs_human | yes | Privacy rule: sender email differs from order owner. |
| E08 | complaint | Angry + leak within 48h: High ticket, draft, human handles | - | - | - | complaint | ticket_created + draft_reply + needs_human (TKT-003) | yes |  |
| E09 | product_query | Draft in Hindi: NE 6-8 business days, COD allowed (Rs 3,499 <= 5,000, Rs 49 fee) | - | - | - | product_query | draft_reply | yes | Hindi reply; language is detected, reply template is Hindi. |
| E10 | order_status | No order ID: find KC-1044 by sender email, draft with ETA 25 Nov | - | - | - | order_status | draft_reply | yes | Order found via sender email. |
| E11 | return_refund | Eligible (5 days, sealed kit): ticket + draft, return as complete set | - | - | - | return_refund | ticket_created + draft_reply (TKT-004) | yes |  |
| E12 | return_refund | Injection ignored; 8 days + used = not eligible: ticket, denial draft, human review | - | - | - | return_refund | ticket_created + draft_reply + needs_human (TKT-005) | yes | Contains embedded instructions; stripped before classification and flagged. No approval given. |
| E13 | other | GST invoice not in FAQ: escalate to human, holding reply only | - | - | - | other | draft_reply + needs_human | yes | Not in FAQ, so no policy answer. |
| E14 | complaint | Wrong item within 48h: ticket + draft asking photos, human verifies | - | - | - | complaint | ticket_created + draft_reply + needs_human (TKT-006) | yes |  |
| E15 | order_status | Out for delivery today; COD amount Rs 1,897 in draft | - | - | - | order_status | draft_reply | yes |  |
| E16 | return_refund | 15 days + used: not eligible; ticket, denial draft, human review | - | - | - | return_refund | ticket_created + draft_reply + needs_human (TKT-007) | yes |  |
| E17 | other | Redirect to partnerships@ address (no ticket) | - | - | - | other | draft_reply | yes | Brief says 'other = log only'; I draft a one-line redirect because the FAQ lists the partnerships address. |
| E18 | order_status | Vague, no order, sender unknown: ask for order number, human | - | - | - | order_status | draft_reply + needs_human | yes | Only low-confidence (0.55) case; routed by threshold 0.70. |
| E19 | return_refund | Eligible COD return: ticket + draft, UPI/bank refund after pickup | - | - | - | return_refund | ticket_created + draft_reply (TKT-008) | yes |  |
| E20 | product_query | No medical claims; dermatologist + patch test advice; human | - | - | - | product_query | draft_reply + needs_human | yes |  |
| E21 | complaint | Follow-up with photos: update the existing KC-1052 ticket, no duplicate | - | - | - | complaint | ticket_updated + draft_reply + needs_human (TKT-002) | yes | Same order as E04; ticket TKT-002 updated, not duplicated. |
| E22 | order_status | KC-1099 not found: ask to verify number, human | - | - | - | order_status | draft_reply + needs_human | yes | Order id not in orders.json. |
| E23 | complaint | Missing item within 48h: ticket + draft, refund Rs 499 only after verification | - | - | - | complaint | ticket_created + draft_reply + needs_human (TKT-009) | yes |  |
| E24 | order_status | Cancelled order: explain, refund Rs 828 initiated 16 Nov, 5-7 business days | - | - | - | order_status | draft_reply | yes |  |
| E25 | other | Positive thanks: log only | - | - | - | other | log_only | yes |  |

## 3. Summary

| Metric | Prompt v1 | Prompt v2 (offline harness) |
|---|---|---|
| Emails run (*n*) | not run | 25 |
| Intent accuracy | - | 25 / 25 |
| Correct action (intent + needs_human + ticket) | - | 25 / 25 |
| Valid JSON on first try | - | 25 / 25 (rule-based output, so not a meaningful LLM measure) |
| Emails sent to "needs human" | - | 12 |
| Avg. tokens per email | - | not applicable (no LLM calls) |
| Est. cost per email | - | not measured; see README section 7 for an estimate |

Tickets: 9 created for 10 qualifying emails (E04 and E21 share one ticket).

## 4. What I changed while building, and why

There is no LLM v1 to compare against. The one change the harness did surface:

- The first run flagged E11 as angry because a loose keyword ("now", from "won't need it now") matched. I removed it, and E08 is still caught by stronger signals. This is also the kind of failure an LLM sentiment step can have in reverse, so the threshold and `needs_human` rules should be rechecked on a real run.

Planned v1 to v2 changes for the LLM run: wrap customer text in `<email>` tags with an explicit "untrusted data" rule, move eligibility into code, add the schema-repair retry, and add `injection_suspected`.

## 5. Failures I could not fix (be honest)

| Email | What goes wrong | Why I think it happens | What I would try next |
|---|---|---|---|
| All | No real LLM run, no v1 comparison, no Google Sheet, no screenshots, no video | I built and ran this where there was no n8n instance, Sheets access or LLM key | Import the workflow, run the 25 emails with the Anthropic path, record both versions |
| All | Classifier is keyword rules tuned on these 25 emails | Written to match the test set | Evaluate on a fresh set of emails written by someone else |
| E05, E09 | Hinglish/Hindi replies are fixed templates | Reply language only handled for these two cases | Let the agent write the reply in the customer's language |
| E04, E08, E14, E23 | The 48-hour window is approximated by calendar days | Order data has no delivery time | Add delivery timestamps, or escalate edge cases (exactly 2 days) |
| E19, E03, E11 | "Unopened" is taken from the customer's words | Nothing in the data can verify it | Confirm at pickup quality check (the draft says so) |
| E12 | Injection detection is a regex list | Only catches obvious phrasing | Combine the LLM `injection_suspected` flag with the regex and never let email text change routing |
