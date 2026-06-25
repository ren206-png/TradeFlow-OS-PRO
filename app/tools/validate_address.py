async def validate_service_area(tool_input: dict, context: dict) -> dict:
    """Check if a postal/ZIP code falls within the contractor's configured service areas."""
    contractor = context["contractor"]
    postal_zip: str = tool_input.get("postal_zip", "").strip().upper()
    city: str = tool_input.get("city", "").strip().lower()

    if not postal_zip:
        return {"status": "unknown", "message": "No postal/ZIP code provided.", "success": True}

    service_areas: list[str] = [s.strip().upper() for s in (contractor.service_areas or [])]

    # Exact match
    if postal_zip in service_areas:
        return {"status": "inside", "message": f"{postal_zip} is within our service area.", "success": True}

    # Canadian FSA match (first 3 characters)
    fsa = postal_zip[:3]
    if any(area[:3] == fsa for area in service_areas):
        return {"status": "inside", "message": f"{postal_zip} is within our service area.", "success": True}

    # City name match (case-insensitive)
    if city and any(area.lower() == city for area in [s.lower() for s in (contractor.service_areas or [])]):
        return {"status": "inside", "message": f"{city.title()} is within our service area.", "success": True}

    return {
        "status": "outside",
        "message": f"{postal_zip} is outside our current service coverage area.",
        "success": True,
    }
