import json
from pathlib import Path
import app

def test_schema_fallback_valid():
    c=app.classify_rules({"subject":"Where is my order KC-1041","body":"","from":{"email":"x@example.com"}})
    from jsonschema import validate
    validate(c,app.SCHEMA)

def test_order_lookup_by_email():
    assert app.order_by_email("ananya.rao@example.com")["order_id"]=="KC-1041"

def test_privacy_gate():
    o=app.order_by_id("KC-1041")
    assert app.get_order("KC-1041","attacker@example.com")=={"found":True,"authorized":False,"order_id":"KC-1041"}

def test_faq_unknown_escalates():
    assert "No matching FAQ" in app.search_faq("what is the CEO's birthday")

def test_ticket_dedup(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"ROOT",tmp_path)
    e={"id":"E1","from":{"email":"customer@example.com"}}
    c={"order_id":"KC-1234","intent":"complaint","sentiment":"angry","summary":"Leaking bottle"}
    t1=app.ticket_upsert(e,c)
    e2={"id":"E2","from":{"email":"customer@example.com"}}
    t2=app.ticket_upsert(e2,c)
    assert t1==t2
    import csv
    rows=list(csv.DictReader((tmp_path/"tickets.csv").open()))
    assert len(rows)==1 and rows[0]["email_ids"]=="E1;E2"
