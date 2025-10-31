def get_pack_from_bot_id(bot_id):
    mapping = {
        "avocat-001": "avocat",
        "immo-002": "immo",
        "medecin-003": "medecin"
    }
    return mapping.get(bot_id, "avocat")
