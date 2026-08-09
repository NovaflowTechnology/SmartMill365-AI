TAG_STERILIZER_MAP = {
    "SAMYSK_PSTR_240004": {
        "tag_name": "SKPG",
        "unit": "psi",
        "sterilizers": [
            {"field": "ch2", "sterilizer_name": "Sterilizer 1"},
            {"field": "ch3", "sterilizer_name": "Sterilizer 2"},
            {"field": "ch4", "sterilizer_name": "Sterilizer 3"},
            {"field": "ch5", "sterilizer_name": "Sterilizer 4"},
        ],
    },
    "SAMYSK_PSTR_250041": {
        "tag_name": "SKSPAD",
        "unit": "bar",
        "sterilizers": [
            {"field": "ch2", "sterilizer_name": "Sterilizer 1"},
            {"field": "ch3", "sterilizer_name": "Sterilizer 2"},
            {"field": "ch4", "sterilizer_name": "Sterilizer 3"},
            {"field": "ch5", "sterilizer_name": "Sterilizer 4"},
        ],
    },
    "SAMYSK_PSTR_240014": {
        "tag_name": "SKRSB",
        "unit": "psi",
        "sterilizers": [
            {"field": "ch3", "sterilizer_name": "Sterilizer 1"},
            {"field": "ch4", "sterilizer_name": "Sterilizer 2"},
            {"field": "ch5", "sterilizer_name": "Sterilizer 3"},
            {"field": "ch6", "sterilizer_name": "Sterilizer 4"},
            {"field": "ch7", "sterilizer_name": "Sterilizer 5"},
        ],
    },
    "SAMYSK_PSTR_240006": {
        "tag_name": "SKRHL",
        "unit": "bar",
        "sterilizers": [
            {"field": "ch3", "sterilizer_name": "Sterilizer 1"},
            {"field": "ch4", "sterilizer_name": "Sterilizer 2"},
        ],
    },
    "SAMYSK_PSTR_250036": {
        "tag_name": "SKBAR",
        "unit": "psi",
        "sterilizers": [
            {"field": "ch3", "sterilizer_name": "Sterilizer 1"},
            {"field": "ch4", "sterilizer_name": "Sterilizer 2"},
            {"field": "ch5", "sterilizer_name": "Sterilizer 3"},
            {"field": "ch6", "sterilizer_name": "Sterilizer 4"},
            {"field": "ch7", "sterilizer_name": "Sterilizer 5"},
            {"field": "ch8", "sterilizer_name": "Sterilizer 6"},
        ],
    },
    "SAMYSK_PSTR_250024": {
        "tag_name": "SKMJ",
        "unit": "psi",
        "sterilizers": [
            {"field": "ch3", "sterilizer_name": "Sterilizer 1"},
            {"field": "ch4", "sterilizer_name": "Sterilizer 2"},
            {"field": "ch5", "sterilizer_name": "Sterilizer 3"},
        ],
    },
}


def get_all_tags():
    results = []

    for tag_id, item in TAG_STERILIZER_MAP.items():
        results.append(
            {
                "tag_id": tag_id,
                "tag_name": item["tag_name"],
                "unit": item.get("unit"),
                "sterilizer_count": len(item["sterilizers"]),
            }
        )

    return results


def get_sterilizers_for_tag(tag_id: str):
    item = TAG_STERILIZER_MAP.get(tag_id)
    if not item:
        return []
    return item["sterilizers"]


def get_default_unit_for_tag(tag_id: str):
    item = TAG_STERILIZER_MAP.get(tag_id)
    if not item:
        return None
    return item.get("unit")