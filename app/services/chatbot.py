"""
Chatbot Service - AI-powered travel & diving sales chatbot
Fetches data from database - Supports OpenAI, DeepSeek, and OpenRouter
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.coupon import Coupon
from app.models.course import Course
from app.models.dive_center import DiveCenter
from app.models.package import Package
from app.models.trip import Trip

# =============================================================================
# In-Memory Data Storage
# =============================================================================

# Session storage: {session_id: [{"role": "user/assistant", "content": "message", "timestamp": datetime}]}
sessions: Dict[str, List[Dict[str, Any]]] = {}


# =============================================================================
# AI Client Setup - Multi-Provider Support
# =============================================================================


def get_ai_client() -> Optional[OpenAI]:
    """Initialize AI client based on configured provider."""
    if not settings.AI_API_KEY:
        return None

    provider = settings.AI_PROVIDER.lower()

    if provider == "openai":
        # OpenAI - default base URL
        return OpenAI(api_key=settings.AI_API_KEY)

    elif provider == "deepseek":
        # DeepSeek
        base_url = settings.AI_BASE_URL or "https://api.deepseek.com"
        return OpenAI(api_key=settings.AI_API_KEY, base_url=base_url)

    elif provider == "openrouter":
        # OpenRouter
        base_url = settings.AI_BASE_URL or "https://openrouter.ai/api/v1"
        return OpenAI(api_key=settings.AI_API_KEY, base_url=base_url)

    else:
        # Default to OpenAI
        return OpenAI(api_key=settings.AI_API_KEY)


def get_default_model() -> str:
    """Get default model based on provider."""
    provider = settings.AI_PROVIDER.lower()

    if settings.AI_MODEL and settings.AI_MODEL != "gpt-3.5-turbo":
        # User specified a custom model
        return settings.AI_MODEL

    if provider == "openai":
        return "gpt-3.5-turbo"
    elif provider == "deepseek":
        return "deepseek-chat"
    elif provider == "openrouter":
        return "openai/gpt-3.5-turbo"
    else:
        return "gpt-3.5-turbo"


# Lazy client initialization
_client: Optional[OpenAI] = None


def get_client() -> Optional[OpenAI]:
    global _client
    if _client is None:
        _client = get_ai_client()
    return _client


# =============================================================================
# Database Functions
# =============================================================================


def get_db() -> Session:
    """Get database session."""
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def get_all_trips(db: Session, limit: int = 20) -> List[Dict]:
    """Fetch all trips from database."""
    trips = db.query(Trip).limit(limit).all()
    return [
        {
            "id": trip.id,
            "title": trip.name,
            "price": trip.adult_price,
            "child_price": trip.child_price,
            "description": trip.description or "",
            "child_allowed": trip.child_allowed,
            "max_people": trip.maxim_person,
            "has_discount": trip.has_discount,
            "discount_percentage": trip.discount_percentage,
            "duration": trip.duration,
            "duration_unit": trip.duration_unit,
            "package_id": trip.package_id,
        }
        for trip in trips
    ]


def get_all_courses(db: Session, limit: int = 20) -> List[Dict]:
    """Fetch all courses from database."""
    courses = db.query(Course).limit(limit).all()
    return [
        {
            "id": course.id,
            "title": course.name,
            "price": course.price if course.price_available else None,
            "description": course.description or "",
            "level": course.course_level,
            "duration": course.course_duration,
            "duration_unit": course.course_duration_unit,
            "course_type": course.course_type,
            "provider": course.provider,
            "has_certificate": course.has_certificate,
            "certificate_type": course.certificate_type,
            "has_online_content": course.has_online_content,
            "has_discount": course.has_discount,
            "discount_percentage": course.discount_percentage,
        }
        for course in courses
    ]


def get_all_packages(db: Session, limit: int = 20) -> List[Dict]:
    """Fetch all packages from database."""
    packages = db.query(Package).limit(limit).all()
    return [
        {
            "id": package.id,
            "title": package.name,
            "description": package.description or "",
            "trip_count": len(package.trips) if package.trips else 0,
        }
        for package in packages
    ]


def get_active_coupons(db: Session) -> List[Dict]:
    """Fetch all active coupons from database."""
    from datetime import datetime

    coupons = db.query(Coupon).filter(Coupon.is_active == True).all()

    # Filter for valid coupons (not expired, has remaining uses)
    valid_coupons = []
    for coupon in coupons:
        if coupon.can_used:  # Uses the property from the model
            valid_coupons.append(coupon)

    return [
        {
            "id": coupon.id,
            "code": coupon.code,
            "discount_percent": coupon.discount_percentage,
            "activity": coupon.activity,
            "description": f"{coupon.discount_percentage}% off {coupon.activity}",
            "remaining": coupon.remaining,
            "expire_date": coupon.expire_date,
        }
        for coupon in valid_coupons
    ]


def get_dive_center_info(db: Session) -> Optional[Dict]:
    """Fetch dive center info from database."""
    dive_center = db.query(DiveCenter).first()
    if not dive_center:
        return None

    return {
        "name": dive_center.name,
        "description": dive_center.description,
        "phone": dive_center.phone,
        "email": dive_center.email,
        "location": dive_center.location,
        "hotel_name": dive_center.hotel_name,
        "coordinates": dive_center.coordinates,
        "working_hours": dive_center.working_hours,
    }


def get_trip_by_id_from_db(db: Session, trip_id: int) -> Optional[Dict]:
    """Get trip by ID from database."""
    trip = db.query(Trip).filter(Trip.id == trip_id).first()
    if not trip:
        return None
    return {
        "id": trip.id,
        "title": trip.name,
        "price": trip.adult_price,
        "description": trip.description or "",
    }


def get_course_by_id_from_db(db: Session, course_id: int) -> Optional[Dict]:
    """Get course by ID from database."""
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        return None
    return {
        "id": course.id,
        "title": course.name,
        "price": course.price if course.price_available else None,
        "description": course.description or "",
    }


def validate_promo_code_from_db(db: Session, code: str) -> Optional[Dict]:
    """Validate a promo code from database."""
    code = code.upper().strip()
    coupon = (
        db.query(Coupon).filter(Coupon.code == code, Coupon.is_active == True).first()
    )

    if coupon and coupon.can_used:
        return {
            "id": coupon.id,
            "code": coupon.code,
            "discount_percent": coupon.discount_percentage,
            "activity": coupon.activity,
            "description": f"{coupon.discount_percentage}% off {coupon.activity}",
            "remaining": coupon.remaining,
        }
    return None


# =============================================================================
# Session Management
# =============================================================================


def get_or_create_session(session_id: str) -> List[Dict[str, Any]]:
    """Get existing session or create new one."""
    if session_id not in sessions:
        sessions[session_id] = []
    return sessions[session_id]


def add_message_to_session(session_id: str, role: str, content: str):
    """Add a message to the session history."""
    session = get_or_create_session(session_id)
    session.append({"role": role, "content": content, "timestamp": datetime.now()})
    # Keep only last 50 messages to prevent memory issues
    if len(session) > 50:
        sessions[session_id] = session[-50:]


def get_conversation_history(session_id: str) -> List[Dict[str, Any]]:
    """Get conversation history for a session."""
    return sessions.get(session_id, [])


def clear_session(session_id: str):
    """Clear a session from memory."""
    if session_id in sessions:
        del sessions[session_id]


# =============================================================================
# Booking Link Generation
# =============================================================================


def generate_trip_link(trip_id: int) -> str:
    """Generate booking link for a trip."""
    return f"{settings.FRONTEND_URL}/trips/{trip_id}"


def generate_course_link(course_id: int) -> str:
    """Generate booking link for a course."""
    return f"{settings.FRONTEND_URL}/courses/{course_id}"


def generate_package_link(package_id: int) -> str:
    """Generate booking link for a package."""
    return f"{settings.FRONTEND_URL}/packages/{package_id}"


# =============================================================================
# AI System Prompt
# =============================================================================

SYSTEM_PROMPT = """You are a professional, friendly, and persuasive sales representative for {dive_center_name}, a premier diving and travel company in {location}. Your goal is to help customers discover and book amazing diving trips, courses, and travel packages.

## ABOUT US:
{dive_center_info}

## YOUR ROLE:
- Be enthusiastic, helpful, and professional
- Act like an expert sales consultant who understands customer needs
- Use persuasive but honest language
- Always guide customers toward making a booking

## AVAILABLE PRODUCTS:

### DIVING TRIPS:
{trips_info}

### DIVING COURSES:
{courses_info}

### TRAVEL PACKAGES:
{packages_info}

### ACTIVE PROMO CODES:
{promo_codes_info}

## RULES:
1. **Detect Intent**: Identify if customer wants trips, courses, packages, or discounts
2. **Ask Questions**: Gather information about their experience level, preferences, dates, group size
3. **Recommend**: Suggest options based on their needs (mention availability, price, what's included)
4. **Upsell/Cross-sell**: Suggest related activities (e.g., if booking diving, suggest a course)
5. **Promo Codes**: Mention and validate promo codes when relevant
6. **Booking Links**: Always provide specific booking links when recommending products
7. **Persuasive Language**: Use phrases like "Limited spots available", "This is our most popular option", "Would you like to book now?"
8. **Be Concise**: Keep responses friendly but focused on helping them book
9. **Dive Center Info**: Use the dive center contact information when customers ask about location, phone, or email

## BOOKING LINK FORMAT:
- Trips: {frontend_url}/trips/{{id}}
- Courses: {frontend_url}/courses/{{id}}
- Packages: {frontend_url}/packages/{{id}}

## RESPONSE STYLE:
- Warm and welcoming
- Professional but conversational
- Solution-focused
- Always end with a call-to-action or question to continue the conversation
"""


def build_system_prompt() -> str:
    """Build the system prompt with current data from database."""
    db = get_db()
    try:
        # Fetch all data
        trips = get_all_trips(db)
        courses = get_all_courses(db)
        packages = get_all_packages(db)
        promo_codes = get_active_coupons(db)
        dive_center = get_dive_center_info(db)

        # Build dive center info string
        if dive_center:
            dive_center_name = dive_center["name"]
            location = dive_center["location"]
            dive_center_info = f"""
Name: {dive_center["name"]}
Description: {dive_center["description"] or 'N/A'}
Phone: {dive_center["phone"]}
Email: {dive_center["email"]}
Location: {dive_center["location"]}
Hotel: {dive_center["hotel_name"] or 'N/A'}
"""
        else:
            dive_center_name = "topdivers"
            location = "Hurghada, Egypt"
            dive_center_info = "Premier diving center in Hurghada, Egypt."

        # Build trips info
        trips_info = (
            "\n".join(
                [
                    f"- ID {t['id']}: {t['title']} (${t['price']}) - {t['description'][:100] if t['description'] else 'No description'} {'[Discount: ' + str(t['discount_percentage']) + '% off]' if t['has_discount'] else ''}"
                    for t in trips[:10]  # Limit to 10 trips
                ]
            )
            if trips
            else "No trips available."
        )

        # Build courses info
        courses_info = (
            "\n".join(
                [
                    f"- ID {c['id']}: {c['title']} ({'$' + str(c['price']) if c['price'] else 'Contact for price'}) - Level: {c['level']}, Duration: {c['duration']} {c['duration_unit']} - {c['description'][:80] if c['description'] else 'No description'} {'[Discount: ' + str(c['discount_percentage']) + '% off]' if c['has_discount'] else ''}"
                    for c in courses[:10]  # Limit to 10 courses
                ]
            )
            if courses
            else "No courses available."
        )

        # Build packages info
        packages_info = (
            "\n".join(
                [
                    f"- ID {p['id']}: {p['title']} - {p['description'][:100] if p['description'] else 'No description'} (Includes {p['trip_count']} trips)"
                    for p in packages[:10]  # Limit to 10 packages
                ]
            )
            if packages
            else "No packages available."
        )

        # Build promo codes info
        promo_codes_info = (
            "\n".join(
                [
                    f"- {p['code']}: {p['discount_percent']}% off {p['activity']} - {p['description']} (Remaining: {p['remaining']} uses)"
                    for p in promo_codes[:5]  # Limit to 5 promo codes
                ]
            )
            if promo_codes
            else "No active promo codes at the moment."
        )

        return SYSTEM_PROMPT.format(
            dive_center_name=dive_center_name,
            location=location,
            dive_center_info=dive_center_info,
            trips_info=trips_info,
            courses_info=courses_info,
            packages_info=packages_info,
            promo_codes_info=promo_codes_info,
            frontend_url=settings.FRONTEND_URL,
        )
    finally:
        db.close()


# =============================================================================
# AI Integration - Multi-Provider
# =============================================================================


async def generate_ai_response(session_id: str, user_message: str) -> str:
    """Generate AI response using configured provider."""
    ai_client = get_ai_client()

    if not ai_client or not settings.AI_API_KEY:
        return "I apologize, but our AI service is currently unavailable. Please contact our sales team directly."

    try:
        # Get conversation history
        history = get_conversation_history(session_id)

        # Build messages for AI
        messages = [{"role": "system", "content": build_system_prompt()}]

        # Add conversation history (last 10 messages for context)
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Get the appropriate model
        model = get_default_model()

        # Call AI API based on provider
        provider = settings.AI_PROVIDER.lower()

        if provider in ["openai", "deepseek", "openrouter"]:
            # All three providers use OpenAI-compatible API
            response = ai_client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=500,
                temperature=0.7,
                extra_body={"thinking": {"type": "disabled"}},
            )
            ai_reply = response.choices[0].message.content
        else:
            return "I apologize, but the AI provider is not configured correctly."

        # Store the conversation
        add_message_to_session(session_id, "user", user_message)
        add_message_to_session(session_id, "assistant", ai_reply)

        return ai_reply

    except Exception as e:
        return f"I apologize, but I'm having trouble processing your request right now. Please try again or contact our team directly."


async def generate_final_response(session_id: str) -> str:
    """Generate final persuasive response to encourage booking."""
    ai_client = get_ai_client()

    if not ai_client or not settings.AI_API_KEY:
        return "Please contact our sales team to complete your booking."

    db = get_db()
    try:
        history = get_conversation_history(session_id)

        if not history:
            return "It looks like we haven't chatted yet! How can I help you today?"

        # Build context from conversation
        conversation_summary = "\n".join(
            [
                f"{'Customer' if msg['role'] == 'user' else 'Assistant'}: {msg['content'][:100]}..."
                for msg in history[-10:]
            ]
        )

        # Fetch current data
        trips = get_all_trips(db)
        courses = get_all_courses(db)
        packages = get_all_packages(db)
        promo_codes = get_active_coupons(db)

        final_prompt = f"""Based on this conversation history, provide a final persuasive response that:
1. Summarizes what the customer is looking for
2. Recommends the BEST matching product from our inventory
3. Includes a specific booking link
4. Mentions any relevant promo codes
5. Creates urgency (limited spots, popular item, etc.)
6. Has a strong call-to-action

Conversation History:
{conversation_summary}

Available Trips: {[f"ID {t['id']}: {t['title']} (${t['price']})" for t in trips[:5]]}
Available Courses: {[f"ID {c['id']}: {c['title']} ({'$' + str(c['price']) if c['price'] else 'Contact for price'})" for c in courses[:5]]}
Available Packages: {[f"ID {p['id']}: {p['title']}" for p in packages[:5]]}

Active Promo Codes: {[p['code'] for p in promo_codes]}

Respond in a friendly, persuasive sales tone. Be specific about which product you're recommending and include the booking link."""

        # Get the appropriate model
        model = get_default_model()
        provider = settings.AI_PROVIDER.lower()

        if provider in ["openai", "deepseek", "openrouter"]:
            response = ai_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a persuasive sales closer. Your goal is to convert the conversation into a booking.",
                    },
                    {"role": "user", "content": final_prompt},
                ],
                max_tokens=400,
                temperature=0.8,
                extra_body={"thinking": {"type": "disabled"}},
            )
            return response.choices[0].message.content
        else:
            return "I'd love to help you complete your booking! Please visit our website or contact us directly."

    except Exception as e:
        return "I'd love to help you complete your booking! Please visit our website or contact us directly."
    finally:
        db.close()


# =============================================================================
# Intent Detection (Simple keyword-based)
# =============================================================================


def detect_intent(message: str) -> Dict[str, Any]:
    """Detect user intent from message."""
    message_lower = message.lower()

    intents = {
        "diving": any(
            word in message_lower
            for word in ["diving", "dive", "scuba", "underwater", "reef", "snorkel"]
        ),
        "trip": any(
            word in message_lower
            for word in ["trip", "tour", "visit", "excursion", "safari"]
        ),
        "course": any(
            word in message_lower
            for word in [
                "course",
                "learn",
                "certification",
                "padi",
                "class",
                "training",
                "lesson",
            ]
        ),
        "package": any(
            word in message_lower
            for word in ["package", "deal", "bundle", "all-inclusive", "vacation"]
        ),
        "discount": any(
            word in message_lower
            for word in [
                "discount",
                "promo",
                "code",
                "coupon",
                "deal",
                "offer",
                "sale",
                "%",
                "percent",
                "off",
            ]
        ),
        "price": any(
            word in message_lower
            for word in [
                "price",
                "cost",
                "how much",
                "cheap",
                "expensive",
                "$",
                "dollar",
            ]
        ),
        "booking": any(
            word in message_lower
            for word in ["book", "reserve", "schedule", "appointment"]
        ),
        "contact": any(
            word in message_lower
            for word in [
                "phone",
                "email",
                "call",
                "contact",
                "reach",
                "location",
                "address",
                "where",
            ]
        ),
    }

    return intents


# =============================================================================
# Promo Code Validation (Public API)
# =============================================================================


def validate_promo_code(code: str) -> Optional[Dict]:
    """Validate a promo code."""
    db = get_db()
    try:
        return validate_promo_code_from_db(db, code)
    finally:
        db.close()


def get_all_active_promo_codes() -> List[Dict]:
    """Get all active promo codes."""
    db = get_db()
    try:
        return get_active_coupons(db)
    finally:
        db.close()
