import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel


START_TIME = time.time()
APP_VERSION = "0.1.0"
SUBMITTED_AT = datetime.now(timezone.utc).isoformat()
VALID_SCOPES = {"category", "merchant", "customer", "trigger"}
LLM_POLISH_ENABLED = os.getenv("LLM_POLISH_ENABLED", "0") != "0"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "8"))
POLISH_TRIGGER_KINDS = {
    "research_digest",
    "regulation_change",
    "perf_dip",
    "curious_ask_due",
    "winback_eligible",
    "review_theme_emerged",
    "milestone_reached",
    "seasonal_perf_dip",
    "supply_alert",
    "gbp_unverified",
    "competitor_opened",
    "perf_spike",
    "dormant_with_vera",
}
POLISH_CACHE: dict[str, str] = {}
AUTO_REPLY_PATTERNS = [
    "thank you for contacting",
    "our team will respond shortly",
    "we will get back to you",
    "automated assistant",
    "auto reply",
    "auto-reply",
    "this is an automated",
]
NEGATIVE_PATTERNS = [
    "stop messaging",
    "not interested",
    "don't message",
    "do not message",
    "useless spam",
    "stop this",
    "unsubscribe",
]
HOSTILE_PATTERNS = ["useless", "spam", "bothering me", "waste of time", "annoying"]
INTENT_PATTERNS = [
    "yes",
    "lets do it",
    "let's do it",
    "go ahead",
    "proceed",
    "confirm",
    "what's next",
    "whats next",
    "send it",
    "do it",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pct(value: Optional[float], digits: int = 0) -> str:
    if value is None:
        return ""
    return f"{value * 100:.{digits}f}%"


def title_from_kind(kind: str) -> str:
    return kind.replace("_", " ").title()


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


PRETTY_TEXT_MAP = {
    "review_count": "reviews",
    "delivery_late": "late delivery",
    "weight_loss": "weight loss",
    "free_for_members": "free for members",
    "postcard_or_phone_call": "postcard or phone call",
    "kids_yoga_post": "the kids yoga post",
    "subscription_expiry": "subscription expiry",
}


def humanize_text(value: Optional[str], *, title_case: bool = False) -> str:
    if not value:
        return ""
    text = PRETTY_TEXT_MAP.get(value, value.replace("_", " "))
    text = compact_whitespace(text)
    return text.title() if title_case else text


def split_template_params(body: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", body) if part.strip()]
    if not parts:
        return [body[:60]]
    return parts[:3]


def format_original_iso_time(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.strftime("%I:%M%p").lstrip("0").lower()


def make_conversation_id(trigger: dict[str, Any], merchant: dict[str, Any], customer: dict[str, Any] | None) -> str:
    merchant_part = merchant.get("merchant_id", "merchant")
    customer_part = customer.get("customer_id") if customer else None
    trigger_part = trigger.get("id", trigger.get("kind", "trigger"))
    pieces = ["conv", merchant_part]
    if customer_part:
        pieces.append(customer_part)
    pieces.append(trigger_part)
    return "_".join(re.sub(r"[^a-zA-Z0-9]+", "_", piece).strip("_") for piece in pieces if piece)


def active_offers(merchant: dict[str, Any]) -> list[dict[str, Any]]:
    return [offer for offer in merchant.get("offers", []) if offer.get("status") == "active"]


def best_offer_title(category: dict[str, Any], merchant: dict[str, Any]) -> Optional[str]:
    offers = active_offers(merchant)
    if offers:
        return offers[0].get("title")
    catalog = category.get("offer_catalog", [])
    if catalog:
        return catalog[0].get("title")
    return None


def owner_name(merchant: dict[str, Any]) -> str:
    identity = merchant.get("identity", {})
    first_name = identity.get("owner_first_name")
    if first_name:
        if merchant.get("category_slug") == "dentists":
            return f"Dr. {first_name}"
        return first_name
    return identity.get("name", "there")


def merchant_name_for_customer(merchant: dict[str, Any]) -> str:
    return merchant.get("identity", {}).get("name", "the clinic")


def customer_prefers_hinglish(customer: dict[str, Any] | None) -> bool:
    if not customer:
        return False
    pref = customer.get("identity", {}).get("language_pref", "").lower()
    return "hi-en" in pref or "hinglish" in pref


def find_digest_item(category: dict[str, Any], item_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not item_id:
        return None
    for item in category.get("digest", []):
        if item.get("id") == item_id:
            return item
    return None


def find_review_theme(merchant: dict[str, Any], theme_name: Optional[str]) -> Optional[dict[str, Any]]:
    if not theme_name:
        return None
    for theme in merchant.get("review_themes", []):
        if theme.get("theme") == theme_name:
            return theme
    return None


def category_peer_anchor(category: dict[str, Any], metric: str) -> Optional[str]:
    peer = category.get("peer_stats", {})
    if metric == "ctr" and peer.get("avg_ctr") is not None:
        return pct(peer["avg_ctr"], 1)
    if metric == "rating" and peer.get("avg_rating") is not None:
        return str(peer["avg_rating"])
    if metric == "reviews" and peer.get("avg_reviews") is not None:
        return str(int(peer["avg_reviews"]))
    return None


def peer_social_proof(category: dict[str, Any], merchant: dict[str, Any]) -> str:
    peer = category.get("peer_stats", {})
    locality = merchant.get("identity", {}).get("locality", "your area")
    scope = peer.get("scope", locality)
    ctr = peer.get("avg_ctr")
    reviews = peer.get("avg_reviews")
    merchant_ctr = merchant.get("performance", {}).get("ctr")
    if ctr and merchant_ctr and merchant_ctr < ctr:
        return f"Peers in {scope} average {pct(ctr, 1)} CTR vs your {pct(merchant_ctr, 1)}. "
    if reviews:
        return f"The peer median in {scope} is {int(reviews)} reviews. "
    return ""


def extract_recent_history_snippet(merchant: dict[str, Any], keyword: str | None = None) -> Optional[str]:
    history = merchant.get("conversation_history", [])
    for item in reversed(history):
        body = item.get("body", "")
        if not body:
            continue
        if keyword is None or keyword.lower() in body.lower():
            return body
    return None


def metric_value(merchant: dict[str, Any], metric: str) -> Optional[Any]:
    return merchant.get("performance", {}).get(metric)


def first_slot_label(trigger: dict[str, Any]) -> Optional[str]:
    slots = trigger.get("payload", {}).get("available_slots") or trigger.get("payload", {}).get("next_session_options")
    if slots:
        return slots[0].get("label")
    return None


def second_slot_label(trigger: dict[str, Any]) -> Optional[str]:
    slots = trigger.get("payload", {}).get("available_slots") or trigger.get("payload", {}).get("next_session_options")
    if slots and len(slots) > 1:
        return slots[1].get("label")
    return None


def is_auto_reply(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in AUTO_REPLY_PATTERNS)


def is_negative(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in NEGATIVE_PATTERNS)


def is_hostile(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in HOSTILE_PATTERNS)


def is_positive_intent(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in INTENT_PATTERNS)


def template_name_for_kind(kind: str, scope: str) -> str:
    prefix = "merchant" if scope == "customer" else "vera"
    return f"{prefix}_{kind}_v1"


def safe_rationale(text: str) -> str:
    return compact_whitespace(text)[:280]


def short_list(items: list[str], limit: int = 3) -> str:
    picked = [item for item in items if item][:limit]
    return ", ".join(picked)


def style_prefix(category_slug: str, merchant: dict[str, Any], customer: dict[str, Any] | None = None) -> str:
    if customer:
        customer_name = customer.get("identity", {}).get("name", "there")
        business = merchant_name_for_customer(merchant)
        if customer_prefers_hinglish(customer):
            return f"Hi {customer_name}, {business} here."
        return f"Hi {customer_name}, {business} here."
    name = owner_name(merchant)
    if category_slug == "dentists":
        return f"{name},"
    if category_slug in {"restaurants", "pharmacies"}:
        return f"{name}, quick heads-up:"
    return f"Hi {name},"


def build_polish_prompt(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
    draft: str,
) -> str:
    identity = merchant.get("identity", {})
    performance = merchant.get("performance", {})
    aggregate = merchant.get("customer_aggregate", {})
    offers = [offer.get("title", "") for offer in active_offers(merchant)]
    digest_ids = trigger.get("payload", {})
    prompt = {
        "task": "Rewrite the draft WhatsApp message to improve merchant fit and reply likelihood without changing the factual meaning.",
        "rules": [
            "Return only the final message body, no quotes, no markdown, no explanation.",
            "Keep all factual claims grounded in the provided context.",
            "Do not invent new numbers, new offers, new sources, new competitors, or new timings.",
            "Keep it concise: ideally 45-75 words.",
            "Use one clear CTA, ideally a low-friction YES-style ask when the trigger is action-oriented.",
            "Match the category voice. Dentists should sound peer-clinical, not promotional.",
            "Hindi-English mix is allowed only if the merchant or customer context supports it.",
            "No URLs.",
        ],
        "context": {
            "category": category.get("slug"),
            "voice_tone": category.get("voice", {}).get("tone"),
            "merchant_name": identity.get("name"),
            "owner_name": identity.get("owner_first_name"),
            "locality": identity.get("locality"),
            "languages": identity.get("languages", []),
            "signals": merchant.get("signals", [])[:5],
            "active_offers": offers,
            "performance": {
                "views": performance.get("views"),
                "calls": performance.get("calls"),
                "ctr": performance.get("ctr"),
            },
            "customer_aggregate": aggregate,
            "trigger_kind": trigger.get("kind"),
            "trigger_payload": digest_ids,
            "customer_language_pref": customer.get("identity", {}).get("language_pref") if customer else None,
        },
        "draft": draft,
    }
    return json.dumps(prompt, ensure_ascii=False)


def maybe_polish_body(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
    draft: str,
) -> str:
    if not LLM_POLISH_ENABLED:
        return draft
    if customer is not None:
        return draft
    if trigger.get("kind") not in POLISH_TRIGGER_KINDS:
        return draft

    cache_key = hashlib.sha1(
        json.dumps(
            {
                "category": category.get("slug"),
                "merchant_id": merchant.get("merchant_id"),
                "trigger_id": trigger.get("id"),
                "draft": draft,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    cached = POLISH_CACHE.get(cache_key)
    if cached:
        return cached

    prompt = build_polish_prompt(category, merchant, trigger, customer, draft)
    body = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 140},
        }
    ).encode("utf-8")
    req = urlrequest.Request(
        f"{OLLAMA_URL}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urlrequest.urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS)
        payload = json.loads(resp.read().decode("utf-8"))
        polished = compact_whitespace(payload.get("response", ""))
    except (urlerror.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return draft

    if not polished or len(polished) < 20:
        return draft
    if any(token in polished.lower() for token in ["here is the revised", "rewritten", "explanation:"]):
        return draft

    draft_numbers = re.findall(r"\d[\d.,%]*", draft)
    polished_numbers = re.findall(r"\d[\d.,%]*", polished)
    if draft_numbers and not polished_numbers:
        return draft

    POLISH_CACHE[cache_key] = polished
    return polished


def compose_research_digest(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    item = find_digest_item(category, trigger.get("payload", {}).get("top_item_id")) or {}
    prefix = style_prefix(category.get("slug", ""), merchant)
    title = item.get("title", "this week's category digest landed")
    source = item.get("source")
    trial_n = item.get("trial_n")
    actionable = item.get("actionable")
    cohort = merchant.get("customer_aggregate", {}).get("high_risk_adult_count")
    cohort_text = f"your {cohort} high-risk adult patients" if cohort else "your higher-risk patients"
    if trial_n:
        body = (
            f"{prefix} {title}. For {cohort_text}, the strongest hook is this: "
            f"{trial_n:,}-patient data supports the change. "
            f"{actionable}. "
            f"I can draft one 90-second patient WhatsApp you can use this week. Reply YES? "
            f"{source}"
        )
    else:
        body = (
            f"{prefix} {title}. This looks relevant for {cohort_text}. "
            f"{actionable or 'Worth a quick review before your next patient-education push.'} "
            f"I can turn it into one short patient explainer for this week. Reply YES? {source or ''}"
        )
    rationale = "Research-led message anchored in category digest and merchant-specific cohort, with a low-friction follow-up artifact."
    return compact_whitespace(body), rationale


def compose_regulation_change(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    item = find_digest_item(category, trigger.get("payload", {}).get("top_item_id")) or {}
    deadline = trigger.get("payload", {}).get("deadline_iso") or trigger.get("expires_at")
    deadline_dt = parse_dt(deadline)
    deadline_text = deadline_dt.strftime("%d %b %Y") if deadline_dt else "the stated deadline"
    prefix = style_prefix(category.get("slug", ""), merchant)
    locality = merchant.get("identity", {}).get("locality", "your clinic")
    summary = item.get("summary", "")
    last_touch = extract_recent_history_snippet(merchant, "post")
    body = (
        f"{prefix} compliance heads-up: {item.get('title', 'a regulation update landed')}. "
        f"Deadline: {deadline_text}. "
        f"{summary + ' ' if summary else ''}"
        f"{item.get('actionable', 'Worth checking your SOP and setup now rather than later.')}. "
        f"{f'Since you were already updating posts recently, I can keep this short for the {locality} team. ' if last_touch else ''}"
        f"I can turn it into a 3-point clinic checklist you can action in 5 minutes. Reply YES? {item.get('source', '')}"
    )
    rationale = "Compliance-triggered message with deadline, source anchor, and practical next step."
    return compact_whitespace(body), rationale


def compose_perf_dip(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    metric = trigger.get("payload", {}).get("metric", "performance")
    delta_pct = trigger.get("payload", {}).get("delta_pct")
    baseline = trigger.get("payload", {}).get("vs_baseline")
    current = metric_value(merchant, metric)
    peer_ctr = category_peer_anchor(category, "ctr")
    prefix = style_prefix(category.get("slug", ""), merchant)
    lapsed = merchant.get("customer_aggregate", {}).get("lapsed_180d_plus")
    locality = merchant.get("identity", {}).get("locality", "your locality")
    no_offer = "no_active_offers" in merchant.get("signals", [])
    unverified = not merchant.get("identity", {}).get("verified", True)
    social = peer_social_proof(category, merchant)
    body = (
        f"{prefix} your {metric} are down {pct(abs(delta_pct), 0)} in the last {trigger.get('payload', {}).get('window', '7d')} "
        f"vs your usual baseline of {baseline}. Current 30-day {metric}: {current}. "
        f"{social}"
        f"{f'Meanwhile {lapsed} of your patients have lapsed in {locality}. Every week without a recovery move widens that gap. ' if lapsed else ''}"
        f"{'Your GBP is still unverified — that alone costs you trust. ' if unverified else ''}"
        f"{'You also have no active offer visible right now. ' if no_offer else ''}"
        f"I've already identified the single highest-impact fix. Want to see it? Reply YES."
    )
    rationale = "Performance dip uses loss aversion (widening gap), peer social proof, and curiosity (want to see the fix) to drive reply."
    return compact_whitespace(body), rationale


def compose_renewal_due(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    calls = merchant.get("performance", {}).get("calls")
    baseline_calls = merchant.get("performance", {}).get("delta_7d", {}).get("calls_pct")
    body = (
        f"{prefix} your {payload.get('plan', merchant.get('subscription', {}).get('plan', 'current'))} plan has "
        f"{payload.get('days_remaining', merchant.get('subscription', {}).get('days_remaining', '?'))} days left. "
        f"Renewal amount is ₹{payload.get('renewal_amount', '0')}. "
        f"{f'Current 30-day calls are only {calls}. ' if calls is not None else ''}"
        f"{'You do not have an active visible offer live right now. ' if not active_offers(merchant) else ''}"
        f"I can draft one renewal summary plus the first recovery move to justify it. Reply YES?"
    )
    rationale = "Renewal prompt keeps the ask transactional and specific instead of slipping back into re-qualification."
    return compact_whitespace(body), rationale


def compose_festival(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    offer = best_offer_title(category, merchant)
    prefix = style_prefix(category.get("slug", ""), merchant)
    body = (
        f"{prefix} {payload.get('festival', 'festival season')} is {payload.get('days_until', '?')} days away, so this is planning time, not blast time. "
        f"For {merchant.get('identity', {}).get('locality', 'your locality')}, the sharper move is to save one festive draft now and keep {offer or 'your strongest service+price hook'} doing the current work. "
        f"I can draft the Diwali version and park it for later. Reply YES?"
    )
    rationale = "Seasonal message uses event timing plus the merchant's actual offer inventory to create a ready-to-send artifact."
    return compact_whitespace(body), rationale


def compose_curious_ask(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    prefix = style_prefix(category.get("slug", ""), merchant)
    offers = active_offers(merchant)
    guess = short_list([offer.get("title", "") for offer in offers], limit=2)
    last_search_signal = extract_recent_history_snippet(merchant, "searches")
    social = peer_social_proof(category, merchant)
    body = (
        f"{prefix} quick check: what service has been most asked for this week at {merchant.get('identity', {}).get('name')}? "
        f"{f'If I had to guess, is it {guess}? ' if guess else ''}"
        f"{'You were already seeing bridal-trial search momentum recently. ' if last_search_signal else ''}"
        f"{social}"
        f"I'll turn your answer into one Google post plus one pricing reply you can reuse this week — ready in 2 minutes. Reply with just the service name."
    )
    rationale = "Curiosity-led prompt with social proof anchor invites a lightweight reply and offers immediate work output with effort externalization."
    return compact_whitespace(body), rationale


def compose_winback_eligible(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    offer = best_offer_title(category, merchant)
    lapsed_pool = merchant.get("customer_aggregate", {}).get("lapsed_90d_plus")
    perf_drop = pct(abs(payload.get("perf_dip_pct")), 0)
    body = (
        f"{prefix} it's been {payload.get('days_since_expiry', '?')} days since your last plan expired, and "
        f"{payload.get('lapsed_customers_added_since_expiry', '?')} more customers have gone inactive since then — that number grows every week you wait. "
        f"Calls are also down about {perf_drop}. "
        f"{f'Your total lapsed pool is already {lapsed_pool}. ' if lapsed_pool else ''}"
        f"I've already drafted a winback push around {offer or 'a single service+price offer'} for your old customers. Want to see it? Reply YES."
    )
    rationale = "Winback uses loss aversion (grows every week), curiosity (want to see it), and effort externalization (already drafted)."
    return compact_whitespace(body), rationale


def compose_ipl(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    offer = best_offer_title(category, merchant)
    match_time = format_original_iso_time(payload.get("match_time_iso")) or "tonight"
    prefix = style_prefix(category.get("slug", ""), merchant)
    delivery_orders = merchant.get("customer_aggregate", {}).get("delivery_orders_30d")
    dine_in_orders = merchant.get("customer_aggregate", {}).get("dine_in_orders_30d")
    body = (
        f"{prefix} {payload.get('match')} is at {payload.get('venue')} {match_time}. "
        f"{f'You already do {delivery_orders} delivery orders vs {dine_in_orders} dine-in orders in 30 days. ' if delivery_orders and dine_in_orders else ''}"
        f"{'Saturday matches usually shift demand toward home orders.' if not payload.get('is_weeknight') else 'Weeknight matches usually pull footfall down.'} "
        f"Skip a generic match promo and push {offer or 'your strongest delivery offer'} as the sharper angle. "
        f"I can draft the banner copy now. Reply YES?"
    )
    rationale = "Event-triggered recommendation adds operator judgment and channels the merchant toward an offer that fits match-day behavior."
    return compact_whitespace(body), rationale


def compose_review_theme(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    theme = find_review_theme(merchant, payload.get("theme")) or {}
    prefix = style_prefix(category.get("slug", ""), merchant)
    offer = best_offer_title(category, merchant)
    delivery_orders = merchant.get("customer_aggregate", {}).get("delivery_orders_30d")
    body = (
        f"{prefix} {payload.get('occurrences_30d', '?')} recent reviews mention {humanize_text(payload.get('theme', 'one recurring issue'))}, and it's still rising. "
        f"{f'With {delivery_orders} delivery orders in the last 30 days, each negative review costs you more than a low-volume place. ' if delivery_orders else ''}"
        f"Most repeated phrasing: \"{theme.get('common_quote', payload.get('common_quote', ''))}\". "
        f"Good news: your food quality is still being praised, so this is an ops fix, not a menu problem. "
        f"I've already drafted a reply template that addresses this and protects {offer or 'your current offer'}. Want to see it? Reply YES."
    )
    rationale = "Review-theme uses loss aversion (costs you more), curiosity (want to see the draft), and effort externalization (already drafted)."
    return compact_whitespace(body), rationale


def compose_milestone(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    locality = merchant.get("identity", {}).get("locality", "your locality")
    active_offer = best_offer_title(category, merchant)
    gap = max(0, payload.get("milestone_value", 0) - payload.get("value_now", 0))
    peer_reviews = category_peer_anchor(category, "reviews")
    milestone = payload.get('milestone_value', '?')
    body = (
        f"{prefix} you're at {payload.get('value_now', '?')} {humanize_text(payload.get('metric', 'reviews'))} and only "
        f"{gap} away from {milestone}. "
        f"{f'The peer median in {locality} is {peer_reviews} — crossing {milestone} puts you ahead. ' if peer_reviews else f'In {locality}, crossing that mark is a clean trust signal. '}"
        f"{f'Best moment to ask is right after customers redeem {active_offer}. ' if active_offer else ''}"
        f"I've already drafted a 2-line review ask that feels natural, not needy. Want to see it? Reply YES."
    )
    rationale = "Milestone uses social proof (peer median), loss aversion (puts you ahead), curiosity (see the draft), and effort externalization."
    return compact_whitespace(body), rationale


def compose_active_planning(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    topic = trigger.get("payload", {}).get("intent_topic", "the plan you asked about")
    prefix = style_prefix(category.get("slug", ""), merchant)
    if "corporate_bulk_thali" in topic:
        history_snippet = extract_recent_history_snippet(merchant, "18 orders/day")
        body = (
            f"{prefix} good idea. Since your weekday thali is already doing about 18 orders/day, I'd start the corporate version with 10, 25, and 50+ slabs plus one fixed lunch delivery window for Indiranagar offices. "
            f"I can draft the exact menu and price ladder now. Reply YES?"
        )
    elif "kids_yoga" in topic:
        body = (
            f"{prefix} nice direction. For a kids yoga summer camp, I'd start with a 4-week format, two age bands, and one parent-trial message before launch. "
            f"I can draft the timetable and pricing frame in one shot. Reply YES?"
        )
    else:
        body = (
            f"{prefix} understood. I can turn {topic.replace('_', ' ')} into a concrete first draft instead of another brainstorm round. "
            f"Reply YES and I'll send the actual offer structure."
        )
    rationale = "Explicit merchant intent is routed straight into action mode instead of more qualifying questions."
    return compact_whitespace(body), rationale


def compose_seasonal_perf_dip(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    active_members = merchant.get("customer_aggregate", {}).get("total_active_members")
    trial_to_paid = merchant.get("customer_aggregate", {}).get("trial_to_paid_pct")
    offer = best_offer_title(category, merchant)
    body = (
        f"{prefix} your {payload.get('metric', 'views')} are down {pct(abs(payload.get('delta_pct')), 0)} this week, but this looks seasonal rather than structural. "
        f"Apr-Jun is usually a softer acquisition window here. "
        f"{'The smart move right now is retention over acquisition — the gyms that grow through summer are the ones that keep their base. ' if payload.get('is_expected_seasonal') else ''}"
        f"{f'You still have {active_members} active members and a {pct(trial_to_paid,0)} trial-to-paid rate — that is your strongest asset right now. ' if active_members and trial_to_paid is not None else ''}"
        f"I've already drafted a 7-day attendance challenge to protect member retention. Want to see it? Reply YES."
    )
    rationale = "Seasonal dip uses social proof (smart gyms do X), effort externalization (already drafted), and curiosity (want to see it)."
    return compact_whitespace(body), rationale


def compose_supply_alert(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    batches = ", ".join(payload.get("affected_batches", []))
    chronic_count = merchant.get("customer_aggregate", {}).get("chronic_rx_count")
    body = (
        f"{prefix} urgent: {payload.get('molecule')} batches {batches} from {payload.get('manufacturer')} are under recall review. "
        f"{f'You have about {chronic_count} chronic-Rx customers — even a small affected subset puts trust at risk if you are not the first to tell them. ' if chronic_count else ''}"
        f"Pharmacies that communicate recalls proactively retain more trust than those customers hear about it elsewhere. "
        f"I've already drafted the customer note plus the replacement-pickup workflow. Want to see it? Reply YES."
    )
    rationale = "Supply alert uses loss aversion (trust at risk), social proof (proactive pharmacies retain trust), and effort externalization (already drafted)."
    return compact_whitespace(body), rationale


def compose_category_seasonal(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    trend = ", ".join(payload.get("trends", [])[:3]).replace("_", " ")
    prefix = style_prefix(category.get("slug", ""), merchant)
    body = (
        f"{prefix} summer demand is shifting fast: {trend}. "
        f"This is a good shelf and WhatsApp moment for the categories already moving, not the ones cooling off. "
        f"I can draft a 3-item summer push using your delivery and repeat-customer strengths. Reply YES?"
    )
    rationale = "Seasonal category message converts trend signals into a merchant-facing merchandising action."
    return compact_whitespace(body), rationale


def compose_gbp_unverified(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    uplift = pct(payload.get("estimated_uplift_pct"), 0)
    views = merchant.get("performance", {}).get("views")
    calls = merchant.get("performance", {}).get("calls")
    no_offer = "no_active_offers" in merchant.get("signals", [])
    locality = merchant.get("identity", {}).get("locality", "your locality")
    body = (
        f"{prefix} your Google profile is still unverified, which means you are missing out on trust and discovery compared to verified competitors in {locality}. "
        f"They typically see around {uplift} more traffic. "
        f"{f'Right now you are getting {views} views but only {calls} calls in 30 days, which is a missed opportunity. ' if views is not None and calls is not None else ''}"
        f"{'You also do not have an active visible offer yet, so verification is the cleanest next bottleneck to clear. ' if no_offer else ''}"
        f"Path available: {humanize_text(payload.get('verification_path', 'standard verification'))}. "
        f"I can send the 3 exact steps to get verified now, taking just 5 minutes. Reply YES?"
    )
    rationale = "Verification message leverages loss aversion and social proof while maintaining a low-friction 5-minute CTA."
    return compact_whitespace(body), rationale


def compose_cde_opportunity(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    item = find_digest_item(category, trigger.get("payload", {}).get("digest_item_id")) or {}
    prefix = style_prefix(category.get("slug", ""), merchant)
    body = (
        f"{prefix} one CDE item worth your attention: {item.get('title')}. "
        f"{trigger.get('payload', {}).get('credits', 0)} credits, {humanize_text(trigger.get('payload', {}).get('fee', 'check fee details'))}. "
        f"Especially useful if you are actively discussing aligners or digital workflow. "
        f"I can pull the 3 takeaways most relevant to your clinic. Reply YES?"
    )
    rationale = "CDE message ties professional development to merchant-facing growth output, which makes the invite more compelling."
    return compact_whitespace(body), rationale


def compose_competitor_opened(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    my_offer = best_offer_title(category, merchant)
    locality = merchant.get("identity", {}).get("locality", "your area")
    cohort = merchant.get("customer_aggregate", {}).get("high_risk_adult_count")
    body = (
        f"{prefix} a new competitor just opened {payload.get('distance_km')} km away: {payload.get('competitor_name')}. "
        f"They're leading with {payload.get('their_offer')}. "
        f"In {locality}, that will pull attention fast unless your hook is sharper. "
        f"{f'You have {cohort} high-risk adults in your base already — trust and recall matter more than a price race. ' if cohort else ''}"
        f"I've already drafted comparison-safe positioning copy around {my_offer or 'your strongest service+price hook'}. Want to see the side-by-side? Reply YES."
    )
    rationale = "Competitor alert uses loss aversion (pull attention), curiosity (see the side-by-side), and effort externalization (already drafted)."
    return compact_whitespace(body), rationale


def compose_perf_spike(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    trial_to_paid = merchant.get("customer_aggregate", {}).get("trial_to_paid_pct")
    offer = best_offer_title(category, merchant)
    body = (
        f"{prefix} your {payload.get('metric', 'calls')} are up {pct(payload.get('delta_pct'), 0)} this week vs a baseline of {payload.get('vs_baseline', '?')}. "
        f"Likely driver: {humanize_text(payload.get('likely_driver', 'recent content or offer activity'))}. "
        f"{f'Your trial-to-paid rate is already {pct(trial_to_paid,0)} — compounding this spike now could lock in more paid members before it fades. ' if trial_to_paid is not None else ''}"
        f"I've already drafted the next post to double down on {offer or 'your active hook'} while the momentum is fresh. Want to see it? Reply YES."
    )
    rationale = "Performance spike uses loss aversion (before it fades), curiosity (want to see the draft), and effort externalization (already drafted)."
    return compact_whitespace(body), rationale


def compose_dormant(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant)
    lapsed_pool = merchant.get("customer_aggregate", {}).get("lapsed_90d_plus") or merchant.get("customer_aggregate", {}).get("lapsed_180d_plus")
    offer = best_offer_title(category, merchant)
    locality = merchant.get("identity", {}).get("locality", "your locality")
    social = peer_social_proof(category, merchant)
    body = (
        f"{prefix} it's been {payload.get('days_since_last_merchant_message', '?')} days since we last spoke — last topic was {humanize_text(payload.get('last_topic', 'the previous task'))}. "
        f"{f'Since then, {lapsed_pool} more customers have gone idle in {locality}. Every week without a push widens the gap. ' if lapsed_pool else ''}"
        f"{social}"
        f"I've already picked the single best next move and drafted it around {offer or 'one clean offer angle'}. Want to see it? Reply YES."
    )
    rationale = "Dormancy nudge uses loss aversion (gap widens every week), peer social proof, curiosity (want to see it), and effort externalization (already drafted)."
    return compact_whitespace(body), rationale


def compose_recall_due(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any], customer: dict[str, Any]) -> tuple[str, str]:
    offer = best_offer_title(category, merchant) or "your recall visit"
    slot1 = first_slot_label(trigger)
    slot2 = second_slot_label(trigger)
    prefix = style_prefix(category.get("slug", ""), merchant, customer)
    if customer_prefers_hinglish(customer):
        body = (
            f"{prefix} It's been a while since your last visit, and your 6-month cleaning recall is due. "
            f"Apke liye 2 slots ready hain: {slot1} ya {slot2}. "
            f"{offer}. Reply 1 for the first slot, 2 for the second, or send a time that works better."
        )
    else:
        body = (
            f"{prefix} your 6-month recall is due. "
            f"We have {slot1} or {slot2} ready for you. "
            f"{offer}. Reply 1 for the first slot, 2 for the second, or send a better time."
        )
    rationale = "Customer recall message uses name, timing, actual slots, and a low-friction booking CTA."
    return compact_whitespace(body), rationale


def compose_wedding_followup(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any], customer: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    offer = best_offer_title(category, merchant) or "a bridal skin-prep package"
    prefix = style_prefix(category.get("slug", ""), merchant, customer)
    body = (
        f"{prefix} {payload.get('days_to_wedding', '?')} days to the wedding is a good time to start the next prep step. "
        f"After your trial, the right move now is a structured skin-prep window rather than waiting for the final rush. "
        f"{offer}. Want me to hold your first consultation slot?"
    )
    rationale = "Bridal follow-up message uses timing, continuity from trial, and a single next-step ask."
    return compact_whitespace(body), rationale


def compose_customer_lapsed_hard(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any], customer: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    prefix = style_prefix(category.get("slug", ""), merchant, customer)
    body = (
        f"{prefix} It's been about {payload.get('days_since_last_visit', '?')} days since your last session. "
        f"No judgment at all, this happens. "
        f"We've got a good re-entry point for your earlier {humanize_text(payload.get('previous_focus', 'fitness'))} goal, and I can hold one trial session for you. "
        f"Reply YES if you want me to block it."
    )
    rationale = "Winback message keeps the tone warm, removes shame, and reduces friction to one simple commitment."
    return compact_whitespace(body), rationale


def compose_trial_followup(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any], customer: dict[str, Any]) -> tuple[str, str]:
    slot1 = first_slot_label(trigger)
    prefix = style_prefix(category.get("slug", ""), merchant, customer)
    body = (
        f"{prefix} Hope the trial felt useful. The easiest next step is to lock your next session while the rhythm is fresh. "
        f"I can hold {slot1} for you. Reply YES to confirm it."
    )
    rationale = "Trial follow-up message leverages recency and offers one concrete slot rather than reopening the whole sales conversation."
    return compact_whitespace(body), rationale


def compose_chronic_refill(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any], customer: dict[str, Any]) -> tuple[str, str]:
    payload = trigger.get("payload", {})
    molecules = ", ".join(payload.get("molecule_list", []))
    prefix = style_prefix(category.get("slug", ""), merchant, customer)
    delivery_text = "Free home delivery can be arranged to your saved address." if payload.get("delivery_address_saved") else "Pickup or delivery can be arranged."
    body = (
        f"{prefix} the next refill for {molecules} is due soon. "
        f"We can keep the same medicines ready so there is no gap. "
        f"{delivery_text} Reply CONFIRM to dispatch, or tell us if any dosage changed."
    )
    rationale = "Chronic refill message stays precise, practical, and easy to act on for a caregiver or family contact."
    return compact_whitespace(body), rationale


COMPOSERS: dict[str, Any] = {
    "research_digest": compose_research_digest,
    "regulation_change": compose_regulation_change,
    "perf_dip": compose_perf_dip,
    "renewal_due": compose_renewal_due,
    "festival_upcoming": compose_festival,
    "curious_ask_due": compose_curious_ask,
    "winback_eligible": compose_winback_eligible,
    "ipl_match_today": compose_ipl,
    "review_theme_emerged": compose_review_theme,
    "milestone_reached": compose_milestone,
    "active_planning_intent": compose_active_planning,
    "seasonal_perf_dip": compose_seasonal_perf_dip,
    "supply_alert": compose_supply_alert,
    "category_seasonal": compose_category_seasonal,
    "gbp_unverified": compose_gbp_unverified,
    "cde_opportunity": compose_cde_opportunity,
    "competitor_opened": compose_competitor_opened,
    "perf_spike": compose_perf_spike,
    "dormant_with_vera": compose_dormant,
    "recall_due": compose_recall_due,
    "wedding_package_followup": compose_wedding_followup,
    "customer_lapsed_hard": compose_customer_lapsed_hard,
    "trial_followup": compose_trial_followup,
    "chronic_refill_due": compose_chronic_refill,
}


def default_cta(trigger: dict[str, Any]) -> str:
    kind = trigger.get("kind")
    if kind in {"recall_due"}:
        return "multi_choice_slot"
    if kind in {
        "research_digest",
        "trial_followup",
        "customer_lapsed_hard",
        "renewal_due",
        "festival_upcoming",
        "ipl_match_today",
        "active_planning_intent",
        "perf_dip",
        "winback_eligible",
        "review_theme_emerged",
        "seasonal_perf_dip",
        "supply_alert",
        "category_seasonal",
        "gbp_unverified",
        "cde_opportunity",
        "perf_spike",
        "dormant_with_vera",
        "regulation_change",
        "milestone_reached",
        "competitor_opened",
    }:
        return "binary_yes_no"
    if kind in {"chronic_refill_due"}:
        return "binary_confirm_cancel"
    return "open_ended"


def validate_and_repair(body: str, category: dict[str, Any], merchant: dict[str, Any], customer: dict[str, Any] | None = None) -> str:
    repaired = compact_whitespace(body)
    repaired = re.sub(r"https?://\S+", "", repaired).strip()
    taboo_words = category.get("voice", {}).get("vocab_taboo", [])
    for taboo in taboo_words:
        repaired = re.sub(re.escape(taboo), "", repaired, flags=re.IGNORECASE)
    if customer is None and merchant.get("category_slug") == "dentists" and not repaired.startswith("Dr."):
        preferred = owner_name(merchant)
        if preferred.startswith("Dr."):
            repaired = f"{preferred}, {repaired.lstrip(', ')}"
    return compact_whitespace(repaired)


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    composer = COMPOSERS.get(trigger.get("kind"))
    if composer:
        if customer is not None:
            body, rationale = composer(category, merchant, trigger, customer)
        else:
            body, rationale = composer(category, merchant, trigger)
    else:
        send_as = "merchant_on_behalf" if customer else "vera"
        body = (
            f"{style_prefix(category.get('slug', ''), merchant, customer)} "
            f"{title_from_kind(trigger.get('kind', 'update'))} came up just now. "
            f"I can turn it into the most useful next message for this conversation. Want me to draft it?"
        )
        rationale = "Fallback composer used because this trigger kind did not have a specialized template."

    body = maybe_polish_body(category, merchant, trigger, customer, body)
    body = validate_and_repair(body, category, merchant, customer)
    send_as = "merchant_on_behalf" if customer else "vera"
    return {
        "body": body,
        "cta": default_cta(trigger),
        "send_as": send_as,
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": safe_rationale(rationale),
    }


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str]
    trigger_id: str
    send_as: str
    created_at: datetime
    auto_reply_count: int = 0
    ended: bool = False
    wait_until: Optional[datetime] = None
    turns: list[dict[str, Any]] = field(default_factory=list)
    sent_bodies: list[str] = field(default_factory=list)


class Store:
    def __init__(self) -> None:
        self.contexts: dict[tuple[str, str], dict[str, Any]] = {}
        self.conversations: dict[str, ConversationState] = {}
        self.sent_suppressions: set[str] = set()
        self.target_paused_until: dict[str, datetime] = {}
        self.target_auto_reply_counts: dict[str, int] = {}

    def count_contexts(self) -> dict[str, int]:
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        for scope, _ in self.contexts:
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def get_payload(self, scope: str, context_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not context_id:
            return None
        stored = self.contexts.get((scope, context_id))
        return stored["payload"] if stored else None

    def upsert_context(self, scope: str, context_id: str, version: int, payload: dict[str, Any]) -> tuple[bool, Optional[int]]:
        key = (scope, context_id)
        current = self.contexts.get(key)
        if current and current["version"] > version:
            return False, current["version"]
        self.contexts[key] = {"version": version, "payload": payload}
        return True, None


store = Store()
app = FastAPI(title="magicpin-challenge-bot")


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


def trigger_priority(trigger: dict[str, Any]) -> int:
    score = int(trigger.get("urgency", 0))
    if trigger.get("scope") == "customer":
        score += 2
    if trigger.get("kind") in {"active_planning_intent", "supply_alert", "recall_due", "chronic_refill_due"}:
        score += 3
    if trigger.get("kind") in {"dormant_with_vera", "curious_ask_due"}:
        score += 1
    return score


def target_key(merchant_id: str, customer_id: Optional[str]) -> str:
    return f"{merchant_id}:{customer_id or 'merchant'}"


def is_target_paused(merchant_id: str, customer_id: Optional[str], at_time: datetime) -> bool:
    paused = store.target_paused_until.get(target_key(merchant_id, customer_id))
    return bool(paused and paused > at_time)


def compose_action_for_trigger(trigger_id: str) -> Optional[dict[str, Any]]:
    trigger = store.get_payload("trigger", trigger_id)
    if not trigger:
        return None
    merchant = store.get_payload("merchant", trigger.get("merchant_id"))
    if not merchant:
        return None
    category = store.get_payload("category", merchant.get("category_slug"))
    if not category:
        return None
    customer = None
    if trigger.get("scope") == "customer":
        customer = store.get_payload("customer", trigger.get("customer_id"))
        if not customer:
            return None

    result = compose(category, merchant, trigger, customer)
    action = {
        "conversation_id": make_conversation_id(trigger, merchant, customer),
        "merchant_id": trigger.get("merchant_id"),
        "customer_id": trigger.get("customer_id"),
        "send_as": result["send_as"],
        "trigger_id": trigger.get("id"),
        "template_name": template_name_for_kind(trigger.get("kind", "generic"), trigger.get("scope", "merchant")),
        "template_params": split_template_params(result["body"]),
        "body": result["body"],
        "cta": result["cta"],
        "suppression_key": result["suppression_key"],
        "rationale": result["rationale"],
    }
    return action


def infer_trigger_for_reply(merchant_id: Optional[str], customer_id: Optional[str]) -> Optional[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    now = now_utc()
    for (scope, _), stored in store.contexts.items():
        if scope != "trigger":
            continue
        trigger = stored["payload"]
        if merchant_id and trigger.get("merchant_id") != merchant_id:
            continue
        if customer_id is not None and trigger.get("customer_id") != customer_id:
            continue
        expires_at = parse_dt(trigger.get("expires_at"))
        if expires_at and expires_at <= now:
            continue
        candidates.append(trigger)
    if not candidates:
        return None
    candidates.sort(key=trigger_priority, reverse=True)
    return candidates[0]


def ensure_conversation_state(
    conversation_id: str,
    merchant_id: Optional[str],
    customer_id: Optional[str],
) -> ConversationState:
    existing = store.conversations.get(conversation_id)
    if existing:
        return existing

    trigger = infer_trigger_for_reply(merchant_id, customer_id)
    state = ConversationState(
        conversation_id=conversation_id,
        merchant_id=merchant_id or (trigger.get("merchant_id") if trigger else "unknown_merchant"),
        customer_id=customer_id or (trigger.get("customer_id") if trigger else None),
        trigger_id=trigger.get("id") if trigger else "",
        send_as="merchant_on_behalf" if (customer_id or (trigger and trigger.get("scope") == "customer")) else "vera",
        created_at=now_utc(),
    )
    store.conversations[conversation_id] = state
    return state


def register_sent_action(action: dict[str, Any], when: datetime) -> None:
    conv_id = action["conversation_id"]
    state = store.conversations.get(conv_id)
    if not state:
        state = ConversationState(
            conversation_id=conv_id,
            merchant_id=action["merchant_id"],
            customer_id=action.get("customer_id"),
            trigger_id=action["trigger_id"],
            send_as=action["send_as"],
            created_at=when,
        )
        store.conversations[conv_id] = state
    state.turns.append({"from": "bot", "body": action["body"], "at": when.isoformat()})
    state.sent_bodies.append(action["body"])
    if action.get("suppression_key"):
        store.sent_suppressions.add(action["suppression_key"])


def avoid_repetition(state: ConversationState, body: str) -> str:
    if body not in state.sent_bodies:
        return body
    if not body.endswith("?"):
        return body + " Reply when you're ready."
    return body.replace("?", " when you see this?")


def redirect_body_for_trigger(kind: str) -> str:
    if kind in {"research_digest", "regulation_change", "cde_opportunity"}:
        return "I can help with the clinic-growth and patient communication side here. Coming back to the current item, would you like the summary first or the draft?"
    if kind in {"renewal_due", "gbp_unverified"}:
        return "I can only help with your magicpin and profile workflow here. Want the next step checklist?"
    return "I can help with growth, profile, and customer messaging here. Want me to continue with the current task?"


def action_followup_text(trigger: dict[str, Any], merchant: dict[str, Any], customer: dict[str, Any] | None) -> str:
    kind = trigger.get("kind")
    if kind in {"active_planning_intent"}:
        if "corporate_bulk_thali" in trigger.get("payload", {}).get("intent_topic", ""):
            return "Great. I'm drafting the pricing ladder, delivery window, and 3-line outreach WhatsApp now. Reply CONFIRM and I'll keep the copy tight and ready to use."
        return "Great. I'm drafting the first version now with the structure, pricing, and launch copy. Reply CONFIRM and I'll send the finished draft."
    if kind in {"research_digest", "regulation_change", "cde_opportunity"}:
        return "Sending the concise summary first, then the merchant-ready draft right after. Reply CONFIRM if you want me to shape it as a patient WhatsApp too."
    if kind in {"recall_due", "trial_followup"}:
        return "Done. I've marked the slot as tentatively held. Reply CONFIRM and we'll lock it."
    if kind in {"customer_lapsed_hard"}:
        return "Nice. I'll hold the trial spot and send the class details next. Reply CONFIRM to lock it."
    if kind in {"chronic_refill_due"}:
        return "Done. I'll keep the refill ready and the delivery flow simple. Reply CONFIRM if the same medicines should be dispatched."
    if kind in {"renewal_due"}:
        return "Great. I'll keep this to the exact renewal summary and next step only. Reply CONFIRM if you want the final version."
    return "Great. I'm moving straight to the next step now so you don't have to explain it twice."


def respond(conversation_id: str, merchant_message: str, merchant_id: Optional[str] = None, customer_id: Optional[str] = None) -> dict[str, Any]:
    state = ensure_conversation_state(conversation_id, merchant_id, customer_id)
    message = compact_whitespace(merchant_message)
    lowered = message.lower()
    when = now_utc()
    reply_target = target_key(state.merchant_id, state.customer_id)
    state.turns.append({"from": "merchant", "body": message, "at": when.isoformat()})

    if is_negative(message) or is_hostile(message):
        state.ended = True
        store.target_paused_until[reply_target] = when + timedelta(days=30)
        store.target_auto_reply_counts[reply_target] = 0
        return {"action": "end", "rationale": "Merchant explicitly opted out or was hostile; suppressing follow-ups for 30 days."}

    if is_auto_reply(message):
        auto_reply_count = store.target_auto_reply_counts.get(reply_target, 0) + 1
        store.target_auto_reply_counts[reply_target] = auto_reply_count
        state.auto_reply_count = auto_reply_count
        if auto_reply_count == 1:
            body = "Looks like an auto-reply. When the owner sees this, just reply YES and I'll take it from there."
            body = avoid_repetition(state, body)
            state.sent_bodies.append(body)
            return {
                "action": "send",
                "body": body,
                "cta": "binary_yes_no",
                "rationale": "Detected likely auto-reply and tried exactly one owner-facing prompt before backing off.",
            }
        if auto_reply_count == 2:
            state.wait_until = when + timedelta(hours=24)
            return {"action": "wait", "wait_seconds": 86400, "rationale": "Same auto-reply repeated twice; waiting for a real owner response."}
        state.ended = True
        return {"action": "end", "rationale": "Auto-reply repeated three times with no engagement signal; closing conversation."}

    trigger = store.get_payload("trigger", state.trigger_id) or {}
    merchant = store.get_payload("merchant", state.merchant_id) or {}
    customer = store.get_payload("customer", state.customer_id) if state.customer_id else None
    store.target_auto_reply_counts[reply_target] = 0

    if not trigger and is_positive_intent(message):
        body = "Great. I have enough to move into action mode now. I'll draft the concrete next step instead of asking more questions. Reply CONFIRM and I'll send it."
        body = avoid_repetition(state, body)
        state.sent_bodies.append(body)
        return {"action": "send", "body": body, "cta": "binary_confirm_cancel", "rationale": "Recovered from a reply-only conversation and still switched into action mode on explicit intent."}

    if "gst" in lowered or "tax" in lowered:
        body = redirect_body_for_trigger(trigger.get("kind", ""))
        body = avoid_repetition(state, body)
        state.sent_bodies.append(body)
        return {"action": "send", "body": body, "cta": "open_ended", "rationale": "Out-of-scope query declined politely, then redirected to the live task."}

    if is_positive_intent(message):
        body = action_followup_text(trigger, merchant, customer)
        body = avoid_repetition(state, body)
        state.sent_bodies.append(body)
        return {"action": "send", "body": body, "cta": "binary_confirm_cancel", "rationale": "Explicit intent detected, so the bot switched into action mode immediately."}

    if "price" in lowered or "cost" in lowered or "how much" in lowered:
        offer = best_offer_title(store.get_payload("category", merchant.get("category_slug")) or {}, merchant)
        body = f"Current best-fit offer from your side is {offer or 'the active offer already in your profile'}. Want me to shape the message around that exact price point?"
        body = avoid_repetition(state, body)
        state.sent_bodies.append(body)
        return {"action": "send", "body": body, "cta": "binary_yes_no", "rationale": "Merchant asked for price clarity, so the reply anchored on the current offer rather than staying abstract."}

    if "when" in lowered or "slot" in lowered or "time" in lowered:
        slot1 = first_slot_label(trigger)
        slot2 = second_slot_label(trigger)
        if slot1:
            body = f"Best available options right now are {slot1}" + (f" or {slot2}" if slot2 else "") + ". Reply with the one you want and I'll treat that as the next step."
        else:
            body = "I can line up the next timing once you confirm you want to proceed."
        body = avoid_repetition(state, body)
        state.sent_bodies.append(body)
        return {"action": "send", "body": body, "cta": "open_ended", "rationale": "Merchant asked for timing, so the reply moved to the scheduling detail directly."}

    body = "Got it. I can keep this simple and send the clean next step only. Reply YES if you want me to proceed with that."
    body = avoid_repetition(state, body)
    state.sent_bodies.append(body)
    return {"action": "send", "body": body, "cta": "binary_yes_no", "rationale": "Fallback reply keeps the thread alive with one low-friction next step."}


@app.get("/v1/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": store.count_contexts(),
    }


@app.get("/v1/metadata")
async def metadata() -> dict[str, Any]:
    return {
        "team_name": os.getenv("TEAM_NAME", "magicpin-challenge-bot"),
        "team_members": [member.strip() for member in os.getenv("TEAM_MEMBERS", "Candidate").split(",") if member.strip()],
        "model": os.getenv("MODEL_NAME", f"hybrid-rule-plus-{OLLAMA_MODEL}" if LLM_POLISH_ENABLED else "deterministic-rule-composer"),
        "approach": "rule-first composer with trigger routing, replay-safe reply handling, and optional ollama polishing on selected merchant-facing triggers",
        "contact_email": os.getenv("CONTACT_EMAIL", "candidate@example.com"),
        "version": APP_VERSION,
        "submitted_at": SUBMITTED_AT,
    }


@app.post("/v1/context")
async def push_context(body: ContextBody) -> JSONResponse:
    if body.scope not in VALID_SCOPES:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope", "details": body.scope})
    accepted, current_version = store.upsert_context(body.scope, body.context_id, body.version, body.payload)
    if not accepted:
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": current_version},
        )
    return JSONResponse(
        status_code=200,
        content={
            "accepted": True,
            "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": now_utc().isoformat(),
        },
    )


@app.post("/v1/tick")
async def tick(body: TickBody) -> dict[str, Any]:
    tick_start = time.time()
    now = parse_dt(body.now) or now_utc()
    ranked_triggers = []
    for trigger_id in body.available_triggers:
        trigger = store.get_payload("trigger", trigger_id)
        if not trigger:
            continue
        if trigger.get("suppression_key") and trigger["suppression_key"] in store.sent_suppressions:
            continue
        expires_at = parse_dt(trigger.get("expires_at"))
        if expires_at and expires_at <= now:
            continue
        if is_target_paused(trigger.get("merchant_id"), trigger.get("customer_id"), now):
            continue
        ranked_triggers.append(trigger)

    ranked_triggers.sort(key=trigger_priority, reverse=True)
    actions = []
    used_targets = set()
    for trigger in ranked_triggers:
        if time.time() - tick_start > 8:
            break
        key = target_key(trigger.get("merchant_id"), trigger.get("customer_id"))
        if key in used_targets:
            continue
        action = compose_action_for_trigger(trigger.get("id"))
        if not action:
            continue
        used_targets.add(key)
        register_sent_action(action, now)
        actions.append(action)
        if len(actions) >= 20:
            break
    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyBody) -> dict[str, Any]:
    return respond(body.conversation_id, body.message, body.merchant_id, body.customer_id)


@app.post("/v1/teardown")
async def teardown() -> dict[str, Any]:
    store.contexts.clear()
    store.conversations.clear()
    store.sent_suppressions.clear()
    store.target_paused_until.clear()
    store.target_auto_reply_counts.clear()
    return {"status": "cleared"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
