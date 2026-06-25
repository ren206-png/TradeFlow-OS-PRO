TRADEFLOW_TOOLS = [
    {
        "name": "check_availability",
        "description": (
            "Check the contractor's calendar for available appointment slots. "
            "Call this when you need to offer the caller appointment times."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_preference": {
                    "type": "string",
                    "description": "Caller's preferred date or 'earliest' for emergency",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["emergency", "same_day", "next_day", "flexible"],
                },
                "trade": {
                    "type": "string",
                    "description": "Trade type needed",
                },
            },
            "required": ["urgency", "trade"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Book a confirmed appointment slot on the contractor's calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slot_id": {
                    "type": "string",
                    "description": "Slot ID returned by check_availability",
                },
                "caller_name": {"type": "string"},
                "phone": {"type": "string"},
                "service_address": {"type": "string"},
                "trade": {"type": "string"},
                "problem_summary": {"type": "string"},
                "property_type": {
                    "type": "string",
                    "enum": ["residential", "commercial"],
                },
            },
            "required": ["slot_id", "caller_name", "phone", "service_address", "trade"],
        },
    },
    {
        "name": "validate_service_area",
        "description": "Check if a service address falls within the contractor's service area.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "city": {"type": "string"},
                "postal_zip": {"type": "string"},
            },
            "required": ["postal_zip"],
        },
    },
    {
        "name": "send_sms",
        "description": (
            "Send an SMS to the caller. "
            "Use for booking confirmations, reminders, and missed call recovery."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_number": {"type": "string"},
                "message_type": {
                    "type": "string",
                    "enum": ["confirmation", "reminder", "enroute", "missed_call", "review_request"],
                },
                "appointment_time": {
                    "type": "string",
                    "description": "ISO datetime string, required for confirmation/reminder",
                },
                "service_address": {"type": "string"},
            },
            "required": ["to_number", "message_type"],
        },
    },
    {
        "name": "create_lead_record",
        "description": (
            "Create or update the structured CRM lead record. "
            "Call this at the end of every call or when key information is captured."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "caller_name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "service_address": {"type": "string"},
                "city": {"type": "string"},
                "province_state": {"type": "string"},
                "postal_zip": {"type": "string"},
                "property_type": {"type": "string"},
                "business_name": {"type": "string"},
                "trade": {"type": "string"},
                "service_category": {"type": "string"},
                "problem_summary": {"type": "string"},
                "emergency_level": {"type": "string"},
                "life_safety_risk": {"type": "boolean"},
                "service_area_status": {
                    "type": "string",
                    "enum": ["inside", "outside", "unknown"],
                },
                "appointment_status": {
                    "type": "string",
                    "enum": ["booked", "callback_required", "transferred", "not_booked"],
                },
                "appointment_time": {"type": "string"},
                "human_transfer_required": {"type": "boolean"},
                "transfer_reason": {"type": "string"},
                "emergency_score": {"type": "integer", "minimum": 1, "maximum": 10},
                "revenue_score": {"type": "integer", "minimum": 1, "maximum": 10},
                "close_probability": {"type": "integer", "minimum": 1, "maximum": 10},
                "priority_level": {
                    "type": "string",
                    "enum": ["Low", "Medium", "High", "Critical"],
                },
                "customer_sentiment": {"type": "string"},
                "notes": {"type": "string"},
                "call_direction": {
                    "type": "string",
                    "enum": ["inbound", "outbound", "missed_call_recovery"],
                },
            },
            "required": ["call_direction"],
        },
    },
    {
        "name": "transfer_call",
        "description": (
            "Escalate the call to a human dispatcher or manager. "
            "Use when caller demands human, is hostile, or situation requires it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "caller_requested",
                        "hostile_caller",
                        "emergency_dispatch",
                        "complex_commercial",
                        "vip_customer",
                        "no_availability",
                        "outside_service_area",
                        "legal_warranty_question",
                    ],
                },
                "urgency": {
                    "type": "string",
                    "enum": ["immediate", "soon", "when_available"],
                },
                "notes": {
                    "type": "string",
                    "description": "Context for the human taking the call",
                },
            },
            "required": ["reason", "urgency"],
        },
    },
]
