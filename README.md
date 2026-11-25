# Kesar & Co. Support Copilot - Moazzam Siddiqui

## 1. Summary

- **What I built:** an order-support copilot pipeline: sanitize the email, classify it into the required JSON schema, look up the order (by ID, else sender email), apply policy checks in code, create or update tickets (no duplicates per order), draft a brand-voice reply, and write one audit row per email.
- **Platform and LLM:** a Python (standard library only) reference implementation in `code/copilot.py`, plus an n8n blueprint in `workflow/`. The LLM path uses the Anthropic Messages API; the 25-email run below used the offline rule-based stand-in, not an LLM.
- **Time spent:** not tracked.
- **What works:** all 25 emails run end to end and produce `output/audit_log.csv` and `output/tickets.csv` (9 tickets, E21 updates E04's ticket). Sender check, 7-day return and 48-hour damage logic, prompt-injection stripping, and the confidence threshold are all exercised.
- **What doesn't (yet):** no real LLM run, so no LLM accuracy numbers and no v1 vs v2 comparison; no Google Sheet, screenshots or screen recording; the n8n JSON has never been imported into n8n. See `TEST_REPORT.md` section 5.

## 2. Architecture

```
Webhook -> Sanitize -> Classify (LLM, JSON) -> Validate/repair -> Order lookup + sender check
        -> Policy checks (code) -> Route by intent
              order_status / product_query -> Reply agent (get_order, search_faq)
              return_refund / complaint    -> Find open ticket -> Create or update ticket -> Reply agent
              other                        -> log only
        -> Audit Log row (every email)
```

Screenshots are not included (`screenshots/` is empty): the workflow has not been run in n8n.

| Step / node | What it does | Why I designed it this way |
|---|---|---|
| Sanitize | Drops paragraphs that try to instruct the system and sets `injection_suspected` | Customer text is untrusted; remove it before it reaches any model |
| Classify | Returns the schema JSON; one repair retry; safe fallback (`other`, confidence 0, needs human) | The email is never dropped, even if the model misbehaves |
| Order lookup + sender check | By `order_id`, else by sender email; details only if sender equals `customer_email` | Privacy rule enforced in code, not by prompt |
| Policy checks | 7-day return window, unopened/used, 48-hour damage window | Dates and rules are deterministic; the agent only words the result |
| Ticket upsert | Looks for an open ticket on the same order; updates it (adds email ID and note) or creates one | No duplicates (E04 and E21) |
| Reply drafting agent | Drafts from `get_order` and `search_faq` only | Grounding: no invented dates, links or policies |
| Audit log | One row per email, including `needs_human`, notes and errors | Reviewers can see why each decision was made |

## 3. Setup - how someone else can run this

1. **Offline run (no accounts):** `python3 code/copilot.py --data data --out output --eval code/expected.json`. Python 3.9+; no packages.
2. **LLM classification:** `export ANTHROPIC_API_KEY=...` (and optionally `KESAR_MODEL`), then add `--llm anthropic`. This path is written but untested.
3. **n8n:** import `workflow/kesar_copilot_v2.n8n.json`, attach an HTTP header credential (`x-api-key`) and a Google Sheets credential, replace `<YOUR_SHEET_ID>` and the `raw.githubusercontent.com` URLs, paste the prompts from `prompts/`. The blueprint still needs a "Classify prompt" node and a final Code node that builds the audit row from the agent output.
4. **Sheet:** import `templates/audit_log_template.csv` as "Audit Log" and `templates/tickets_template.csv` as "Tickets" (the offline run already writes both files in `output/`).
5. **25 test emails:** the command in step 1 processes all of them; `scripts/send_test_emails.py` can post them to a webhook.

No keys are stored anywhere in this repo.

## 4. Prompts

Final prompts are in `prompts/classify.md` and `prompts/agent_system.md`. Settings: classifier temperature 0 and JSON-only; agent temperature 0.2, at most 4 tool calls.

## 5. Key decisions and assumptions

- **Low confidence:** below 0.70 goes to `needs_human`. I chose 0.70 because wrong auto-drafts on money and privacy cost more than extra review; it should be tuned on real data. Anger, injection attempts, privacy mismatches, missing orders, medical questions, FAQ gaps, denials and damage claims also go to a human.
- **Duplicate tickets:** before creating, look for an `Open` ticket with the same `order_id`; if found, append the email ID and a note (E21 -> TKT-002).
- **Right customer only:** order details are used only if the sender email equals the order's `customer_email` (E07 gets a privacy reply with no order data).
- **FAQ grounding:** replies use only order fields and FAQ facts; anything else escalates (E13 GST invoice, E20 eczema).
- **Assumptions:** "today" is the email's date; 48 hours is approximated as <= 2 calendar days; "unopened" is taken from the customer's statement and confirmed at pickup; Hindi/Hinglish replies are templates in the offline run.

## 6. Scaling to 1,000 emails/day

That is roughly 0.7 emails a minute on average, so volume is fine; the first things to break are operational. Rate limits and transient API errors need retries with backoff and a queue between the webhook and the LLM steps. Google Sheets writes are slow and quota-limited, so move the audit log and tickets to a database and keep the Sheet as a view. The orders lookup should call the real Shopify API with caching instead of a static file. Add monitoring on invalid-JSON rate, `needs_human` rate, latency and cost per email, and an alert when any of them jumps.

## 7. Product-thinking questions

1. **Auto-send vs. draft:** Auto-send only low-risk, fully grounded replies: order status for the verified owner, and FAQ answers for product and shipping questions, at confidence >= 0.85. Everything involving money (refund decisions, denials), damage claims, anger, privacy mismatches, medical wording or missing data stays a draft for a person. I would start with drafts for everything and open auto-send per category once a week of reviewed drafts shows near-zero edits. The 0.70 threshold here is for flagging; auto-send needs a higher bar because a wrong reply goes out under the brand's name.

2. **Cost:** Assumed prices (check current rates): $1 per million input tokens, $5 per million output tokens. Classification: about 700 input + 100 output = $0.0007 + $0.0005 = $0.0012. Agent (about 70% of emails; several tool turns): about 3,000 input + 250 output = $0.0030 + $0.00125 = $0.00425, times 0.7 = $0.0030. Total about $0.0042 per email, roughly Rs 0.35 at Rs 85/$. At 1,000 emails a day (30,000 a month): about $125, roughly Rs 10,600 a month. Prompt caching of the long system prompt would cut this. Token counts are estimates, not measured.

3. **Success metric:** Share of drafts approved by the support person without edits, split by intent, in week 1. It captures both accuracy and tone and is easy to log. I would switch it off (back to manual) if any reply with a wrong order fact, a policy invention, or order details sent to the wrong person reaches a customer, or if the unedited-approval rate stays below about 60% after the first week's prompt fixes.

4. **Failure experience:** The customer gets a confident but wrong answer, for example a wrong ETA or an incorrect refund promise, then has to write again and trusts the brand less. To limit it: only drafts for risky categories, facts only from tools, policy checks in code, a standard "a team member will follow up within 24 business hours" fallback, an audit log to trace errors, and a kill switch per intent. Angry or repeat emails always go to a human.

5. **Don't automate:** The final refund or replacement approval, especially denials of "didn't suit me" returns and damage claims. It involves money, judgment about photos, and goodwill exceptions, and an injection attempt (like E12) is exactly what would abuse an automated approval. The copilot prepares the ticket, eligibility facts and a draft; a person decides.

## 8. How I used AI tools

I used an AI assistant (Claude) to draft the reference implementation, prompts, workflow blueprint and these documents, working from the assignment files. *Edit this paragraph to describe what you reviewed, ran and changed yourself before submitting.*

## 9. If I had one more week

- Run the 25 emails through a real LLM in n8n, record v1 and v2, and replace the placeholder results.
- Build and test the n8n workflow, the Google Sheet, screenshots and the 5-minute recording.
- Write a fresh test set from someone other than me, including more Hindi/Hinglish and edge-of-window cases.
- Track tokens and cost per email in the audit log, and add retries with backoff.
