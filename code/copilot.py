#!/usr/bin/env python3
"""Kesar & Co. support copilot: reference implementation of the pipeline.

Pipeline per email:
  sanitize -> classify (JSON, schema-validated) -> order lookup (+ sender check)
  -> policy checks (deterministic) -> ticket create/update (dedup by order)
  -> draft reply (facts only from orders.json / faq.md) -> audit log row

Classifier backends:
  heuristic  (default) rule-based stand-in, runs offline, no API key
  anthropic  calls the Messages API with prompts/classify.md (needs ANTHROPIC_API_KEY)

Usage:
  python3 code/copilot.py --data data --out output [--llm heuristic|anthropic] [--eval code/expected.json]
Only the Python standard library is used.
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

PROMPT_VERSION = "v2"
CONF_THRESHOLD = 0.70
INTENTS = ["order_status", "product_query", "return_refund", "complaint", "other"]
SENTIMENTS = ["positive", "neutral", "negative", "angry"]
LANGS = ["en", "hi", "hinglish"]

AUDIT_COLS = ["email_id", "received_at", "from_email", "intent", "secondary_intent", "order_id", "language",
              "sentiment", "confidence", "order_found", "action_taken", "needs_human", "ticket_id",
              "reply_draft", "prompt_version", "processed_at", "error", "notes"]
TICKET_COLS = ["ticket_id", "created_at", "updated_at", "order_id", "customer_email", "email_ids", "intent",
               "priority", "status", "summary", "notes"]

SIGN = "Warm regards,\nTeam Kesar"


# ----------------------------------------------------------------- helpers
def fmt_date(s):
    d = datetime.strptime(s, "%Y-%m-%d"); return f"{d.day} {d:%b %Y}"


def inr(n):
    return "₹{:,}".format(n)


def first_name(name):
    return name.split()[0] if name else "there"


# ----------------------------------------------------------------- security
INJECTION_RE = re.compile(r"(system note|ignore (all )?(previous|prior) (instructions|policies)|"
                          r"note to (the )?ai|as an ai assistant|you are now)", re.I)


def sanitize(body):
    """Drop paragraphs that try to instruct the system. Returns (clean_text, injected)."""
    parts = re.split(r"\n\s*-{3,}\s*\n|\n\s*\n", body)
    kept, injected = [], False
    for p in parts:
        if INJECTION_RE.search(p):
            injected = True
        else:
            kept.append(p)
    return "\n\n".join(kept), injected


# ----------------------------------------------------------------- classification
def detect_language(text):
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    markers = ["kab", "aayega", "maine", "kiya", "tha", "nahi", "hua", "bata", "jaldi", "pls", "abhi", "tak", "hai", "raha", "ho"]
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(1 for w in words if w in markers)
    return "hinglish" if hits >= 4 else "en"


def classify_heuristic(email, clean):
    text = (email["subject"] + "\n" + clean)
    low = text.lower()
    sender = email["from"]["email"].lower()
    m = re.search(r"\bKC-\d{4}\b", text)
    oid = m.group(0) if m else None
    flags = {"topic": None, "medical": False, "damage": None}
    intent, secondary, conf = "other", None, 0.8

    if re.search(r"ranking|guaranteed|selected for|seo", low) and "order" not in low:
        flags["topic"], conf = "spam", 0.95
    elif re.search(r"collab|media kit|paid collaboration|sponsor", low):
        flags["topic"], conf = "partnership", 0.9
    elif re.search(r"\bgst\b|gstin|tax invoice", low):
        flags["topic"], conf = "invoice", 0.85
    elif re.search(r"eczema|psoriasis|\bcure|pregnan|allerg|dermatolog|rash|acne", low):
        intent, flags["medical"], conf = "product_query", True, 0.9
    elif re.search(r"leaked|cracked|damaged|broken|wrong item|instead|is missing|only had|missing", low):
        intent, conf = "complaint", 0.93
        flags["damage"] = ("wrong" if re.search(r"wrong item|instead", low)
                           else "missing" if re.search(r"missing|only had", low) else "damaged")
        if re.search(r"refund|replacement|send the correct|money", low):
            secondary = "return_refund"
    elif re.search(r"\breturn|refund|money back", low):
        intent, conf = "return_refund", 0.92
        if re.search(r"not happy|no difference|unacceptable|careless", low):
            secondary = "complaint"
    elif re.search(r"where is|status|\bkab\b|tracking|when .*(deliver|reach|arrive)|will be delivered|"
                   r"not received|haven't received|any update|waiting", low):
        intent, conf = "order_status", 0.9
    elif re.search(r"vegan|oily|skin|\bcod\b|कैश|डिलीवरी", low):
        intent, conf = "product_query", 0.9
    elif re.search(r"lovely|thank you|wonderful|love ", low):
        flags["topic"], conf = "thanks", 0.9

    if intent == "order_status" and not oid and len(re.findall(r"\w+", clean)) < 15:
        conf = 0.55  # vague message, nothing to anchor on

    if re.search(r"worst|unacceptable|immediately|!!!", low) and intent in ("complaint", "return_refund"):
        sentiment = "angry"
    elif intent in ("complaint", "return_refund") or re.search(r"waiting forever|unfortunately", low):
        sentiment = "negative"
    elif flags["topic"] == "thanks":
        sentiment = "positive"
    else:
        sentiment = "neutral"

    out = {"intent": intent, "secondary_intent": secondary, "order_id": oid, "sentiment": sentiment,
           "language": detect_language(clean), "confidence": conf, "summary": ""}
    out["summary"] = make_summary(out, flags, email)
    return out, flags


def make_summary(c, flags, email):
    oid = c["order_id"] or "no order ID given"
    if flags["topic"] == "spam":
        s = "Unsolicited SEO promotion, no customer need."
    elif flags["topic"] == "partnership":
        s = "Influencer asking whom to contact for a paid collaboration."
    elif flags["topic"] == "invoice":
        s = "Company asks for a GST invoice with its GSTIN for a past purchase."
    elif flags["topic"] == "thanks":
        s = "Customer thanks the team for delivery and the product."
    elif flags["medical"]:
        s = "Customer asks whether Kumkumadi Night Oil will cure eczema (medical claim question)."
    elif c["intent"] == "order_status":
        s = f"Customer asking for status/delivery of order ({oid})."
    elif c["intent"] == "product_query":
        s = "Customer asking product or shipping/COD questions before buying."
    elif c["intent"] == "return_refund":
        s = f"Customer requests return/refund for order {oid}."
    elif flags["damage"]:
        s = {"damaged": "Damaged/leaking item reported",
             "wrong": "Wrong item delivered",
             "missing": "Item missing from delivered order"}[flags["damage"]] + f" for order {oid}."
    else:
        s = "Other enquiry."
    return s[:200]


def validate(obj):
    errs = []
    for k in ["intent", "secondary_intent", "order_id", "sentiment", "language", "confidence", "summary"]:
        if k not in obj:
            errs.append(f"missing {k}")
    if errs:
        return errs
    if obj["intent"] not in INTENTS:
        errs.append("bad intent")
    if obj["secondary_intent"] not in INTENTS + [None]:
        errs.append("bad secondary_intent")
    if obj["order_id"] is not None and not re.fullmatch(r"KC-[0-9]{4}", str(obj["order_id"])):
        errs.append("bad order_id")
    if obj["sentiment"] not in SENTIMENTS:
        errs.append("bad sentiment")
    if obj["language"] not in LANGS:
        errs.append("bad language")
    if not isinstance(obj["confidence"], (int, float)) or not 0 <= obj["confidence"] <= 1:
        errs.append("bad confidence")
    if not isinstance(obj["summary"], str) or len(obj["summary"]) > 200:
        errs.append("bad summary")
    return errs


def classify_llm(email, clean, root):
    """Anthropic Messages API classifier. Not exercised in the offline run (needs a key)."""
    prompt = (root / "prompts" / "classify.md").read_text(encoding="utf-8")
    body = {"model": os.environ.get("KESAR_MODEL", "claude-haiku-5-5"), "max_tokens": 400, "temperature": 0,
            "system": prompt,
            "messages": [{"role": "user", "content":
                          "<email>\nFrom: {}\nSubject: {}\n\n{}\n</email>".format(
                              email["from"]["email"], email["subject"], clean)}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                          "anthropic-version": "2023-06-01", "content-type": "application/json"})
    for attempt in range(2):  # one repair retry
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = json.load(r)["content"][0]["text"]
        try:
            obj = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
            if not validate(obj):
                return obj
        except Exception:
            pass
    raise ValueError("LLM output invalid after retry")


# ----------------------------------------------------------------- policy checks
def days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def return_eligibility(order, received_date, text):
    low = text.lower()
    reasons = []
    if order["status"] != "delivered":
        return False, ["order not delivered yet"]
    d = days_between(order["delivered_date"], received_date)
    within = d <= 7
    reasons.append(f"{d} days since delivery ({'within' if within else 'outside'} 7-day window)")
    used = bool(re.search(r"used (them|it)|have been using|\busing\b|didn't suit|no difference|opened it", low)) \
        and not re.search(r"unopened|never opened|seal", low)
    unopened = bool(re.search(r"unopened|never opened|sealed|seals? intact", low))
    if used:
        reasons.append("customer says product was used")
    elif unopened:
        reasons.append("customer states unopened/sealed (to be confirmed at pickup QC)")
    else:
        reasons.append("opened/unopened state not stated")
    return (within and not used and unopened), reasons


def damage_window(order, received_date):
    if order["status"] != "delivered":
        return None
    return days_between(order["delivered_date"], received_date) <= 2


# ----------------------------------------------------------------- reply drafting
def items_text(order):
    return ", ".join(("{}x ".format(i["qty"]) if i["qty"] > 1 else "") + i["name"] for i in order["items"])


def reply_order_status(first, order, lang):
    o, st = order, order["status"]
    if st == "shipped" and lang == "hinglish":
        return (f"Hi {first},\n\nAapka order {o['order_id']} ({items_text(o)}) {o['courier']} ke saath shipped ho chuka hai "
                f"aur estimated delivery {fmt_date(o['estimated_delivery'])} hai. Tracking link: {o['tracking_url']}\n\n"
                f"Yeh ek estimate hai, isliye exact din ka pakka wada hum nahi kar sakte. Agar aapke travel se pehle "
                f"order na pahunche, to is email par reply kijiye, hum madad karenge.\n\n{SIGN}")
    if st == "shipped":
        return (f"Hi {first},\n\nYour order {o['order_id']} ({items_text(o)}) has been shipped with {o['courier']} and is "
                f"estimated to be delivered by {fmt_date(o['estimated_delivery'])}. You can track it here: "
                f"{o['tracking_url']}\n\nIf it has not arrived by then, just reply to this email and we will look into it.\n\n{SIGN}")
    if st == "out_for_delivery":
        extra = ""
        if o["payment_method"] == "cod":
            extra = (f" As this is a cash on delivery order, the amount payable is {inr(o['total'])} (including the "
                     f"{inr(o['cod_fee'])} COD handling fee). Delivery partners may not carry change, so please keep exact change ready.")
        return (f"Hi {first},\n\nYour order {o['order_id']} is out for delivery with {o['courier']} (estimated "
                f"delivery {fmt_date(o['estimated_delivery'])}).{extra} Tracking: {o['tracking_url']}\n\n{SIGN}")
    if st == "cancelled":
        return (f"Hi {first},\n\nI'm sorry for the confusion. Order {o['order_id']} was cancelled on "
                f"{fmt_date(o['cancelled_date'])} because of: {o['cancellation_reason'].lower()}, which is why no tracking "
                f"link was issued. A refund of {inr(o['total'])} was initiated to your original payment method on "
                f"{fmt_date(o['cancelled_date'])}, and prepaid refunds reach you within 5-7 business days.\n\n{SIGN}")
    if st == "delivered":
        return (f"Hi {first},\n\nOur records show order {o['order_id']} was delivered on {fmt_date(o['delivered_date'])} "
                f"by {o['courier']}. Tracking: {o['tracking_url']}\n\n{SIGN}")
    return None


def reply_privacy(first):
    return (f"Hi {first},\n\nThank you for reaching out. To protect our customers' privacy, we can only share order details "
            f"(including tracking links and delivery addresses) with the email address used to place the order, so I'm "
            f"not able to share them here. The person who placed the order can write to us from that address, or "
            f"check the tracking email we send when an order is dispatched.\n\n{SIGN}")


def reply_not_found(first, oid):
    ref = f"order {oid}" if oid else "an order"
    return (f"Hi {first},\n\nThank you for writing in. We could not find {ref} linked to this email address. Could you "
            f"reply with your order number (it looks like KC-1234) and the email address used while ordering? A team "
            f"member will follow up within 24 business hours.\n\n{SIGN}")


def reply_return(first, order, eligible, reasons):
    o = order
    if eligible:
        if o["payment_method"] == "cod":
            refund = ("Because this was a cash on delivery order, the refund goes to you by UPI or bank transfer; we will "
                      "ask for your UPI ID (or account details) once the pickup is completed. The "
                      f"{inr(o['cod_fee'])} COD fee is non-refundable.")
        else:
            refund = ("Your refund goes to the original payment method within 5-7 business days after the returned "
                      "item passes quality check.")
            if o["shipping_fee"]:
                refund += f" The {inr(o['shipping_fee'])} shipping fee is non-refundable."
        kit = " Since it is a kit, it needs to be returned as a complete set." if any("Kit" in i["name"] for i in o["items"]) else ""
        return (f"Hi {first},\n\nYour order {o['order_id']} was delivered on {fmt_date(o['delivered_date'])}, which is within "
                f"our 7-day return window, so we can arrange a free reverse pickup within 2-3 business days, provided the "
                f"items are unused, unopened and sealed.{kit} {refund}\n\n{SIGN}")
    return (f"Hi {first},\n\nI'm sorry the product did not work out for you. Returns are accepted within 7 days of delivery "
            f"and only for unused, unopened products; for hygiene reasons we cannot take back opened or used items, "
            f"including cases where a product did not suit or show results. Order {o['order_id']} was delivered on "
            f"{fmt_date(o['delivered_date'])} and has been used, so it does not qualify. A team member will review your "
            f"request and follow up within 24 business hours.\n\n{SIGN}")


def reply_complaint(first, order, kind, photos, wants, angry):
    o = order
    opener = ("I'm really sorry about this experience, and I understand how frustrating it is. " if angry
              else "I'm sorry about this. ")
    what = {"damaged": "the damaged/leaking item", "wrong": "the wrong item", "missing": "the missing item"}[kind]
    if photos:
        body = ("Thank you for the photos. Our team will verify them and arrange your replacement; a team member "
                "will follow up within 24 business hours.")
    else:
        if kind == "missing":
            body = ("Please reply with photos of the product(s) and the outer box. We will verify against our packing "
                    "weight record and then either ship the missing item or refund its value, whichever you prefer.")
        else:
            body = ("Please reply with photos of the product and the outer box. Once verified, we can send a free "
                    "replacement or give you a full refund, your choice")
            body += (", and we will arrange pickup of the wrong item." if kind == "wrong" else ".")
        body += " A team member will follow up within 24 business hours."
    return f"Hi {first},\n\n{opener}Regarding order {o['order_id']} and {what}: {body}\n\n{SIGN}"


def reply_product(first, text, lang, flags):
    low = text.lower()
    if flags["medical"]:
        return (f"Hi {first},\n\nI'm sorry to hear eczema has been so persistent. Our products are cosmetics, not "
                f"medicines, so we cannot say that Kumkumadi Night Oil will treat or cure eczema or any skin condition. "
                f"For a condition like eczema, please consult a dermatologist before using it, and always do a patch "
                f"test (inner forearm, 24 hours) first. A team member will follow up within 24 business hours.\n\n{SIGN}")
    if lang == "hi":
        return ("नमस्ते भास्कर जी,\n\n"
                "गुवाहाटी (नॉर्थ-ईस्ट) के लिए "
                "ऑर्डर आम तौर पर 1-2 कार्य दिवस में "
                "डिस्पैच होता है और उसके बाद "
                "डिलीवरी में 6-8 कार्य दिवस लगते हैं। "
                "Bridal Glow Kit (₹3,499) पर कैश ऑन डिलीवरी उपलब्ध है, "
                "क्योंकि COD ₹5,000 तक के ऑर्डर पर मिलता है; "
                "इसमें ₹49 का COD हैंडलिंग शुल्क लगता है "
                "जो रिफंड नहीं होता। ₹999 या उससे अधिक के ऑर्डर पर "
                "शिपिंग मुफ़्त है।\n\n" + SIGN)
    if "serum" in low and "oily" in low:
        return (f"Hi {first},\n\nYes, the Saffron Glow Face Serum is lightweight and non-greasy, and it is suitable for all "
                f"skin types, including oily skin. It is also vegan, so it fits your switch. You can use it morning and "
                f"night after cleansing, and we always suggest a patch test (inner forearm, 24 hours) before first "
                f"use.\n\n{SIGN}")
    return None


def reply_other(first, flags):
    if flags["topic"] == "partnership":
        return (f"Hi {first},\n\nThank you for your interest in Kesar & Co. For collaboration and partnership enquiries, "
                f"please write to partnerships@kesarandco.example and the team will take it from there.\n\n{SIGN}")
    if flags["topic"] == "invoice":
        return (f"Hi {first},\n\nThank you for writing in. We have passed your GST invoice request to a team member, who "
                f"will follow up within 24 business hours.\n\n{SIGN}")
    return None


# ----------------------------------------------------------------- pipeline
class Tickets:
    def __init__(self):
        self.rows = []

    def upsert(self, order_id, email, email_id, intent, priority, summary, note, ts):
        for t in self.rows:
            if t["order_id"] == order_id and t["status"] == "Open":
                t["email_ids"] += ";" + email_id
                t["updated_at"] = ts
                t["notes"] += f" | {email_id}: {note}"
                if priority == "High":
                    t["priority"] = "High"
                return t["ticket_id"], "ticket_updated"
        tid = "TKT-{:03d}".format(len(self.rows) + 1)
        self.rows.append({"ticket_id": tid, "created_at": ts, "updated_at": ts, "order_id": order_id,
                          "customer_email": email, "email_ids": email_id, "intent": intent, "priority": priority,
                          "status": "Open", "summary": summary, "notes": f"{email_id}: {note}"})
        return tid, "ticket_created"


def process(email, orders, by_email, tickets, backend, root):
    row = {c: "" for c in AUDIT_COLS}
    sender = email["from"]["email"].lower()
    ts = email["received_at"]
    received_date = ts[:10]
    row.update(email_id=email["id"], received_at=ts, from_email=sender, prompt_version=PROMPT_VERSION)
    notes, error = [], ""

    clean, injected = sanitize(email["body"])
    if injected:
        notes.append("prompt-injection text removed and ignored")
    cls, flags = classify_heuristic(email, clean)
    if backend == "anthropic":
        try:
            cls = classify_llm(email, clean, root)
        except Exception as e:  # fall back, never drop the email
            error = f"llm_failed: {e}"
            notes.append("fell back to heuristic classifier")
    errs = validate(cls)
    if errs:
        error = "schema: " + ",".join(errs)

    intent, lang, first = cls["intent"], cls["language"], first_name(email["from"]["name"])
    order, found, mismatch = None, False, False
    needs_order = intent in ("order_status", "return_refund", "complaint")
    if needs_order:
        if cls["order_id"]:
            order = orders.get(cls["order_id"])
        elif sender in by_email:
            order = by_email[sender]
            notes.append(f"order {order['order_id']} found via sender email")
        if order:
            found = True
            if order["customer_email"].lower() != sender:
                mismatch, order = True, None
                notes.append("sender email does not match order owner; no order details shared")
    row.update(intent=intent, secondary_intent=cls["secondary_intent"] or "", language=lang,
               sentiment=cls["sentiment"], confidence=cls["confidence"], order_found="yes" if found else "no")
    row["order_id"] = (order["order_id"] if order else cls["order_id"]) or ""

    needs_human, actions, reply, tid = False, [], None, ""
    low_conf = cls["confidence"] < CONF_THRESHOLD
    if injected:
        needs_human = True
    if cls["sentiment"] == "angry":
        needs_human = True
    if low_conf:
        needs_human = True
        notes.append(f"confidence {cls['confidence']} below {CONF_THRESHOLD}")

    if mismatch:
        needs_human, reply = True, reply_privacy(first)
        notes.append("privacy check failed")
    elif needs_order and not order:
        needs_human = True
        reply = reply_not_found(first, cls["order_id"])
        notes.append("order not found" if cls["order_id"] else "no order ID and sender email matches no order")
    elif intent == "order_status":
        reply = reply_order_status(first, order, lang)
        if reply is None:
            needs_human = True
    elif intent == "return_refund":
        eligible, reasons = return_eligibility(order, received_date, clean)
        notes.append("eligibility: " + "; ".join(reasons))
        if not eligible:
            needs_human = True  # a human reviews every denial
        tid, act = tickets.upsert(order["order_id"], sender, email["id"], intent, "Medium",
                                  cls["summary"], ("eligible, " if eligible else "NOT eligible, ") + "; ".join(reasons), ts)
        actions.append(act)
        reply = reply_return(first, order, eligible, reasons)
    elif intent == "complaint":
        kind = flags["damage"] or "damaged"
        in_window = damage_window(order, received_date)
        photos = bool(email["attachments"])
        notes.append(f"report window <=48h: {in_window}; photos attached: {photos}")
        if not in_window:
            notes.append("outside 48h window: human decision")
        needs_human = True  # claims are verified by a person before replacement/refund
        tid, act = tickets.upsert(order["order_id"], sender, email["id"], intent, "High", cls["summary"],
                                  f"{kind}; photos {'received' if photos else 'requested'}", ts)
        actions.append(act)
        reply = reply_complaint(first, order, kind, photos, None, cls["sentiment"] == "angry")
    elif intent == "product_query":
        if flags["medical"]:
            needs_human = True
        reply = reply_product(first, clean, lang, flags)
        if reply is None:
            needs_human = True
            notes.append("question not answerable from faq.md")
    else:  # other
        reply = reply_other(first, flags)
        if flags["topic"] == "invoice":
            needs_human = True
            notes.append("not covered by faq.md")
        if reply is None and flags["topic"] not in ("spam", "thanks"):
            needs_human = True

    if reply:
        actions.append("draft_reply")
    else:
        actions.append("log_only")
    if needs_human:
        actions.append("needs_human")
    row.update(action_taken="+".join(actions), needs_human="TRUE" if needs_human else "FALSE", ticket_id=tid,
               reply_draft=reply or "", error=error, notes="; ".join(notes),
               processed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return row, cls


def run(data, out, backend, expected):
    root = Path(__file__).resolve().parent.parent
    orders_raw = json.loads((data / "orders.json").read_text(encoding="utf-8"))["orders"]
    orders = {o["order_id"]: o for o in orders_raw}
    by_email = {o["customer_email"].lower(): o for o in orders_raw}
    emails = json.loads((data / "test_emails.json").read_text(encoding="utf-8"))["emails"]
    tickets, rows = Tickets(), []
    for e in emails:
        row, _ = process(e, orders, by_email, tickets, backend, root)
        rows.append(row)
    out.mkdir(parents=True, exist_ok=True)
    for name, cols, data_rows in (("audit_log.csv", AUDIT_COLS, rows), ("tickets.csv", TICKET_COLS, tickets.rows)):
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(data_rows)
    print(f"{len(rows)} emails processed, {len(tickets.rows)} tickets -> {out}")
    if expected:
        exp = json.loads(Path(expected).read_text(encoding="utf-8"))
        ok_i = ok_h = ok_t = ok_a = 0
        for r in rows:
            x = exp[r["email_id"]]
            hit_i = r["intent"] == x["intent"]
            hit_h = (r["needs_human"] == "TRUE") == x["needs_human"]
            has_t = "ticket" in r["action_taken"]
            hit_t = has_t == x["ticket"]
            ok_i += hit_i
            ok_h += hit_h
            ok_t += hit_t
            ok_a += hit_i and hit_h and hit_t
            if not (hit_i and hit_h and hit_t):
                print("MISMATCH", r["email_id"], r["intent"], r["needs_human"], r["action_taken"])
        n = len(rows)
        print(f"intent {ok_i}/{n}  needs_human {ok_h}/{n}  ticket {ok_t}/{n}  all-three {ok_a}/{n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="output")
    ap.add_argument("--llm", default="heuristic", choices=["heuristic", "anthropic"])
    ap.add_argument("--eval", default=None)
    a = ap.parse_args()
    run(Path(a.data), Path(a.out), a.llm, a.eval)
