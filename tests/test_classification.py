import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("WEBHOOK_SECRET", "test_secret_12345678901234567890")
os.environ.setdefault("AUTHORIZED_USER_IDS", "12345")
from app.main import classify

def test_first_before_link():
    assert classify("First https://x.com/user/status/123") == "first"

def test_second_after_link():
    assert classify("https://twitter.com/user/status/123 second") == "second"

def test_unmarked_link():
    assert classify("https://x.com/user/status/123") == "unspecified"

def test_both_markers():
    assert classify("first https://x.com/user/status/123 second") == "unspecified"

def test_non_x_url_ignored():
    assert classify("first https://example.com/post") is None

def test_plain_text_ignored():
    assert classify("I did my first task") is None

def test_hidden_x_link_entity():
    assert classify("See this", [{"type": "text_link", "url": "https://x.com/u/status/1"}]) == "unspecified"

def test_hidden_non_x_link_ignored():
    assert classify("See this", [{"type": "text_link", "url": "https://example.com/"}]) is None
